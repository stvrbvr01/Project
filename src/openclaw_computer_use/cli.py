"""CLI entry point for openclaw-computer-use.

Usage:
    openclaw-cu "Open Firefox and search for the weather"
    openclaw-cu --model claude-sonnet-4-20250514 --quality 40 --resolution 1024x768 "task"
"""

from __future__ import annotations

import argparse
import sys

from openclaw_computer_use.agent import AgentConfig, run
from openclaw_computer_use.screen import ScreenConfig


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="openclaw-cu",
        description="Let Claude use your computer to complete a task.",
    )
    parser.add_argument("task", help="Natural-language description of the task.")
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Anthropic model to use (default: claude-sonnet-4-20250514).",
    )
    parser.add_argument(
        "--resolution",
        default="1280x800",
        help="Max screenshot resolution WxH (default: 1280x800). Lower = cheaper.",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=60,
        help="JPEG quality 1-95 (default: 60). Lower = cheaper.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=50,
        help="Max agent loop iterations (default: 50).",
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=0,
        help="Monitor index to capture (0=all, 1=primary, …).",
    )
    parser.add_argument(
        "--system",
        default=None,
        help="Override the system prompt.",
    )

    args = parser.parse_args(argv)

    w, h = (int(x) for x in args.resolution.split("x"))

    screen_cfg = ScreenConfig(
        max_width=w,
        max_height=h,
        jpeg_quality=args.quality,
        monitor=args.monitor,
    )

    agent_cfg = AgentConfig(
        model=args.model,
        max_turns=args.max_turns,
        screen=screen_cfg,
    )
    if args.system:
        agent_cfg.system_prompt = args.system

    print(f"[openclaw-cu] task: {args.task}", file=sys.stderr)
    print(f"[openclaw-cu] model={args.model}  resolution={w}x{h}  jpeg_q={args.quality}", file=sys.stderr)

    result = run(args.task, agent_cfg)
    print(result)


if __name__ == "__main__":
    main()
