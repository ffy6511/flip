"""Raw terminal key reading.

Extracted verbatim from se_regressor.py — Unix-only (relies on termios/fcntl).
This module has no business logic; it only reads keystrokes from a cbreak tty.
"""

import os
import sys
import time
import fcntl
import termios
import tty


def read_key():
    """Read one keypress. Returns escape-prefixed sequences like '\\x1b[D' for arrows."""
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
