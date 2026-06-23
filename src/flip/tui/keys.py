"""Terminal key reading — platform dispatcher.

The engine_loop consumes a small, platform-independent contract from this
module:

  RESIZE_KEY          — sentinel returned when the terminal is resized
  read_key()          — one keypress, ANSI-encoded (\\x1b[D for Left, etc.)
  save_tty()          — capture tty state to restore on exit
  restore_tty(state)  — restore that state
  enter_cbreak()      — switch to raw, no-echo input

How that contract is satisfied depends on the platform:

  Unix-like    -> _keys_unix (termios/fcntl/tty + SIGWINCH)
  Windows      -> _keys_windows (msvcrt + console API resize polling)

The dispatch is by import availability (termios present => Unix backend;
msvcrt present => Windows backend), not by sys.platform string. That keeps
it robust on stripped Cygwin/MSYS builds and lets the test harness keep
monkeypatching engine_loop.read_key exactly as before — the public symbols
this module re-exports are unchanged.

The Unix backend was extracted verbatim from the original se_regressor.py
implementation; the Windows backend reproduces its key-encoding byte for
byte so the TUI loops don't carry any platform branches.
"""

# The resize sentinel is the one contract both backends MUST share: engine_loop
# compares read_key() == RESIZE_KEY, so both backends return this exact string.
RESIZE_KEY = "\x00resize"

from . import _keys_unix as _unix
from . import _keys_windows as _windows

if _unix.is_supported():
    _backend = _unix
elif _windows.is_supported():
    _backend = _windows
else:  # pragma: no cover - no supported terminal backend at all
    raise RuntimeError(
        "flip needs a terminal backend (termios on Unix, msvcrt on Windows) "
        "but neither is importable in this environment."
    )


# Public API: each function closes over RESIZE_KEY so the backends stay
# parameter-free and the test suite can still swap read_key on engine_loop.
def read_key():
    """Read one keypress. Returns '\\x1b[D'-style sequences for arrow keys."""
    return _backend.read_key(RESIZE_KEY)


def install_resize_handler():
    """Hook the platform's resize signal/poll. Idempotent; safe to call often."""
    return _backend.install_resize_handler(RESIZE_KEY)


def save_tty():
    """Capture tty/console state for later restore_tty."""
    return _backend.save_tty()


def restore_tty(attrs):
    """Restore state previously captured by save_tty (no-op safe on Windows)."""
    return _backend.restore_tty(attrs)


def enter_cbreak():
    """Enter raw no-echo input mode (no-op on Windows; msvcrt is already raw)."""
    return _backend.enter_cbreak()
