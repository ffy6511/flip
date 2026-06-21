"""Deck manifest loading and validation.

A deck is a subject (SE, compilers, …) living under
`~/.local/share/flip/decks/<slug>/` with a `manifest.toml` and a `tiku.json`.
This module makes the subject-specific assumptions explicit instead of
hardcoding them in the engine.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from ._toml import load_toml


SLUG_RE = re.compile(r"^[a-z0-9-]+$")
MANIFEST_NAME = "manifest.toml"
TIKU_NAME = "tiku.json"
MARKED_NAME = "marked.json"
WRONG_DIR_NAME = "wrong"


class DeckError(Exception):
    """Raised when a deck manifest is missing or invalid."""


@dataclass
class ExplainConfig:
    role: str = ""
    max_chars: int = 200
    default_model: str = ""
    model_env: str = "FLIP_EXPLAIN_MODEL"

    def resolve_model(self) -> str:
        """Env var (if set) overrides default_model."""
        import os
        env_val = os.environ.get(self.model_env)
        return env_val if env_val else self.default_model


@dataclass
class Deck:
    slug: str
    name: str
    path: Path                       # deck directory
    source_lang: str
    answer_alphabet: str = "ABCD"
    explain: ExplainConfig = field(default_factory=ExplainConfig)

    # ---- derived paths ----

    @property
    def tiku_path(self) -> Path:
        return self.path / TIKU_NAME

    @property
    def marked_path(self) -> Path:
        return self.path / MARKED_NAME

    @property
    def wrong_dir(self) -> Path:
        return self.path / WRONG_DIR_NAME

    @property
    def manifest_path(self) -> Path:
        return self.path / MANIFEST_NAME

    @property
    def translation_enabled(self) -> bool:
        """Translation is a global property, but the deck reports its source lang.

        Whether translation actually fires is decided by the engine using both
        the deck's source_lang and the global config's target_lang.
        """
        return True  # the engine consults config.translation_enabled instead

    @property
    def answer_letters(self) -> list:
        return list(self.answer_alphabet)

    def digit_to_letter(self, digit: int) -> str:
        """Map a 1-based index to its answer letter, or '' if out of range."""
        if 1 <= digit <= len(self.answer_alphabet):
            return self.answer_alphabet[digit - 1]
        return ""


def _validate_alphabet(value: str) -> str:
    value = (value or "ABCD").upper()
    if not value.isalpha():
        raise DeckError(f"answer_alphabet must be letters only, got: {value!r}")
    if len(set(value)) != len(value):
        raise DeckError(f"answer_alphabet has duplicates: {value!r}")
    return value


def load_deck(deck_dir: Path) -> Deck:
    """Load and validate a deck from its directory."""
    deck_dir = Path(deck_dir)
    if not deck_dir.is_dir():
        raise DeckError(f"deck directory not found: {deck_dir}")

    manifest_path = deck_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise DeckError(f"manifest not found: {manifest_path}")

    data = load_toml(manifest_path)

    deck_table = data.get("deck", {})
    slug = deck_table.get("slug") or deck_dir.name
    name = deck_table.get("name", "")
    source_lang = deck_table.get("source_lang", "")
    answer_alphabet = _validate_alphabet(deck_table.get("answer_alphabet", "ABCD"))

    if not name:
        raise DeckError(f"[deck].name is required in {manifest_path}")
    if not source_lang:
        raise DeckError(f"[deck].source_lang is required in {manifest_path}")
    if not SLUG_RE.match(slug):
        raise DeckError(f"slug {slug!r} must match {SLUG_RE.pattern}")
    if slug != deck_dir.name:
        raise DeckError(f"slug {slug!r} must equal directory name {deck_dir.name!r}")

    explain_table = data.get("explain", {})
    explain = ExplainConfig(
        role=explain_table.get("role", ""),
        max_chars=int(explain_table.get("max_chars", 200)),
        default_model=explain_table.get("default_model", ""),
        model_env=explain_table.get("model_env", "FLIP_EXPLAIN_MODEL"),
    )
    if not explain.role:
        raise DeckError(f"[explain].role is required in {manifest_path}")

    known_top = {"deck", "explain"}
    unknown = set(data) - known_top
    if unknown:
        import sys
        print(f"warning: unknown manifest sections ignored: {sorted(unknown)}", file=sys.stderr)

    return Deck(
        slug=slug,
        name=name,
        path=deck_dir,
        source_lang=source_lang,
        answer_alphabet=answer_alphabet,
        explain=explain,
    )


def list_decks(decks_root: Path) -> list:
    """Return slugs of decks that have a valid manifest, sorted."""
    decks_root = Path(decks_root)
    if not decks_root.is_dir():
        return []
    slugs = []
    for entry in sorted(decks_root.iterdir()):
        if entry.is_dir() and (entry / MANIFEST_NAME).exists():
            slugs.append(entry.name)
    return slugs
