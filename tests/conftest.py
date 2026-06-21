"""Pytest fixtures: an isolated FLIP_HOME with the example deck registered."""

import os
import shutil
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DECK_SRC = REPO_ROOT / "decks" / "example"


@pytest.fixture
def flip_home(tmp_path, monkeypatch):
    """A clean ~/.local/share/flip equivalent, with the example deck copied in."""
    home = tmp_path / "flip_home"
    decks_root = home / "decks" / "example"
    decks_root.mkdir(parents=True)
    shutil.copyfile(EXAMPLE_DECK_SRC / "manifest.toml", decks_root / "manifest.toml")
    shutil.copyfile(EXAMPLE_DECK_SRC / "tiku.json", decks_root / "tiku.json")

    monkeypatch.setenv("FLIP_HOME", str(home))
    return home


@pytest.fixture
def config(flip_home):
    from flip.config import load_config
    return load_config()


@pytest.fixture
def deck(flip_home):
    from flip.deck import load_deck
    from flip.config import load_config
    cfg = load_config()
    return load_deck(cfg.decks_dir / "example")
