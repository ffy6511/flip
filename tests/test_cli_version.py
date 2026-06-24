"""CLI coverage for `flip --version` / `flip -V`.

The version is read from installed package metadata (importlib.metadata), so we
assert the behavior (prints something non-empty, exits 0, both flags work) rather
than a hardcoded number that would drift across releases.
"""

from typer.testing import CliRunner

from flip.cli import app


def test_version_long_flag_outputs_nonempty():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    out = result.output.strip()
    assert out, "--version printed nothing"
    # Looks like a version string (digits + dots), not an error message.
    assert any(ch.isdigit() for ch in out), f"unexpected version output: {out!r}"


def test_version_short_flag_matches_long():
    long = CliRunner().invoke(app, ["--version"]).output.strip()
    short = CliRunner().invoke(app, ["-V"]).output.strip()
    assert long == short


def test_version_does_not_enter_tui():
    # --version must exit before reaching the interactive deck picker. With no
    # TTY this would otherwise error; a clean exit 0 proves it short-circuited.
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
