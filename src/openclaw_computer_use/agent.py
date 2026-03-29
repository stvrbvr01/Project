"""Core agent loop: screenshot → Claude → execute actions → repeat.

Token-efficiency strategy
-------------------------
1. Screenshots are JPEG-compressed at low quality and scaled to 1280×800.
2. We only send a new screenshot after actions that change the screen
   (clicks, typing, scrolling).  Pure ``cursor_position`` or ``wait``
   actions skip the screenshot to save tokens.
3. The conversation history is kept minimal — we prune older screenshot
   images after they are superseded by a new one, keeping only the
   text/tool_use blocks so Claude retains context without re-ingesting
   stale images.
4. A configurable ``max_turns`` prevents runaway loops.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

import anthropic

from openclaw_computer_use.screen import ScreenConfig, capture_screenshot
from openclaw_computer_use.tools import execute_action

# Actions that visibly change the screen — we take a fresh screenshot after.
_VISUAL_ACTIONS = frozenset(
    {
        "screenshot",
        "left_click",
        "right_click",
        "middle_click",
        "double_click",
        "triple_click",
        "left_click_drag",
        "scroll",
        "type",
        "key",
        "mouse_move",
    }
)

# The computer-use tool name that Claude uses.
COMPUTER_TOOL_NAME = "computer"


@dataclass
class AgentConfig:
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    max_turns: int = 50
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    system_prompt: str = (
        "You are an AI agent that can see and interact with a computer screen. "
        "Use the computer tool to accomplish the user's task. "
        "Be efficient — take the shortest path to the goal. "
        "After completing the task, respond with a brief summary of what you did."
    )


def _make_screenshot_content(b64: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
    }


def _prune_old_images(messages: list[dict]) -> None:
    """Remove base64 image blocks from all but the last user message.

    This drastically cuts input tokens on long sessions because Claude
    doesn't need to re-process stale screenshots.
    """
    # Find the index of the last user message that has an image.
    last_img_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user" and isinstance(messages[i]["content"], list):
            if any(b.get("type") == "image" for b in messages[i]["content"]):
                last_img_idx = i
                break

    # Strip images from all earlier messages.
    for i in range(last_img_idx):
        msg = messages[i]
        if msg["role"] == "user" and isinstance(msg["content"], list):
            msg["content"] = [b for b in msg["content"] if b.get("type") != "image"]
            # If content is now empty, replace with a placeholder.
            if not msg["content"]:
                msg["content"] = [{"type": "text", "text": "[screenshot pruned]"}]


def run(task: str, cfg: AgentConfig | None = None) -> str:
    """Run the computer-use agent loop for *task*.  Returns Claude's final text."""
    cfg = cfg or AgentConfig()
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    # Initial screenshot so Claude can see the current state.
    b64, sw, sh = capture_screenshot(cfg.screen)

    messages: list[dict] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": task},
                _make_screenshot_content(b64),
            ],
        }
    ]

    # The computer-use tool definition following the 2025-01-24 spec.
    tools = [
        {
            "type": "computer_20250124",
            "name": COMPUTER_TOOL_NAME,
            "display_width_px": sw,
            "display_height_px": sh,
            "display_number": cfg.screen.monitor or 1,
        }
    ]

    final_text = ""

    for turn in range(cfg.max_turns):
        _prune_old_images(messages)

        response = client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=cfg.system_prompt,
            tools=tools,
            messages=messages,
            betas=["computer-use-2025-01-24"],
        )

        # Collect assistant content blocks.
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        # If the model stopped without tool use, we're done.
        if response.stop_reason != "tool_use":
            final_text = "".join(
                b.text for b in assistant_content if getattr(b, "text", None)
            )
            break

        # Process every tool_use block in the response.
        tool_results: list[dict] = []
        needs_screenshot = False

        for block in assistant_content:
            if block.type != "tool_use":
                continue

            action = block.input.get("action", "")
            _log(f"  → {action} {_summarize(block.input)}")

            if action == "screenshot":
                needs_screenshot = True
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [],  # screenshot attached below
                    }
                )
            else:
                result_text = execute_action(action, block.input, sw, sh)
                if action in _VISUAL_ACTIONS:
                    needs_screenshot = True
                    # Small delay to let the UI settle.
                    time.sleep(0.3)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [{"type": "text", "text": result_text or "ok"}],
                    }
                )

        # Attach a fresh screenshot to the *last* tool result if needed.
        if needs_screenshot:
            b64, sw, sh = capture_screenshot(cfg.screen)
            # Update tool dimensions in case resolution changed.
            tools[0]["display_width_px"] = sw
            tools[0]["display_height_px"] = sh
            # Append screenshot to the last tool result.
            tool_results[-1]["content"].append(_make_screenshot_content(b64))

        messages.append({"role": "user", "content": tool_results})
    else:
        final_text = "[agent reached max turns without completing]"

    return final_text


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _summarize(params: dict) -> str:
    """One-line summary of action params for logging."""
    parts = []
    if "coordinate" in params:
        parts.append(f"at {params['coordinate']}")
    if "text" in params:
        t = params["text"]
        parts.append(f"'{t[:40]}{'…' if len(t) > 40 else ''}'")
    if "key" in params:
        parts.append(params["key"])
    return " ".join(parts)
