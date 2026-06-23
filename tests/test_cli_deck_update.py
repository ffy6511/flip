"""CLI coverage for the bundled-deck update / assign-ids / prune commands."""

import json

from typer.testing import CliRunner

from flip import bootstrap, store
from flip.cli import app


def _patch_demo_bundled(monkeypatch, *, version="1", name="Demo", source_lang="en", role="demo"):
    monkeypatch.setattr(bootstrap, "_bundled_slugs", lambda: ["demo"])
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": "demo",
        "name": name,
        "source_lang": source_lang,
        "role": role,
        "content_version": version,
    })


def test_deck_assign_ids_dry_run(deck):
    # The example fixture deck's questions already have ids, so dry-run reports
    # zero — verifies the no-op path and the preview message format.
    result = CliRunner().invoke(app, ["deck", "assign-ids", "example", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would assign" in result.output


def test_deck_assign_ids_writes_uuids(flip_home, monkeypatch):
    # Seed a deck with id-less questions, then assign ids via the CLI.
    decks_dir = flip_home / "decks"
    deck_dir = decks_dir / "noid"
    deck_dir.mkdir(parents=True)
    (deck_dir / "tiku.json").write_text(
        json.dumps({"1": [
            {"topic": "q1", "options": ["A. x"], "answer": "A"},
            {"topic": "q2", "options": ["A. x"], "answer": "A"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (deck_dir / "manifest.toml").write_text(
        '[deck]\nname = "NoId"\nslug = "noid"\nsource_lang = "en"\n'
        'answer_alphabet = "ABCD"\nmax_display_options = 4\n\n'
        '[explain]\nrole = "demo"\nmax_chars = 200\n',
        encoding="utf-8",
    )
    import re

    result = CliRunner().invoke(app, ["deck", "assign-ids", "noid"])

    assert result.exit_code == 0, result.output
    assert "assigned 2 new id(s)" in result.output
    tiku = json.loads((deck_dir / "tiku.json").read_text(encoding="utf-8"))
    ids = [q["id"] for q in tiku["1"]]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert all(re.fullmatch(r"q-[0-9a-f]{12}", i) for i in ids)


def test_deck_update_runs_merge_and_preserves_history(tmp_path, monkeypatch):
    # Install a fake bundled v1, add a mark, ship v2, run `flip deck update`.
    home = tmp_path / "flip_home"
    decks_dir = home / "decks"
    monkeypatch.setenv("FLIP_HOME", str(home))

    q1 = {"topic": "t1", "options": ["A. x", "B. y"], "answer": "A"}
    _patch_demo_bundled(monkeypatch, version="1")
    monkeypatch.setattr(bootstrap, "_read_bundled_tiku_text",
                        lambda _slug: json.dumps({"1": [q1]}, ensure_ascii=False))
    bootstrap.install_bundled("demo", decks_dir)

    # Mark the installed question.
    from flip.deck import load_deck
    from flip import engine
    deck = load_deck(decks_dir / "demo")
    tiku = store.load_tiku(deck)
    tiku["1"][0]["marked"] = True
    tiku["1"][0]["user_note"] = "MINE"
    store.save_tiku(deck, tiku)
    engine._sync_marked_from_tiku(deck)
    qid = tiku["1"][0]["id"]

    # Ship v2 with a topic edit (same id).
    monkeypatch.setattr(bootstrap, "_read_bundled_tiku_text",
                        lambda _slug: json.dumps({"1": [
                            {"id": qid, "topic": "t1 [fixed]", "options": ["A. x", "B. y"], "answer": "A"},
                        ]}, ensure_ascii=False))
    _patch_demo_bundled(monkeypatch, version="2")

    result = CliRunner().invoke(app, ["deck", "update", "demo"])

    assert result.exit_code == 0, result.output
    assert "update preview:" in result.output
    assert "updated deck demo to content_version=2" in result.output
    # History survived.
    deck2 = load_deck(decks_dir / "demo")
    assert deck2.content_version == "2"
    tiku2 = store.load_tiku(deck2)
    assert tiku2["1"][0]["id"] == qid
    assert tiku2["1"][0]["marked"] is True
    assert tiku2["1"][0]["user_note"] == "MINE"


def test_deck_update_already_current_is_noop(deck):
    # example is not a bundled deck -> update reports it's not bundled, exit 1.
    result = CliRunner().invoke(app, ["deck", "update", "example"])
    assert result.exit_code == 1
    assert "not a bundled deck" in result.output


def test_deck_prune_no_orphans_is_noop(tmp_path, monkeypatch):
    home = tmp_path / "flip_home"
    decks_dir = home / "decks"
    monkeypatch.setenv("FLIP_HOME", str(home))
    _patch_demo_bundled(monkeypatch, version="1")
    monkeypatch.setattr(bootstrap, "_read_bundled_tiku_text",
                        lambda _slug: json.dumps({"1": [
                            {"id": "q-aaaaaaaaaaaa", "topic": "t", "options": ["A. x"], "answer": "A"},
                        ]}, ensure_ascii=False))
    bootstrap.install_bundled("demo", decks_dir)

    result = CliRunner().invoke(app, ["deck", "prune", "demo", "--yes"])

    assert result.exit_code == 0, result.output
    assert "no orphaned questions" in result.output


def test_deck_versions_cli_switches(tmp_path, monkeypatch):
    home = tmp_path / "flip_home"
    decks_dir = home / "decks"
    monkeypatch.setenv("FLIP_HOME", str(home))

    _patch_demo_bundled(monkeypatch, version="2")
    monkeypatch.setattr(bootstrap, "_read_bundled_tiku_text",
                        lambda _slug: json.dumps({"1": [
                            {"id": "q-1", "topic": "new topic", "options": ["A. x"], "answer": "A"},
                        ]}, ensure_ascii=False))
    bootstrap.install_bundled("demo", decks_dir)

    backup_dir = home / "backups" / "demo-update-20260623-120000"
    backup_dir.mkdir(parents=True)
    (backup_dir / "tiku.json").write_text(
        json.dumps({"1": [
            {"id": "q-1", "topic": "old topic", "options": ["A. x"], "answer": "A", "user_note": "maintainer"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (backup_dir / "manifest.toml").write_text(
        '[deck]\nname = "Demo"\nslug = "demo"\nsource_lang = "en"\n'
        'answer_alphabet = "ABCD"\nmax_display_options = 4\n'
        'content_version = "1"\n\n[explain]\nrole = "demo"\nmax_chars = 200\n',
        encoding="utf-8",
    )
    (backup_dir / "meta.json").write_text(
        json.dumps({"slug": "demo", "content_version": "1", "op": "update", "timestamp": "20260623-120000"}),
        encoding="utf-8",
    )

    from flip.deck import load_deck
    deck = load_deck(decks_dir / "demo")
    tiku = store.load_tiku(deck)
    tiku["1"][0]["user_note"] = "MINE"
    store.save_tiku(deck, tiku)

    result = CliRunner().invoke(app, ["deck", "versions", "demo"], input="1\n")

    assert result.exit_code == 0, result.output
    assert "switched deck demo to content_version=1" in result.output
    deck2 = load_deck(decks_dir / "demo")
    assert deck2.content_version == "1"
    assert store.load_tiku(deck2)["1"][0]["user_note"] == "MINE"
