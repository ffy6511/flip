"""Windows terminal key reading backend (msvcrt).

Mirrors the Unix backend's key-encoding contract exactly, so engine_loop
doesn't care which platform it runs on:

  - ordinary printable char  -> that char
  - Ctrl-C / Ctrl-Break      -> '\\x03'   (so KeyboardInterrupt-style paths work)
  - Backspace                -> '\\x08'   (engine_loop treats both \\x7f and \\b as backspace)
  - Enter                    -> '\\r'
  - Esc (bare)               -> '\\x1b'
  - Arrow keys               -> '\\x1b[A' / '\\x1b[B' / '\\x1b[C' / '\\x1b[D'
  - terminal resized         -> RESIZE_KEY

Design notes:

msvcrt.getwch returns a Unicode char for normal keys and the special
NUL/PE precursor (\\x00 / \\xe0) for extended keys; the follow-up byte is a
virtual-key code we translate to the ANSI sequence the rest of flip already
expects. There is no SIGWINCH on Windows; resize is detected lazily by
comparing the cached console size on each poll (cheap, and the only portable
hook short of a background thread).

This module must never import termios/fcntl/tty — it's only imported on
Windows (see keys.py), and importing it on Unix is still safe because msvcrt
is the only platform-specific import here.
"""

import os
import sys
import time

try:
    import msvcrt      # type: ignore[import-not-found]
    _HAS_MSVCRT = True
except ImportError:  # pragma: no cover - non-Windows platform
    msvcrt = None     # type: ignore[assignment]
    _HAS_MSVCRT = False


# Virtual-key codes that follow the \\x00 or \\xe0 precursor from getwch.
# Mapped to the ANSI escape sequences engine_loop already understands, so
# arrow / paging / Home/End behavior matches the Unix build byte-for-byte.
_VK_TO_ANSI = {
    "H": "\x1b[A",   # Up
    "P": "\x1b[B",   # Down
    "M": "\x1b[C",   # Right
    "K": "\x1b[D",   # Left
    "G": "\x1b[H",   # Home  (engine_loop tolerates unknown sequences)
    "O": "\x1b[F",   # End
    "I": "\x1b[5~",  # Page Up
    "Q": "\x1b[6~",  # Page Down
    "R": "\x1b[2~",  # Insert
    "S": "\x1b[3~",  # Delete
    "\x85": "\x1b[A",  # F11-ish / variant Up  on some layouts
    "\x86": "\x1b[B",  # variant Down
    "\x87": "\x1b[C",  # variant Right
    "\x88": "\x1b[D",  # variant Left
}


# Debounce + polling cadence. Kept tiny so the TUI feels responsive while
# still yielding the CPU between keypresses.
_POLL_INTERVAL = 0.02
RESIZE_DEBOUNCE_SECONDS = 0.08

# Last seen console dimensions (columns, lines). A change => resize event.
_last_size = None
_resize_pending = False
_resize_at = 0.0


def is_supported() -> bool:
    """True when the msvcrt backend can actually drive a console here."""
    return _HAS_MSVCRT


def _console_size():
    """Return (columns, lines) using the Windows API, or None if unavailable.

    ctypes is preferred over shutil.get_terminal_size here because it reads
    the live console buffer even when stdout is redirected; shutil falls back
    to (80, 24) in that case which would make resize detection useless.
    """
    try:
        from ctypes import create_string_buffer, windll  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - not Windows
        return None
    try:
        # STD_OUTPUT_HANDLE = -11 (per WinCon API)
        h = windll.kernel32.GetStdHandle(-11)
        csbi = create_string_buffer(22)
        if not windll.kernel32.GetConsoleScreenBufferInfo(h, csbi):
            return None
        # Layout of CONSOLE_SCREEN_BUFFER_INFO:
        #   bufx, bufy (2+2), curx, cury (2+2), attr (2),
        #   left, top, right, bottom (2*4), maxx, maxy (2+2)
        import struct
        (_bx, _by, _cx, _cy, _attr,
         left, top, right, bottom, _mx, _my) = struct.unpack("hhhhHhhhhhh", csbi.raw)
        cols = right - left + 1
        lines = bottom - top + 1
        if cols <= 0 or lines <= 0:
            return None
        return cols, lines
    except Exception:  # pragma: no cover - defensive
        return None


def install_resize_handler(resize_key):
    """No-op on Windows; resize is detected by polling in read_key.

    Kept for API parity with the Unix backend so keys.py can call it
    unconditionally.
    """
    return


def _refresh_resize_state():
    """Cache current console size; flag a resize if it changed since last poll."""
    global _last_size, _resize_pending, _resize_at
    size = _console_size()
    if size is None:
        return
    if _last_size is None:
        _last_size = size
        return
    if size != _last_size:
        _last_size = size
        _resize_pending = True
        _resize_at = time.monotonic()


def read_key(resize_key):
    """Read one keypress from the Windows console.

    Polls kbhit at a short interval so resize detection can run between
    keypresses (there's no signal to interrupt us). Returns `resize_key`
    once the console size has stabilized past the debounce window.
    """
    global _resize_pending
    while True:
        _refresh_resize_state()
        if _resize_pending and time.monotonic() - _resize_at >= RESIZE_DEBOUNCE_SECONDS:
            _resize_pending = False
            return resize_key
        if msvcrt.kbhit():
            break
        time.sleep(_POLL_INTERVAL)

    ch = msvcrt.getwch()
    # Extended key: a second getwch returns the virtual-key code.
    if ch in ("\x00", "\xe0"):
        vk = msvcrt.getwch()
        return _VK_TO_ANSI.get(vk, "")
    # Ctrl-C is delivered as \x03 by getwch on Windows too; Ctrl-Break
    # arrives as a KeyboardInterrupt by default, which we let propagate.
    return ch


def save_tty():
    """Capture current console mode so restore_tty can put it back.

    On Windows there's no termios-style cbreak mode to toggle for raw input
    — msvcrt.getwch already reads without line buffering or echo. We still
    stash the output handle's mode so a future VT/echo tweak could be
    undone; today it's an opaque token.
    """
    try:
        from ctypes import windll  # type: ignore[import-not-found]
        h = windll.kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = (windll.kernel32.GetConsoleMode(h) & 0xFFFFFFFF) if h else 0
        return ("win", h, mode)
    except Exception:  # pragma: no cover - defensive
        return ("win", None, None)


def restore_tty(attrs):
    """Restore the console mode captured by save_tty (best-effort, no-op if N/A)."""
    if not attrs or attrs[0] != "win":
        return
    _tag, h, mode = attrs
    if h is None or mode is None:
        return
    try:
        from ctypes import windll  # type: ignore[import-not-found]
        windll.kernel32.SetConsoleMode(h, mode)
    except Exception:  # pragma: no cover - defensive
        pass


def enter_cbreak():
    """Enter raw-input mode.

    msvcrt.getwch is already unbuffered/no-echo, so there's nothing to put
    the console into. Kept for API parity with the Unix backend; intentionally
    a no-op so engine_loop's save_tty/enter_cbreak/restore_tty sandwich works
    unchanged on both platforms.
    """
    return
