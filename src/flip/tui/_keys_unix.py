"""Unix terminal key reading backend (termios/fcntl/tty).

This is the original implementation extracted verbatim from se_regressor.py.
It produces the key-encoding contract that engine_loop depends on:

  - ordinary printable char  -> that char (e.g. 'a', '1')
  - Ctrl-C                   -> '\\x03'
  - Backspace                -> '\\x7f' or '\\b'
  - Enter                    -> '\\r' or '\\n'
  - Esc (bare)               -> '\\x1b'
  - Arrow keys               -> '\\x1b[A' / '\\x1b[B' / '\\x1b[C' / '\\x1b[D'
  - terminal resized         -> RESIZE_KEY ('\\x00resize')

The Windows backend mirrors this exact encoding so engine_loop stays
platform-agnostic. See keys.py for the dispatcher.

No business logic here — only raw keystroke reading from a cbreak tty.
"""

import os
import select
import signal
import sys
import time

try:
    import fcntl      # type: ignore[import-not-found]
    import termios    # type: ignore[import-not-found]
    import tty        # type: ignore[import-not-found]
    _HAS_TERMIOS = True
except ImportError:  # pragma: no cover - non-Unix platform / stripped env
    fcntl = None      # type: ignore[assignment]
    termios = None    # type: ignore[assignment]
    tty = None        # type: ignore[assignment]
    _HAS_TERMIOS = False


RESIZE_DEBOUNCE_SECONDS = 0.08
_resize_pending = False
_resize_at = 0.0
_resize_installed = False


def is_supported() -> bool:
    """True when the termios backend can actually drive a tty here."""
    return _HAS_TERMIOS


def _mark_resize(_signum, _frame):
    global _resize_pending, _resize_at
    _resize_pending = True
    _resize_at = time.monotonic()


def install_resize_handler(resize_key):
    """Hook SIGWINCH so a resize is reported as `resize_key` on the next read.

    Idempotent. No-op when the platform lacks SIGWINCH (then resize events
    simply aren't detected — engine_loop tolerates that by re-rendering on
    other keys too).
    """
    global _resize_installed
    if _resize_installed or not hasattr(signal, "SIGWINCH"):
        return
    signal.signal(signal.SIGWINCH, _mark_resize)
    _resize_installed = True


def read_key(resize_key):
    """Read one keypress. Returns escape-prefixed sequences like '\\x1b[D' for arrows."""
    install_resize_handler(resize_key)
    while True:
        if _resize_pending and time.monotonic() - _resize_at >= RESIZE_DEBOUNCE_SECONDS:
            globals()["_resize_pending"] = False
            return resize_key

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
