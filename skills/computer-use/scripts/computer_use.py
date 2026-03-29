#!/usr/bin/env python3
"""Computer-use agent for OpenClaw.

Captures screenshots, sends them to Claude, and executes the mouse/keyboard
actions Claude decides on.  Designed to be as token-efficient as possible.

Token-efficiency strategies:
  1. JPEG compression at low quality + resolution scaling (fast preset: 800x600 q35)
  2. Grayscale mode (~30% smaller images)
  3. Prompt caching — system prompt, tool def, and conversation prefix are cached
  4. Old screenshot pruning — only the latest screenshot stays in history
  5. Screen change detection — skips sending duplicate screenshots
  6. Terse system prompt — minimizes output tokens
  7. Token budget — hard stop at 200K tokens by default
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Allow imports from the scripts/ directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import anthropic

from screen import ScreenConfig, capture_screenshot
from tools import execute_action

# ---------------------------------------------------------------------------
# Presets — fast is the default
# ---------------------------------------------------------------------------

PRESETS: dict[str, dict] = {
    "fast": {
        "max_width": 800,
        "max_height": 600,
        "jpeg_quality": 35,
        "grayscale": True,
        "token_budget": 200_000,
    },
    "balanced": {
        "max_width": 1024,
        "max_height": 768,
        "jpeg_quality": 50,
        "grayscale": False,
        "token_budget": 200_000,
    },
    "accurate": {
        "max_width": 1280,
        "max_height": 800,
        "jpeg_quality": 60,
        "grayscale": False,
        "token_budget": 200_000,
    },
}

# Actions that visibly change the screen.
_VISUAL_ACTIONS = frozenset({
    "screenshot", "left_click", "right_click", "middle_click",
    "double_click", "triple_click", "left_click_drag", "scroll",
    "type", "key", "mouse_move",
})

SYSTEM_PROMPT = (
    "You control a computer to complete the user's task. "
    "Be maximally efficient: use the shortest action sequence, "
    "do not explain your reasoning, do not narrate actions. "
    "When done, reply with a one-sentence summary."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_screenshot_content(b64: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
    }


def _prune_old_images(messages: list[dict]) -> None:
    """Remove base64 image blocks from all but the last user message."""
    last_img_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user" and isinstance(messages[i]["content"], list):
            if any(b.get("type") == "image" for b in messages[i]["content"]):
                last_img_idx = i
                break

    for i in range(last_img_idx):
        msg = messages[i]
        if msg["role"] == "user" and isinstance(msg["content"], list):
            msg["content"] = [b for b in msg["content"] if b.get("type") != "image"]
            if not msg["content"]:
                msg["content"] = [{"type": "text", "text": "[screenshot pruned]"}]


def _tag_cache_breakpoint(messages: list[dict]) -> None:
    """Tag the last content block of the second-to-last message for caching.

    This makes the entire conversation prefix cacheable across turns,
    so only the newest message is billed at full input-token rate.
    """
    if len(messages) < 2:
        return

    target = messages[-2]["content"]
    if isinstance(target, list) and target:
        # Add cache_control to a copy of the last block.
        last = target[-1]
        if isinstance(last, dict):
            target[-1] = {**last, "cache_control": {"type": "ephemeral"}}
    elif isinstance(target, str):
        messages[-2]["content"] = [
            {"type": "text", "text": target, "cache_control": {"type": "ephemeral"}}
        ]


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _summarize_params(params: dict) -> str:
    parts = []
    if "coordinate" in params:
        parts.append(f"at {params['coordinate']}")
    if "text" in params:
        t = params["text"]
        parts.append(f"'{t[:40]}{'…' if len(t) > 40 else ''}'")
    if "key" in params:
        parts.append(params["key"])
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run(
    task: str,
    model: str,
    screen_cfg: ScreenConfig,
    max_turns: int,
    token_budget: int,
) -> str:
    """Run the computer-use agent loop. Returns Claude's final text."""
    client = anthropic.Anthropic()

    # Initial screenshot.
    b64, sw, sh, _ = capture_screenshot(screen_cfg)

    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": task},
                _make_screenshot_content(b64),
            ],
        }
    ]

    # Tool definition with prompt-cache breakpoint.
    tools = [
        {
            "type": "computer_20250124",
            "name": "computer",
            "display_width_px": sw,
            "display_height_px": sh,
            "display_number": 1,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    # System prompt with prompt-cache breakpoint.
    system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    total_in = 0
    total_out = 0
    final_text = ""

    for turn in range(max_turns):
        _prune_old_images(messages)
        _tag_cache_breakpoint(messages)

        response = client.beta.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            tools=tools,
            messages=messages,
            betas=["computer-use-2025-01-24"],
        )

        # Track token usage.
        usage = response.usage
        total_in += usage.input_tokens
        total_out += usage.output_tokens
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0

        _log(
            f"  [turn {turn + 1}] "
            f"in={usage.input_tokens} out={usage.output_tokens} "
            f"cache_read={cache_read} cache_create={cache_create} "
            f"| cumulative in={total_in} out={total_out}"
        )

        # Budget check.
        if token_budget and (total_in + total_out) > token_budget:
            _log(f"  [budget exceeded: {total_in + total_out} > {token_budget}]")
            final_text = "[agent stopped: token budget exceeded]"
            break

        # Collect assistant content.
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        # If no tool use, we're done.
        if response.stop_reason != "tool_use":
            final_text = "".join(
                b.text for b in assistant_content if getattr(b, "text", None)
            )
            break

        # Process tool_use blocks.
        tool_results: list[dict] = []
        needs_screenshot = False

        for block in assistant_content:
            if block.type != "tool_use":
                continue

            action = block.input.get("action", "")
            _log(f"  -> {action} {_summarize_params(block.input)}")

            if action == "screenshot":
                needs_screenshot = True
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": [],
                })
            else:
                result_text = execute_action(action, block.input, sw, sh)
                if action in _VISUAL_ACTIONS:
                    needs_screenshot = True
                    time.sleep(0.3)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": [{"type": "text", "text": result_text or "ok"}],
                })

        # Attach screenshot (or "unchanged" text) to last tool result.
        if needs_screenshot:
            b64, sw, sh, changed = capture_screenshot(screen_cfg)
            tools[0]["display_width_px"] = sw
            tools[0]["display_height_px"] = sh
            if changed:
                tool_results[-1]["content"].append(_make_screenshot_content(b64))
            else:
                tool_results[-1]["content"].append(
                    {"type": "text", "text": "[screen unchanged since last screenshot]"}
                )

        messages.append({"role": "user", "content": tool_results})
    else:
        final_text = "[agent reached max turns without completing]"

    _log(f"  [done] total_in={total_in} total_out={total_out} total={total_in + total_out}")
    return final_text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenClaw computer-use skill — let Claude control the screen.",
    )
    parser.add_argument("--task", required=True, help="Task description.")
    parser.add_argument(
        "--preset", choices=PRESETS.keys(), default="balanced",
        help="Preset: fast (cheapest), balanced (default), accurate.",
    )
    parser.add_argument("--model", default="claude-sonnet-4-6-20250610")
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--token-budget", type=int, default=None,
                        help="Override preset token budget (default: 200000).")
    parser.add_argument("--resolution", default=None,
                        help="Override resolution WxH (e.g., 1024x768).")
    parser.add_argument("--quality", type=int, default=None,
                        help="Override JPEG quality 1-95.")
    parser.add_argument("--grayscale", action="store_true", default=None,
                        help="Force grayscale screenshots.")
    parser.add_argument("--no-grayscale", dest="grayscale", action="store_false",
                        help="Force color screenshots.")
    parser.add_argument("--monitor", type=int, default=0)

    args = parser.parse_args()

    # Start from preset, then apply overrides.
    preset = PRESETS[args.preset]

    max_w = preset["max_width"]
    max_h = preset["max_height"]
    if args.resolution:
        max_w, max_h = (int(x) for x in args.resolution.split("x"))

    quality = args.quality if args.quality is not None else preset["jpeg_quality"]
    grayscale = args.grayscale if args.grayscale is not None else preset["grayscale"]
    budget = args.token_budget if args.token_budget is not None else preset["token_budget"]

    screen_cfg = ScreenConfig(
        max_width=max_w,
        max_height=max_h,
        jpeg_quality=quality,
        monitor=args.monitor,
        grayscale=grayscale,
    )

    _log(f"[computer-use] task: {args.task}")
    _log(f"[computer-use] preset={args.preset} model={args.model} "
         f"res={max_w}x{max_h} q={quality} gs={grayscale} budget={budget}")

    result = run(
        task=args.task,
        model=args.model,
        screen_cfg=screen_cfg,
        max_turns=args.max_turns,
        token_budget=budget,
    )
    print(result)


if __name__ == "__main__":
    main()
