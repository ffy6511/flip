# Homebrew formula for flip.
#
# This file lives in the flip repo as the source of truth. To publish via a
# tap, create a separate repo named `homebrew-tap` under the same GitHub
# account and copy this file to `<homebrew-tap>/Formula/flip.rb`. Users then
# install with:
#
#   brew tap ffy6511/tap
#   brew install flip
#
# When releasing a new version: tag the flip repo (e.g. v0.2.0), push the
# tag to trigger a GitHub Release with a source tarball, then update the
# `version` and `sha256` below (recompute with `shasum -a 256` on the
# tarball URL: `curl -sL <url> | shasum -a 256`).

class Flip < Formula
  desc "Terminal quiz trainer — a deck-agnostic template engine"
  homepage "https://github.com/ffy6511/flip"
  url "https://github.com/ffy6511/flip/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  # flip is pure Python; the formula installs it into an isolated venv and
  # symlinks the `flip` entry point onto the PATH. This is the same model
  # pipx uses, but without requiring pipx.
  uses_from_macos "python"

  depends_on "python@3.11"

  def install
    # Build a private venv so dependencies (typer, tomli on py<3.11) don't
    # collide with the user's system Python.
    venv = virtualenv_create(libexec, "python3")
    venv.pip_install_and_link(buildpath)
  end

  test do
    # `flip --help` exits 0 and mentions the deck command — a smoke test that
    # the entry point is wired up and typer loaded.
    assert_match "deck", shell_output("#{bin}/flip --help")
  end
end
