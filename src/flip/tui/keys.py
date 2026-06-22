"""Raw terminal key reading.

Extracted verbatim from se_regressor.py — Unix-only (relies on termios/fcntl).
This module has no business logic; it only reads keystrokes from a cbreak tty.
"""

import os
import select
import signal
import sys
import time
import fcntl
import termios
import tty


RESIZE_KEY = "\x00resize"
RESIZE_DEBOUNCE_SECONDS = 0.08
_resize_pending = False
_resize_at = 0.0
_resize_installed = False


def _mark_resize(_signum, _frame):
    global _resize_pending, _resize_at
    _resize_pending = True
    _resize_at = time.monotonic()


def install_resize_handler():
    global _resize_installed
    if _resize_installed or not hasattr(signal, "SIGWINCH"):
        return
    signal.signal(signal.SIGWINCH, _mark_resize)
    _resize_installed = True


def read_key():
    """Read one keypress. Returns escape-prefixed sequences like '\\x1b[D' for arrows."""
    install_resize_handler()
    while True:
        if _resize_pending and time.monotonic() - _resize_at >= RESIZE_DEBOUNCE_SECONDS:
            globals()["_resize_pending"] = False
            return RESIZE_KEY

        ready, _, _ = select.select([sys.stdin], [], [], 0.02)
        if not ready:
            continue
        break

    char = sys.stdin.read(1)
    if char == '\x1b':
        time.sleep(0.01)
        fd = sys.stdin.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            rest = sys.stdin.read(2) or ""
        except (BlockingIOError, TypeError):
            rest = ""
        finally:
            fcntl.fcntl(fd, fcntl.F_SETFL, flags)
        return char + rest
    return char


def save_tty():
    """Capture current termios attrs so they can be restored on exit."""
    return termios.tcgetattr(sys.stdin)


def restore_tty(attrs):
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, attrs)


def enter_cbreak():
    tty.setcbreak(sys.stdin)
