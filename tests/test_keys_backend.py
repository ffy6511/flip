"""Cross-platform key-backend invariants.

The TUI loops in engine_loop compare read_key() output against literal byte
strings ('\x1b[D' for Left, '\x00resize' for a resize, etc.). Both backends
must produce these EXACT encodings or the TUI silently breaks on one platform.

These tests run on every platform (the Unix backend's termios calls are not
exercised here — only the Windows backend's pure mapping table, which is safe
to import anywhere). They pin the encoding contract so a future edit to
_Keys_windows can't drift out of sync with engine_loop's expectations.
"""

from flip.tui import keys
from flip.tui import _keys_windows as win


def test_dispatcher_exposes_full_public_api():
    # engine_loop imports these five symbols by name from flip.tui; if any
    # disappears the import line itself breaks at startup.
    for name in ("RESIZE_KEY", "read_key", "save_tty", "restore_tty", "enter_cbreak"):
        assert hasattr(keys, name), f"keys dispatcher lost public symbol: {name}"


def test_resize_sentinel_is_stable():
    # engine_loop checks `key == RESIZE_KEY`; both backends return this string.
    assert keys.RESIZE_KEY == "\x00resize"


def test_windows_backend_imports_safely_on_any_platform():
    # keys.py imports _keys_windows unconditionally (the dispatcher needs to
    # probe is_supported()). Importing it must never raise on Unix — the only
    # platform-specific import (msvcrt) is guarded.
    assert hasattr(win, "read_key")
    assert hasattr(win, "is_supported")


def test_windows_vk_mapping_matches_engine_loop_expectations():
    # Every arrow / nav key the TUI acts on must round-trip through the VK
    # table to the exact ANSI sequence engine_loop compares against. If this
    # drifts, arrow keys silently stop working on Windows.
    assert win._VK_TO_ANSI["H"] == "\x1b[A"   # Up
    assert win._VK_TO_ANSI["P"] == "\x1b[B"   # Down
    assert win._VK_TO_ANSI["M"] == "\x1b[C"   # Right
    assert win._VK_TO_ANSI["K"] == "\x1b[D"   # Left


def test_windows_save_restore_tty_is_safe_without_real_console():
    # On a platform without a Windows console (CI on Linux, or a redirected
    # handle), save_tty/restore_tty must degrade gracefully rather than crash.
    token = win.save_tty()
    assert token[0] == "win"
    win.restore_tty(token)        # must not raise
    win.restore_tty(None)         # must tolerate a missing token
    win.restore_tty(("win", None, None))  # and a degenerate token
