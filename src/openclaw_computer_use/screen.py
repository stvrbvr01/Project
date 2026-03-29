"""Fast, token-efficient screen capture.

Uses mss for capture and Pillow to scale + JPEG-compress before sending
to Claude.  JPEG at quality 60 is ~5-10x smaller than PNG for typical
desktop screenshots, which directly reduces input-token cost.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import mss
from PIL import Image


@dataclass(frozen=True)
class ScreenConfig:
    """Knobs that control screenshot size → token cost."""

    # Target resolution for the screenshot sent to Claude.
    # Claude computer-use works well at 1280x800; lower = fewer tokens.
    max_width: int = 1280
    max_height: int = 800

    # JPEG quality (1-95). Lower = smaller payload = fewer tokens.
    jpeg_quality: int = 60

    # Which monitor to capture (0 = all monitors combined, 1 = primary, …)
    monitor: int = 0


def capture_screenshot(cfg: ScreenConfig | None = None) -> tuple[str, int, int]:
    """Capture the screen and return (base64_jpeg, width, height).

    The image is scaled to fit within *cfg.max_width* × *cfg.max_height*
    while preserving aspect ratio, then JPEG-compressed.
    """
    cfg = cfg or ScreenConfig()

    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[cfg.monitor])
        img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

    # Scale down, preserving aspect ratio.
    img.thumbnail((cfg.max_width, cfg.max_height), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=cfg.jpeg_quality, optimize=True)
    b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return b64, img.width, img.height


def get_screen_size() -> tuple[int, int]:
    """Return the *native* screen resolution (before any scaling)."""
    with mss.mss() as sct:
        mon = sct.monitors[0]  # virtual screen spanning all monitors
        return mon["width"], mon["height"]
