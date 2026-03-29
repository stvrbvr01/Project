"""Fast, token-efficient screen capture with change detection.

Uses mss for capture and Pillow to scale + JPEG-compress before sending
to Claude.  JPEG at quality 35 is ~10-20x smaller than PNG for typical
desktop screenshots, which directly reduces input-token cost.

Change detection uses a perceptual hash to avoid sending duplicate
screenshots when the screen hasn't changed (saves ~1000 tokens per skip).

Windows Session 0 isolation
----------------------------
When OpenClaw runs as a Windows service, the process lives in Session 0
which has no visible desktop.  mss/GDI will only see a black frame.
Window stations are per-session objects — you cannot reach the interactive
desktop from Session 0 via SetThreadDesktop or OpenDesktopW.

The fix: detect the black frame, then use WTSQueryUserToken +
CreateProcessAsUser to launch a tiny capture subprocess in the
interactive user's session (Session 1+).  That subprocess runs mss
normally (it can see the real desktop) and writes the screenshot to a
temp file, which we read back.
"""

from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
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
    """Check if an image is effectively all black (Session 0 symptom)."""
    gray = img.convert("L")
    pixels = list(gray.getdata())
    return (sum(pixels) / len(pixels)) < threshold


def _get_current_session_id() -> int:
    """Get the Windows session ID of the current process."""
    import ctypes
    import ctypes.wintypes as wt
    kernel32 = ctypes.windll.kernel32
    sid = wt.DWORD()
    kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(sid))
    return sid.value


def _capture_via_interactive_session(monitor_idx: int) -> Image.Image | None:
    """Launch a capture subprocess in the interactive user session.

    Uses WTSQueryUserToken + CreateProcessAsUser to spawn a Python process
    in the logged-in user's session (Session 1+), which can see the real
    desktop.  The subprocess captures via mss and writes a JPEG to a temp
    file that we read back.

    Requires the service to run as LocalSystem (default for most services).
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32
        wtsapi32 = ctypes.windll.wtsapi32

        # Find the interactive session (the one with the physical display).
        session_id = kernel32.WTSGetActiveConsoleSessionId()
        if session_id == 0xFFFFFFFF:
            _log_capture("No active console session found")
            return None

        # Get the logged-in user's token for that session.
        user_token = wt.HANDLE()
        if not wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(user_token)):
            _log_capture(f"WTSQueryUserToken failed for session {session_id}")
            return None

        try:
            # Duplicate the token as a primary token for CreateProcessAsUser.
            dup_token = wt.HANDLE()
            # MAXIMUM_ALLOWED=0x02000000, SecurityImpersonation=2, TokenPrimary=1
            if not advapi32.DuplicateTokenEx(
                user_token, 0x02000000, None, 2, 1, ctypes.byref(dup_token)
            ):
                _log_capture("DuplicateTokenEx failed")
                return None

            try:
                # Create the user's environment block.
                userenv = ctypes.windll.userenv
                env_block = ctypes.c_void_p()
                userenv.CreateEnvironmentBlock(ctypes.byref(env_block), dup_token, False)

                try:
                    return _spawn_capture_process(
                        dup_token, env_block, monitor_idx, kernel32, advapi32
                    )
                finally:
                    if env_block:
                        userenv.DestroyEnvironmentBlock(env_block)
            finally:
                kernel32.CloseHandle(dup_token)
        finally:
            kernel32.CloseHandle(user_token)

    except Exception as e:
        _log_capture(f"Interactive session capture failed: {e}")
        return None


def _spawn_capture_process(
    token, env_block, monitor_idx: int, kernel32, advapi32
) -> Image.Image | None:
    """Create a process in the user's session that captures the screen."""
    import ctypes
    import ctypes.wintypes as wt

    # STARTUPINFOW structure.
    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR),
            ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
            ("dwX", wt.DWORD), ("dwY", wt.DWORD),
            ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
            ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD),
            ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD),
            ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
            ("hStdError", wt.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
            ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD),
        ]

    # Temp file for the captured screenshot.
    fd, output_path = tempfile.mkstemp(suffix=".bmp")
    os.close(fd)
    os.unlink(output_path)  # remove so we can detect when it's written

    # Inline capture script — keeps it self-contained, no external file needed.
    # Use forward slashes to avoid escaping issues in the -c string.
    out_escaped = output_path.replace("\\", "/")
    capture_script = (
        "import mss;from PIL import Image;"
        f"s=mss.mss();r=s.grab(s.monitors[{monitor_idx}]);"
        "i=Image.frombytes('RGB',(r.width,r.height),r.rgb);"
        f"i.save('{out_escaped}','BMP')"
    )

    python_exe = sys.executable
    cmd = f'"{python_exe}" -c "{capture_script}"'

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    si.lpDesktop = "WinSta0\\Default"  # interactive desktop
    si.dwFlags = 0x00000001  # STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE

    pi = PROCESS_INFORMATION()

    # CREATE_UNICODE_ENVIRONMENT=0x400 | CREATE_NO_WINDOW=0x08000000
    creation_flags = 0x00000400 | 0x08000000

    success = advapi32.CreateProcessAsUserW(
        token,
        None,           # lpApplicationName
        cmd,            # lpCommandLine
        None, None,     # process/thread security attributes
        False,          # inherit handles
        creation_flags,
        env_block,
        None,           # current directory (inherit)
        ctypes.byref(si),
        ctypes.byref(pi),
    )

    if not success:
        err = kernel32.GetLastError()
        _log_capture(f"CreateProcessAsUserW failed with error {err}")
        return None

    try:
        # Wait for the capture process to finish (max 15 seconds).
        kernel32.WaitForSingleObject(pi.hProcess, 15000)
    finally:
        kernel32.CloseHandle(pi.hProcess)
        kernel32.CloseHandle(pi.hThread)

    # Read the captured image.
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        try:
            img = Image.open(output_path).convert("RGB")
            return img
        finally:
            os.unlink(output_path)
    else:
        _log_capture("Capture subprocess did not produce output")
        return None


def _log_capture(msg: str) -> None:
    print(f"  [screen] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Cache the Session 0 check — it never changes during a process's lifetime.
_is_session_zero: bool | None = None


def _in_session_zero() -> bool:
    """Check if we're running in Windows Session 0 (service context)."""
    global _is_session_zero
    if _is_session_zero is not None:
        return _is_session_zero
    if sys.platform != "win32":
        _is_session_zero = False
        return False
    try:
        _is_session_zero = _get_current_session_id() == 0
    except Exception:
        _is_session_zero = False
    if _is_session_zero:
        _log_capture("Running in Session 0 — will use interactive session capture")
    return _is_session_zero


def _capture_raw(monitor_idx: int) -> Image.Image:
    """Capture a raw screenshot, with Windows Session 0 bypass.

    On Windows Session 0, mss always returns a black frame because the
    service has no visible desktop.  We skip mss entirely and go straight
    to CreateProcessAsUser to capture from the interactive session.
    """
    # On Windows Session 0, skip mss — it will always be black.
    if _in_session_zero():
        session_img = _capture_via_interactive_session(monitor_idx)
        if session_img is not None:
            return session_img
        _log_capture(
            "WARNING: Interactive session capture failed. "
            "Ensure the service runs as LocalSystem and a user is logged in."
        )

    # Normal path: mss works fine on Linux, macOS, and Windows user sessions.
    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[monitor_idx])
        img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)

    # Safety net: if mss returned black on Windows (e.g., RDP disconnect),
    # try the interactive session capture as a last resort.
    if sys.platform == "win32" and _is_black_frame(img):
        _log_capture("Black frame from mss — trying interactive session capture")
        session_img = _capture_via_interactive_session(monitor_idx)
        if session_img is not None and not _is_black_frame(session_img):
            return session_img

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


def capture_zoom_region(
    region: list[int],
    screenshot_w: int,
    screenshot_h: int,
    jpeg_quality: int = 50,
) -> str:
    """Capture a screen region at full native resolution for the zoom action.

    *region* is [x1, y1, x2, y2] in screenshot-space coordinates.
    Returns base64-encoded JPEG of the cropped region without downscaling.
    """
    native_w, native_h = get_screen_size()
    sx = native_w / screenshot_w
    sy = native_h / screenshot_h

    x1 = int(region[0] * sx)
    y1 = int(region[1] * sy)
    x2 = int(region[2] * sx)
    y2 = int(region[3] * sy)

    img = _capture_raw(0)
    cropped = img.crop((x1, y1, x2, y2))

    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def get_screen_size() -> tuple[int, int]:
    """Return the native screen resolution (before any scaling)."""
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            pass
    with mss.mss() as sct:
        mon = sct.monitors[0]
        return mon["width"], mon["height"]
