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
from collections import deque
from codecs import getincrementaldecoder

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
_pending_keys = deque()
_utf8_decoder = getincrementaldecoder("utf-8")()


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


def _read_ready_bytes(fd):
    """Drain all bytes currently readable from the tty fd.

    `select` only reports kernel-level readability. Once Python decodes one
    char from `sys.stdin`, the remaining bytes may sit in user-space buffers
    and become invisible to the next `select` call. Reading raw bytes from the
    fd avoids that split-brain state.
    """
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    data = bytearray()
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        while True:
            try:
                chunk = os.read(fd, 64)
            except BlockingIOError:
                break
            if not chunk:
                break
            data.extend(chunk)
        if data == b"\x1b":
            time.sleep(0.01)
            try:
                data.extend(os.read(fd, 2))
            except BlockingIOError:
                pass
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)
    return bytes(data)


def _queue_decoded_keys(data):
    index = 0
    while index < len(data):
        byte = data[index]
        if byte == 0x1b:
            end = min(len(data), index + 3)
            _pending_keys.append(data[index:end].decode("latin1"))
            index = end
            continue
        decoded = _utf8_decoder.decode(bytes([byte]), final=False)
        if decoded:
            _pending_keys.extend(decoded)
        index += 1


def read_key(resize_key):
    """Read one keypress. Returns escape-prefixed sequences like '\\x1b[D' for arrows."""
    install_resize_handler(resize_key)
    while True:
        if _pending_keys:
            return _pending_keys.popleft()

        if _resize_pending and time.monotonic() - _resize_at >= RESIZE_DEBOUNCE_SECONDS:
            globals()["_resize_pending"] = False
            return resize_key

        ready, _, _ = select.select([sys.stdin], [], [], 0.02)
        if not ready:
            continue
        data = _read_ready_bytes(sys.stdin.fileno())
        if not data:
            continue
        _queue_decoded_keys(data)
        if _pending_keys:
            return _pending_keys.popleft()


def save_tty():
    """Capture current termios attrs so they can be restored on exit."""
    return termios.tcgetattr(sys.stdin)


def restore_tty(attrs):
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, attrs)


def enter_cbreak():
    tty.setcbreak(sys.stdin)
