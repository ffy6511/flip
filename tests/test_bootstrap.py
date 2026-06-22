"""Pure-function coverage for the bundled-deck installer.

These tests pin the on-demand install semantics that replaced the old silent
first-run auto-install:
  * `available_bundled_slugs` reflects "directory doesn't exist" — an installed
    deck drops out of the list, a removed one reappears (goal ②).
  * `install_bundled` produces a structurally valid deck that load_deck accepts.
No TUI loops here — those live in tests/test_engine_loop.py.
"""

import shutil

from flip import bootstrap, store
from flip.deck import load_deck


def test_available_bundled_slugs_empty_home(tmp_path):
    decks_dir = tmp_path / "decks"
    assert bootstrap.available_bundled_slugs(decks_dir) == ["se-template"]


def test_available_bundled_slugs_excludes_installed(tmp_path):
    decks_dir = tmp_path / "decks"
    bootstrap.install_bundled("se-template", decks_dir)
    assert bootstrap.available_bundled_slugs(decks_dir) == []


def test_available_bundled_slugs_reappears_after_remove(tmp_path):
    # Goal ②: deleting a bundled deck's directory brings it back to the
    # Bootstrap list, since the only criterion is directory existence.
    decks_dir = tmp_path / "decks"
    bootstrap.install_bundled("se-template", decks_dir)
    assert bootstrap.available_bundled_slugs(decks_dir) == []
    shutil.rmtree(decks_dir / "se-template")
    assert bootstrap.available_bundled_slugs(decks_dir) == ["se-template"]


def test_install_bundled_creates_valid_deck(tmp_path):
    decks_dir = tmp_path / "decks"
    bootstrap.install_bundled("se-template", decks_dir)

    deck = load_deck(decks_dir / "se-template")
    assert deck.slug == "se-template"
    assert deck.name == "软件工程模板"
    assert deck.source_lang == "en"

    # tiku is structurally valid: every question has a stable id, and no
    # transient per-question runtime fields leaked in from the bundled copy.
    tiku = store.read_json(deck.tiku_path)
    found = 0
    for _chapter, q in bootstrap.engine.iter_question_records(tiku):
        found += 1
        assert "id" in q and q["id"]
        assert "ai_explanation" not in q
        assert "marked" not in q
        assert "marked_at" not in q
    assert found > 0

    # manifest carries the deck persona used by AI-explain.
    manifest = deck.manifest_path.read_text(encoding="utf-8")
    assert "[explain]" in manifest
    assert "软件工程助教" in manifest


def test_install_bundled_assigns_prefixed_ids(tmp_path):
    # The prefix arg to ensure_question_ids makes ids deterministic and
    # namespaced by slug, so two bundled decks can't collide.
    decks_dir = tmp_path / "decks"
    bootstrap.install_bundled("se-template", decks_dir)
    tiku = store.read_json(decks_dir / "se-template" / "tiku.json")
    ids = [q["id"] for _, q in bootstrap.engine.iter_question_records(tiku)]
    assert ids, "expected at least one question id"
    assert all(i.startswith("se-template") for i in ids)


def test_bundled_deck_summary_has_question_count():
    summary = bootstrap.bundled_deck_summary("se-template")
    assert summary["slug"] == "se-template"
    assert summary["name"] == "软件工程模板"
    assert summary["source_lang"] == "en"
    assert summary["questions"] > 0
