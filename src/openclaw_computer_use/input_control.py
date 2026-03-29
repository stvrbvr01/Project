"""Mouse and keyboard control via pyautogui.

Translates Claude computer-use tool actions into real input events.
Coordinates from Claude are in the *scaled* screenshot space; callers
must map them back to native screen pixels before calling these helpers.
"""

from __future__ import annotations

import time

import pyautogui

# Disable pyautogui's built-in pause (we control timing ourselves)
# and fail-safe (moving mouse to corner raises exception).
pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True  # keep fail-safe on for safety


def move(x: int, y: int) -> None:
    pyautogui.moveTo(x, y, duration=0)


def left_click(x: int | None = None, y: int | None = None) -> None:
    if x is not None and y is not None:
        pyautogui.click(x, y, button="left")
    else:
        pyautogui.click(button="left")


def right_click(x: int | None = None, y: int | None = None) -> None:
    if x is not None and y is not None:
        pyautogui.click(x, y, button="right")
    else:
        pyautogui.click(button="right")


def middle_click(x: int | None = None, y: int | None = None) -> None:
    if x is not None and y is not None:
        pyautogui.click(x, y, button="middle")
    else:
        pyautogui.click(button="middle")


def double_click(x: int | None = None, y: int | None = None) -> None:
    if x is not None and y is not None:
        pyautogui.doubleClick(x, y)
    else:
        pyautogui.doubleClick()


def triple_click(x: int | None = None, y: int | None = None) -> None:
    if x is not None and y is not None:
        pyautogui.click(x, y, clicks=3)
    else:
        pyautogui.click(clicks=3)


def left_click_drag(start_x: int, start_y: int, end_x: int, end_y: int) -> None:
    pyautogui.moveTo(start_x, start_y)
    pyautogui.drag(end_x - start_x, end_y - start_y, duration=0.3)


def scroll(x: int, y: int, delta_x: int, delta_y: int) -> None:
    """Scroll at (x, y). delta_y positive = scroll up, negative = scroll down."""
    pyautogui.moveTo(x, y)
    if delta_y:
        pyautogui.scroll(delta_y)
    if delta_x:
        pyautogui.hscroll(delta_x)


def type_text(text: str) -> None:
    """Type text character by character (handles Unicode)."""
    pyautogui.write(text, interval=0.02)


def press_key(key: str) -> None:
    """Press a key or key combination.

    Accepts strings like "Return", "ctrl+a", "alt+Tab", etc.
    Modifier keys are mapped from Claude's naming to pyautogui's.
    """
    key_map = {
        "Return": "enter",
        "Escape": "escape",
        "BackSpace": "backspace",
        "Tab": "tab",
        "space": "space",
        "Delete": "delete",
        "Home": "home",
        "End": "end",
        "Page_Up": "pageup",
        "Page_Down": "pagedown",
        "Up": "up",
        "Down": "down",
        "Left": "left",
        "Right": "right",
        "Super_L": "win",
        "Super_R": "winright",
    }

    # Handle modifier combos like "ctrl+a" or "ctrl+shift+t"
    if "+" in key:
        parts = key.split("+")
        mapped = [key_map.get(p, p.lower()) for p in parts]
        pyautogui.hotkey(*mapped)
    else:
        mapped_key = key_map.get(key, key.lower())
        pyautogui.press(mapped_key)


def wait(seconds: float = 1.0) -> None:
    time.sleep(seconds)


def get_cursor_position() -> tuple[int, int]:
    pos = pyautogui.position()
    return pos.x, pos.y
