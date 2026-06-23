"""On-demand installation of bundled decks.

Pure helpers consumed by the deck picker's Bootstrap tab. Unlike a "first run"
auto-install, nothing here runs at config load — the user picks what to install
from the Bootstrap tab (engine_loop.deck_picker), and these functions do the
actual work. A deck removed via `flip deck remove` simply re-appears in the
available list because the criterion is "the deck directory doesn't exist".
"""

from __future__ import annotations

import json
from copy import deepcopy
from importlib import resources
from pathlib import Path

from . import engine, store
from .deck import Deck
from .importers import validate_tiku


# Single source of truth for bundled deck metadata. The Bootstrap tab iterates
# this dict (insertion order is the display order) and filters by which deck
# directories already exist to compute the "available to install" list.
BUNDLED_DECK_SPECS = {
    "se-template": {
        "name": "软件工程模板",
        "source_lang": "en",
        "role": "软件工程助教",
        # Monotonic content version of the bundled tiku data. Bump on every
        # upstream change so already-installed decks show as updatable.
        "content_version": "1",
    },
}


def available_bundled_slugs(decks_dir: Path) -> list[str]:
    """Slugs that can be offered by the Bootstrap tab right now.

    A bundled slug is available iff no deck directory of that slug exists under
    `decks_dir`. So a freshly removed bundled deck re-appears here, while an
    installed one is hidden until its directory disappears.
    """
    decks_dir = Path(decks_dir)
    return [slug for slug in BUNDLED_DECK_SPECS if not (decks_dir / slug).exists()]


def install_bundled(slug: str, decks_dir: Path) -> None:
    """Install one bundled deck by slug into `decks_dir/<slug>`.

    Reads the bundled tiku.json from package data, validates it, assigns stable
    question ids, and writes tiku + manifest. Caller (the Bootstrap tab) is
    responsible for having checked the slug is actually available; this function
    will still work if the directory already exists but is normally only called
    for slugs returned by `available_bundled_slugs`.
    """
    spec = BUNDLED_DECK_SPECS[slug]
    decks_dir = Path(decks_dir)
    decks_dir.mkdir(parents=True, exist_ok=True)

    raw_text = _read_bundled_tiku_text(slug)
    tiku_data = json.loads(raw_text)
    errs = validate_tiku(tiku_data)
    if errs:
        raise ValueError(f"bundled deck {slug} failed validation: {'; '.join(errs[:5])}")

    installed_tiku = deepcopy(tiku_data)
    engine.ensure_question_ids(installed_tiku, prefix=slug)
    answer_alphabet = _detect_alphabet_from_tiku(installed_tiku)

    deck = Deck(
        slug=slug,
        name=spec["name"],
        path=decks_dir / slug,
        source_lang=spec["source_lang"],
        answer_alphabet=answer_alphabet,
        content_version=spec.get("content_version", "0"),
    )
    deck.path.mkdir(parents=True, exist_ok=True)
    store.save_tiku(deck, installed_tiku)
    deck.manifest_path.write_text(
        _build_manifest_text(
            slug=slug,
            display_name=spec["name"],
            source_lang=spec["source_lang"],
            answer_alphabet=answer_alphabet,
            role_text=spec["role"],
            content_version=spec.get("content_version", "0"),
        ),
        encoding="utf-8",
    )


def bundled_deck_summary(slug: str) -> dict:
    """Lightweight metadata for the Bootstrap tab's display rows.

    Returns a dict with the spec fields plus a precomputed question count from
    the bundled tiku.json, so the renderer can show "(120 题, en→zh)" without
    each render having to parse the JSON itself.
    """
    spec = BUNDLED_DECK_SPECS[slug]
    data = json.loads(_read_bundled_tiku_text(slug))
    count = 0
    for _, _q in engine.iter_question_records(data):
        count += 1
    return {
        "slug": slug,
        "name": spec["name"],
        "source_lang": spec["source_lang"],
        "questions": count,
    }


def _read_bundled_tiku_text(slug: str) -> str:
    resource = resources.files("flip").joinpath("bundled_decks", slug, "tiku.json")
    return resource.read_text(encoding="utf-8")


def _detect_alphabet_from_tiku(data):
    letters = set("ABCD")
    for _, q in engine.iter_question_records(data):
        for opt in q.get("options", []):
            if isinstance(opt, str) and opt:
                letters.add(opt[0].upper())
    valid = sorted(letter for letter in letters if letter in "ABCDEFGHIJ")
    return "".join(valid) if valid else "ABCD"


def _build_manifest_text(*, slug: str, display_name: str, source_lang: str, answer_alphabet: str, role_text: str, content_version: str = "0") -> str:
    return (
        "[deck]\n"
        f'name = "{display_name}"\n'
        f'slug = "{slug}"\n'
        f'source_lang = "{source_lang}"\n'
        f'answer_alphabet = "{answer_alphabet}"\n'
        "max_display_options = 4\n"
        f'content_version = "{content_version}"\n'
        "\n"
        "[explain]\n"
        f'role = "{role_text}"\n'
        "max_chars = 200\n"
        "# default_model and model_env override the global [explain].model.\n"
    )
