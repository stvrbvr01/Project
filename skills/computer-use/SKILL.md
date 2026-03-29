---
name: computer-use
description: Control the computer screen, mouse, and keyboard like a human. Use when you need to perform visual desktop tasks — clicking buttons, filling forms, navigating UIs without a CLI or API.
metadata: {"openclaw": {"requires": {"bins": ["python3"], "env": ["ANTHROPIC_API_KEY"]}, "os": ["linux", "darwin", "win32"], "emoji": "🖥️"}}
---

# Computer Use

This skill lets you see and interact with the computer screen using Claude's computer-use capability. It captures screenshots, sends them to Claude, and executes the mouse/keyboard actions Claude decides on — like a human sitting at the computer.

## When to Use

- When asked to perform actions on the desktop (e.g., "Open Firefox and search for the weather").
- When navigating applications that lack command-line interfaces.
- For multi-step visual workflows (e.g., "Fill out the form in the browser").
- When you need to click buttons, select menus, or interact with GUIs.

**Do NOT use** when a CLI command, API call, or file edit would accomplish the task faster.

## Setup

Install the Python dependencies (one-time):

```bash
pip install anthropic mss Pillow pyautogui
```

Ensure `ANTHROPIC_API_KEY` is set in the environment.

**Note:** This skill uses a *separate* Anthropic API connection from OpenClaw's own session. The computer-use protocol requires its own Claude conversation loop with screenshot exchange. Token usage is logged to stderr so you can monitor cost.

### Platform Notes

- **Linux**: Requires X11 or Wayland. Install `python3-xlib` if using X11.
- **macOS**: Grant "Screen Recording" permission to the terminal app in System Settings → Privacy & Security.
- **Windows**: Works out of the box. May need to run as administrator for some UI automation.

## Running

Pass the user's task to the helper script:

```bash
python3 {baseDir}/scripts/computer_use.py --task "Open System Settings and switch to Dark Mode"
```

### Available flags

| Flag | Default | Description |
|------|---------|-------------|
| `--task` | (required) | Natural-language task description |
| `--preset` | `balanced` | `fast` (cheapest), `balanced` (default), or `accurate` |
| `--token-budget` | `200000` | Max total tokens before stopping (0 = unlimited) |
| `--grayscale` | auto | Force grayscale screenshots (enabled by default in `fast` preset) |
| `--no-grayscale` | | Force color screenshots |
| `--model` | `claude-sonnet-4-6-20250610` | Anthropic model to use |
| `--max-turns` | `50` | Max agent loop iterations |

### Presets

- **fast**: 800x600, JPEG quality 35, grayscale on. Cheapest per-screenshot, but may need more turns for precise UI tasks.
- **balanced** (default): 1024x768, JPEG quality 50, color. Best cost-to-accuracy tradeoff.
- **accurate**: 1280x800, JPEG quality 60, color. Best visual fidelity.

All presets enforce a 200K token budget by default.

## Tips

- **Be specific**: Instead of "Open Chrome", say "Open Chrome and navigate to google.com".
- **The script prints a summary** to stdout when done. Stderr shows per-turn token usage.
- **Token budget**: The agent stops automatically if it exceeds the token budget. You can raise or lower it with `--token-budget`.
