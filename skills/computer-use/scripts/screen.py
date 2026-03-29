"""Fast, token-efficient screen capture with change detection.

Uses mss for capture and Pillow to scale + JPEG-compress before sending
to Claude.  JPEG at quality 35 is ~10-20x smaller than PNG for typical
desktop screenshots, which directly reduces input-token cost.

Change detection uses a perceptual hash to avoid sending duplicate
screenshots when the screen hasn't changed (saves ~1000 tokens per skip).

On Windows, includes Session 0 isolation workaround — when running as a
service, mss captures a black frame because it sees Session 0's empty
desktop.  We detect this and fall back to GDI capture via the interactive
desktop.
"""

from __future__ import annotations

import base64
import io
import sys
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


def _is_black_frame(img: Image.Image, threshold: int = 10) -> bool:
    """Check if an image is effectively all black (service Session 0)."""
    grayscale = img.convert("L")
    # Sample pixels across the image — if mean brightness < threshold, it's black.
    pixels = list(grayscale.getdata())
    return (sum(pixels) / len(pixels)) < threshold


def _capture_win32_gdi(monitor_idx: int = 0) -> Image.Image | None:
    """Capture the interactive desktop on Windows using GDI via win32 APIs.

    This works even from Session 0 (services) by switching to the
    interactive user's desktop before capturing.
    """
    try:
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        gdi32 = ctypes.windll.gdi32

        # Open the interactive desktop ("Default" on WinSta0).
        hdesk = user32.OpenDesktopW("Default", 0, False, 0x0100)  # DESKTOP_READOBJECTS
        if not hdesk:
            # Try the input desktop instead.
            hdesk = user32.OpenInputDesktop(0, False, 0x0100)
        if not hdesk:
            return None

        # Temporarily switch this thread to the interactive desktop.
        old_desktop = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        user32.SetThreadDesktop(hdesk)

        try:
            # Get screen dimensions.
            width = user32.GetSystemMetrics(0)   # SM_CXSCREEN
            height = user32.GetSystemMetrics(1)  # SM_CYSCREEN

            # GDI screen capture.
            hdc_screen = user32.GetDC(0)
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
            gdi32.SelectObject(hdc_mem, hbmp)
            gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, 0, 0, 0x00CC0020)  # SRCCOPY

            # Read bitmap bits into a buffer.
            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", ctypes.wintypes.DWORD),
                    ("biWidth", ctypes.wintypes.LONG),
                    ("biHeight", ctypes.wintypes.LONG),
                    ("biPlanes", ctypes.wintypes.WORD),
                    ("biBitCount", ctypes.wintypes.WORD),
                    ("biCompression", ctypes.wintypes.DWORD),
                    ("biSizeImage", ctypes.wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.wintypes.LONG),
                    ("biYPelsPerMeter", ctypes.wintypes.LONG),
                    ("biClrUsed", ctypes.wintypes.DWORD),
                    ("biClrImportant", ctypes.wintypes.DWORD),
                ]

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height  # top-down
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0  # BI_RGB

            buf_size = width * height * 4
            buf = ctypes.create_string_buffer(buf_size)
            gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(bmi), 0)

            # Clean up GDI objects.
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)

            # Convert BGRA → RGB via Pillow.
            img = Image.frombytes("RGBX", (width, height), buf.raw, "raw", "BGRX")
            return img.convert("RGB")
        finally:
            # Restore original desktop.
            user32.SetThreadDesktop(old_desktop)
            user32.CloseDesktop(hdesk)

    except Exception:
        return None


def _capture_raw(monitor_idx: int) -> Image.Image:
    """Capture a raw screenshot, with Windows Session 0 fallback."""
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[monitor_idx])
        img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

    # On Windows, check if we got a black frame (Session 0 isolation).
    if sys.platform == "win32" and _is_black_frame(img):
        gdi_img = _capture_win32_gdi(monitor_idx)
        if gdi_img is not None and not _is_black_frame(gdi_img):
            return gdi_img

    return img


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

    img = _capture_raw(cfg.monitor)

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
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            pass
    with mss.mss() as sct:
        mon = sct.monitors[0]
        return mon["width"], mon["height"]
