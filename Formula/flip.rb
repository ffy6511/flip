# Homebrew formula for flip.
#
# The flip repo renders this formula with scripts/build_formula.sh. A tag push
# runs .github/workflows/update-homebrew-tap.yml, computes the source tarball
# sha256, and publishes the rendered file to ffy6511/homebrew-tap.
#
# Users install the published formula with:
#
#   brew tap ffy6511/tap
#   brew install flip
#
# Release entrypoint: bump pyproject.toml, merge the release commit, then push
# a version tag such as v0.2.0. Manual tap edits are limited to one-off repairs
# when the workflow cannot publish.

class Flip < Formula
  include Language::Python::Virtualenv

  desc "Terminal quiz trainer — a deck-agnostic template engine"
  homepage "https://github.com/ffy6511/flip"
  url "https://github.com/ffy6511/flip/archive/refs/tags/v0.0.0.tar.gz"
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
