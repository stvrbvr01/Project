"""Execute Claude computer-use tool calls by dispatching to input_control.

Maps each action to the corresponding pyautogui call, handling coordinate
scaling between the (possibly downscaled) screenshot space and native pixels.
"""

from __future__ import annotations

import input_control
import screen


def _scale_coords(
    x: int,
    y: int,
    screenshot_w: int,
    screenshot_h: int,
) -> tuple[int, int]:
    native_w, native_h = screen.get_screen_size()
    return int(x * native_w / screenshot_w), int(y * native_h / screenshot_h)


def execute_action(
    action: str,
    params: dict,
    screenshot_w: int,
    screenshot_h: int,
) -> str | None:
    """Run a single computer-use action. Returns an optional text result."""

    def sc(x: int, y: int) -> tuple[int, int]:
        return _scale_coords(x, y, screenshot_w, screenshot_h)

    match action:
        case "screenshot":
            return None

        case "mouse_move":
            cx, cy = params["coordinate"]
            input_control.move(*sc(cx, cy))

        case "left_click":
            cx, cy = params["coordinate"]
            input_control.left_click(*sc(cx, cy))

        case "right_click":
            cx, cy = params["coordinate"]
            input_control.right_click(*sc(cx, cy))

        case "middle_click":
            cx, cy = params["coordinate"]
            input_control.middle_click(*sc(cx, cy))

        case "double_click":
            cx, cy = params["coordinate"]
            input_control.double_click(*sc(cx, cy))

        case "triple_click":
            cx, cy = params["coordinate"]
            input_control.triple_click(*sc(cx, cy))

        case "left_click_drag":
            sx, sy = params["start_coordinate"]
            ex, ey = params["coordinate"]
            nsx, nsy = sc(sx, sy)
            nex, ney = sc(ex, ey)
            input_control.left_click_drag(nsx, nsy, nex, ney)

        case "scroll":
            cx, cy = params["coordinate"]
            nx, ny = sc(cx, cy)
            input_control.scroll(nx, ny, params.get("delta_x", 0), params.get("delta_y", 0))

        case "type":
            input_control.type_text(params["text"])

        case "key":
            input_control.press_key(params["key"])

        case "cursor_position":
            px, py = input_control.get_cursor_position()
            native_w, native_h = screen.get_screen_size()
            spx = int(px * screenshot_w / native_w)
            spy = int(py * screenshot_h / native_h)
            return f"cursor_position: ({spx}, {spy})"

        case "wait":
            input_control.wait(params.get("duration", 2))

        case _:
            return f"Unknown action: {action}"

    return None
