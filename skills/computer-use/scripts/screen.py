"""Fast, token-efficient screen capture with change detection.

Uses mss for capture and Pillow to scale + JPEG-compress before sending
to Claude.  JPEG at quality 35 is ~10-20x smaller than PNG for typical
desktop screenshots, which directly reduces input-token cost.

Change detection uses a perceptual hash to avoid sending duplicate
screenshots when the screen hasn't changed (saves ~1000 tokens per skip).
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import mss
from PIL import Image


@dataclass(frozen=True)
class ScreenConfig:
    """Knobs that control screenshot size and token cost."""

    max_width: int = 800
    max_height: int = 600
    jpeg_quality: int = 35
    monitor: int = 0
    grayscale: bool = True
    change_threshold: int = 5  # hamming distance tolerance for "unchanged"


# Module-level state for change detection.
_last_phash: str | None = None


def _perceptual_hash(img: Image.Image) -> str:
    """Compute a 64-bit perceptual hash for change detection."""
    small = img.resize((8, 8), Image.LANCZOS).convert("L")
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    return "".join("1" if p > avg else "0" for p in pixels)


def _hamming_distance(a: str, b: str) -> int:
    return sum(c1 != c2 for c1, c2 in zip(a, b))


def capture_screenshot(
    cfg: ScreenConfig | None = None,
) -> tuple[str, int, int, bool]:
    """Capture the screen and return (base64_jpeg, width, height, changed).

    *changed* is False when the screen looks identical to the last capture
    (based on perceptual hashing).  The caller can skip sending the image
    to save tokens.
    """
    global _last_phash
    cfg = cfg or ScreenConfig()

    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[cfg.monitor])
        img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

    # Scale down, preserving aspect ratio.
    img.thumbnail((cfg.max_width, cfg.max_height), Image.LANCZOS)

    # Optional grayscale conversion (~30% smaller JPEG).
    if cfg.grayscale:
        img = img.convert("L")

    # Change detection via perceptual hash.
    phash = _perceptual_hash(img)
    changed = True
    if _last_phash is not None and _hamming_distance(phash, _last_phash) <= cfg.change_threshold:
        changed = False
    _last_phash = phash

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=cfg.jpeg_quality, optimize=True)
    b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return b64, img.width, img.height, changed


def get_screen_size() -> tuple[int, int]:
    """Return the native screen resolution (before any scaling)."""
    with mss.mss() as sct:
        mon = sct.monitors[0]
        return mon["width"], mon["height"]
