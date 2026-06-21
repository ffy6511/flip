"""Global configuration (~/.local/share/flip/config.toml).

Translation is a *global* capability, not a per-deck attribute: it is enabled
only when `source_lang != target_lang`. Decks whose source language equals the
target language simply never exercise the translation code path.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from ._toml import load_toml


DEFAULT_HOME = Path.home() / ".local" / "share" / "flip"
DEFAULT_CONFIG_NAME = "config.toml"


@dataclass
class Config:
    home: Path
    source_lang: str = "en"
    target_lang: str = "zh"
    default_deck: str = ""

    @property
    def translation_enabled(self) -> bool:
        """True only when source and target languages differ."""
        return bool(self.source_lang) and bool(self.target_lang) and self.source_lang != self.target_lang

    @property
    def config_path(self) -> Path:
        return self.home / DEFAULT_CONFIG_NAME

    @property
    def decks_dir(self) -> Path:
        return self.home / "decks"


def _bootstrap_default_config(path: Path) -> None:
    """Write a default config when none exists. Idempotent and best-effort."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'source_lang = "en"\n'
        'target_lang = "zh"\n'
        'default_deck = ""\n',
        encoding="utf-8",
    )


def load_config(home: Path = None) -> Config:
    """Load global config, creating a default on first run.

    Respects $FLIP_HOME for tests; otherwise uses ~/.local/share/flip.
    """
    env_home = os.environ.get("FLIP_HOME")
    resolved_home = Path(env_home) if env_home else (home or DEFAULT_HOME)

    config_path = resolved_home / DEFAULT_CONFIG_NAME
    source_lang = "en"
    target_lang = "zh"
    default_deck = ""

    if config_path.exists():
        data = load_toml(config_path)
        source_lang = data.get("source_lang", source_lang)
        target_lang = data.get("target_lang", target_lang)
        default_deck = data.get("default_deck", default_deck)
    else:
        # Create the default file so users see it and can edit it.
        _bootstrap_default_config(config_path)

    return Config(
        home=resolved_home,
        source_lang=source_lang,
        target_lang=target_lang,
        default_deck=default_deck,
    )
