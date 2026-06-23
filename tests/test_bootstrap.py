"""Pure-function coverage for the bundled-deck installer.

These tests pin the on-demand install semantics that replaced the old silent
first-run auto-install:
  * `available_bundled_slugs` reflects "directory doesn't exist" — an installed
    deck drops out of the list, a removed one reappears (goal ②).
  * `install_bundled` produces a structurally valid deck that load_deck accepts.
No TUI loops here — those live in tests/test_engine_loop.py.
"""

import json
import shutil
from pathlib import Path

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


def test_install_bundled_assigns_uuid_ids(tmp_path):
    # Since the UUID switch, install_bundled assigns content-independent `q-<hex>`
    # ids (via ensure_question_ids), not slug-prefixed positional ids. The
    # bundled source itself carries UUIDs, so installed ids are those exact
    # values; if a question lacked one it would get a fresh UUID here.
    import re
    decks_dir = tmp_path / "decks"
    bootstrap.install_bundled("se-template", decks_dir)
    tiku = store.read_json(decks_dir / "se-template" / "tiku.json")
    ids = [q["id"] for _, q in bootstrap.engine.iter_question_records(tiku)]
    assert ids, "expected at least one question id"
    assert len(ids) == len(set(ids))  # all unique
    assert all(re.fullmatch(r"q-[0-9a-f]{12}", i) for i in ids)


def test_bundled_deck_summary_has_question_count():
    summary = bootstrap.bundled_deck_summary("se-template")
    assert summary["slug"] == "se-template"
    assert summary["name"] == "软件工程模板"
    assert summary["source_lang"] == "en"
    assert summary["questions"] > 0


# ---- bundled update (stage 4) ----
#
# These pin the core promise: updating a bundled deck preserves per-question
# history (mark/note/ai_explanation) on unchanged questions, migrates legacy
# positional ids to UUIDs, and bumps content_version. We use a tiny fake
# bundled spec (2 questions) instead of the real 561-question se-template so
# the tests stay fast and focused.

def _install_fake_bundled(monkeypatch, decks_dir, *, questions, version="1",
                          slug="demo", name="Demo", source_lang="en", role="demo"):
    """Stand up a throwaway bundled deck so update tests don't touch real data."""
    import json
    tiku = {"1": list(questions)}
    monkeypatch.setattr(bootstrap, "_bundled_slugs", lambda: [slug])
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": slug,
        "name": name,
        "source_lang": source_lang,
        "role": role,
        "content_version": version,
    })
    monkeypatch.setattr(bootstrap, "_read_bundled_tiku_text",
                        lambda _slug: json.dumps(tiku, ensure_ascii=False))
    bootstrap.install_bundled(slug, decks_dir)
    return slug


def _write_min_manifest(deck_dir, slug="demo", version="1"):
    """Write a minimal manifest.toml so store.export_deck can back the deck up."""
    (deck_dir / "manifest.toml").write_text(
        f'[deck]\nname = "Demo"\nslug = "{slug}"\nsource_lang = "en"\n'
        f'answer_alphabet = "ABCD"\nmax_display_options = 4\n'
        f'content_version = "{version}"\n\n[explain]\nrole = "demo"\nmax_chars = 200\n',
        encoding="utf-8",
    )


def test_updatable_bundled_decks_detects_version_diff(tmp_path, monkeypatch):
    decks_dir = tmp_path / "decks"
    slug = _install_fake_bundled(monkeypatch, decks_dir,
                                 questions=[{"topic": "t1", "options": ["A. x"], "answer": "A"}],
                                 version="1")
    # Same version -> not updatable.
    assert bootstrap.updatable_bundled_decks(decks_dir) == []
    # Bump shipped version -> updatable.
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": slug,
        "name": "Demo",
        "source_lang": "en",
        "role": "demo",
        "content_version": "2",
    })
    upd = bootstrap.updatable_bundled_decks(decks_dir)
    assert len(upd) == 1
    assert upd[0]["slug"] == slug
    assert upd[0]["current"] == "1" and upd[0]["latest"] == "2"


def test_update_bundled_preserves_mark_and_note_across_topic_edit(tmp_path, monkeypatch):
    from flip import engine
    decks_dir = tmp_path / "decks"
    q1 = {"topic": "old topic", "options": ["A. x", "B. y"], "answer": "A"}
    q2 = {"topic": "unchanged", "options": ["A. x", "B. y"], "answer": "B"}
    slug = _install_fake_bundled(monkeypatch, decks_dir, questions=[q1, q2], version="1")
    deck = load_deck(decks_dir / slug)

    # Learner marks q1 and adds a personal note.
    tiku = store.load_tiku(deck)
    q1_id = tiku["1"][0]["id"]
    tiku["1"][0]["marked"] = True
    tiku["1"][0]["marked_at"] = "2026-01-01T00:00:00"
    tiku["1"][0]["user_note"] = "MY NOTE"
    store.save_tiku(deck, tiku)
    engine._sync_marked_from_tiku(deck)

    # Ship v2: fix a typo in q1's topic (same id, new content).
    import json
    new_q1 = {"id": q1_id, "topic": "old topic [fixed]", "options": ["A. x", "B. y"], "answer": "A"}
    new_q2 = {"id": tiku["1"][1]["id"], "topic": "unchanged", "options": ["A. x", "B. y"], "answer": "B"}
    monkeypatch.setattr(bootstrap, "_read_bundled_tiku_text",
                        lambda _slug: json.dumps({"1": [new_q1, new_q2]}, ensure_ascii=False))
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": slug,
        "name": "Demo",
        "source_lang": "en",
        "role": "demo",
        "content_version": "2",
    })

    result = bootstrap.update_bundled(slug, decks_dir)

    deck2 = load_deck(decks_dir / slug)
    assert deck2.content_version == "2"
    tiku2 = store.load_tiku(deck2)
    revised = next(q for q in tiku2["1"] if q["id"] == q1_id)
    assert revised["marked"] is True            # mark preserved
    assert revised["marked_at"] == "2026-01-01T00:00:00"
    assert revised["user_note"] == "MY NOTE"    # personal note preserved (not clobbered by upstream)
    assert revised["topic"] == "old topic [fixed]"  # topic update applied
    assert result.updated >= 1
    backup_meta = json.loads((Path(result.backup_dir) / "meta.json").read_text(encoding="utf-8"))
    assert backup_meta["slug"] == slug
    assert backup_meta["content_version"] == "1"
    assert backup_meta["op"] == "update"


def test_update_bundled_migrates_legacy_positional_ids(tmp_path, monkeypatch):
    """A pre-UUID install (positional ids like demo-1-001) is bridged to UUIDs
    by content key, and wrong-index/marked keys are rewritten so history survives."""
    from flip import engine
    decks_dir = tmp_path / "decks"
    # Seed the local deck BY HAND with legacy positional ids (pre-UUID shape).
    local_tiku = {"1": [
        {"id": "demo-1-001", "topic": "legacy q", "options": ["A. x"], "answer": "A",
         "marked": True, "marked_at": "2026-01-01T00:00:00", "user_note": "legacy note"},
    ]}
    deck_dir = decks_dir / "demo"
    deck_dir.mkdir(parents=True)
    deck = bootstrap.Deck(slug="demo", name="Demo", path=deck_dir, source_lang="en")
    store.save_tiku(deck, local_tiku)
    engine._sync_marked_from_tiku(deck)

    # Bundled v1 ships the same question but with a UUID id.
    uuid = "q-deadbeef0001"
    import json
    monkeypatch.setattr(bootstrap, "_bundled_slugs", lambda: ["demo"])
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": "demo",
        "name": "Demo",
        "source_lang": "en",
        "role": "demo",
        "content_version": "1",
    })
    monkeypatch.setattr(bootstrap, "_read_bundled_tiku_text",
                        lambda _slug: json.dumps({"1": [
                            {"id": uuid, "topic": "legacy q", "options": ["A. x"], "answer": "A"},
                        ]}, ensure_ascii=False))
    _write_min_manifest(deck_dir)

    result = bootstrap.update_bundled("demo", decks_dir)

    # The local question's id should now be the UUID (migrated), and its mark
    # + note preserved.
    tiku2 = store.load_tiku(deck)
    assert tiku2["1"][0]["id"] == uuid
    assert tiku2["1"][0]["marked"] is True
    assert tiku2["1"][0]["user_note"] == "legacy note"
    # The marked.json index record key was rewritten to the UUID key.
    marked = store.load_marked(deck)
    uuid_key = engine.question_key("1", {"id": uuid})
    assert any(rec.get("key") == uuid_key for rec in marked)


def test_update_bundled_reports_unmigrated_when_content_changed(tmp_path, monkeypatch):
    """A legacy-id question whose topic also changed upstream can't be bridged
    by content key — it's reported as unmigrated (history orphaned, surfaced)."""
    import json
    decks_dir = tmp_path / "decks"
    local_tiku = {"1": [
        {"id": "demo-1-001", "topic": "old topic", "options": ["A. x"], "answer": "A"},
    ]}
    deck_dir = decks_dir / "demo"
    deck_dir.mkdir(parents=True)
    deck = bootstrap.Deck(slug="demo", name="Demo", path=deck_dir, source_lang="en")
    store.save_tiku(deck, local_tiku)

    monkeypatch.setattr(bootstrap, "_bundled_slugs", lambda: ["demo"])
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": "demo",
        "name": "Demo",
        "source_lang": "en",
        "role": "demo",
        "content_version": "1",
    })
    # Bundled ships the same id-slot but DIFFERENT content -> no content-key match.
    monkeypatch.setattr(bootstrap, "_read_bundled_tiku_text",
                        lambda _slug: json.dumps({"1": [
                            {"id": "q-deadbeef0001", "topic": "totally rewritten", "options": ["A. z"], "answer": "A"},
                        ]}, ensure_ascii=False))
    _write_min_manifest(deck_dir)

    result = bootstrap.update_bundled("demo", decks_dir)
    assert len(result.unmigrated) == 1
    assert result.unmigrated[0][1] == "demo-1-001"  # the orphaned legacy id


def test_read_bundled_metadata_reads_current_version_and_role(tmp_path, monkeypatch):
    root = tmp_path / "flip"
    slug_dir = root / "bundled_decks" / "demo"
    slug_dir.mkdir(parents=True)
    (slug_dir / "metadata.toml").write_text(
        '[deck]\nname = "Demo"\nslug = "demo"\nsource_lang = "en"\ncontent_version = "1.1"\n\n'
        '[explain]\nrole = "demo"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(bootstrap.resources, "files", lambda _pkg: root)

    meta = bootstrap._read_bundled_metadata("demo")

    assert meta["slug"] == "demo"
    assert meta["content_version"] == "1.1"
    assert meta["role"] == "demo"


def test_read_changelog_returns_manual_text(tmp_path, monkeypatch):
    root = tmp_path / "flip"
    slug_dir = root / "bundled_decks" / "demo"
    slug_dir.mkdir(parents=True)
    (slug_dir / "metadata.toml").write_text(
        '[deck]\nname = "Demo"\nslug = "demo"\nsource_lang = "en"\ncontent_version = "1.1"\n\n'
        '[explain]\nrole = "demo"\n',
        encoding="utf-8",
    )
    (slug_dir / "CHANGELOG.md").write_text(
        "# Changelog — Demo\n\n## 1.1\n\n修正 2 道题答案。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bootstrap.resources, "files", lambda _pkg: root)

    text = bootstrap.read_changelog("demo")

    assert "## 1.1" in text
    assert "修正 2 道题答案" in text


def test_version_lt_supports_semver_like_strings():
    assert bootstrap._version_lt("1.1", "1.2") is True
    assert bootstrap._version_lt("1.10", "1.2") is False
    assert bootstrap._version_lt("2", "v1.9") is False


def test_list_backups_reads_meta_json(tmp_path):
    backup_root = tmp_path / "backups"
    first = backup_root / "demo-update-20260623-120000"
    second = backup_root / "demo-prune-20260623-130000"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "meta.json").write_text(
        json.dumps({"slug": "demo", "content_version": "1", "op": "update", "timestamp": "20260623-120000"}),
        encoding="utf-8",
    )
    (second / "meta.json").write_text(
        json.dumps({"slug": "demo", "content_version": "2", "op": "prune", "timestamp": "20260623-130000"}),
        encoding="utf-8",
    )

    backups = bootstrap.list_backups(tmp_path / "decks", "demo")

    assert [item["content_version"] for item in backups] == ["2", "1"]
    assert backups[0]["op"] == "prune"
    assert backups[0]["path"].endswith("demo-prune-20260623-130000")


def test_list_backups_handles_missing_meta(tmp_path):
    backup_root = tmp_path / "backups"
    path = backup_root / "demo-update-20260623-120000"
    path.mkdir(parents=True)

    backups = bootstrap.list_backups(tmp_path / "decks", "demo")

    assert len(backups) == 1
    assert backups[0]["content_version"] == "未知"
    assert backups[0]["op"] == "unknown"


def test_switch_bundled_restores_old_version_preserving_notes(tmp_path, monkeypatch):
    decks_dir = tmp_path / "decks"
    slug = _install_fake_bundled(
        monkeypatch,
        decks_dir,
        questions=[{"id": "q-1", "topic": "new topic", "options": ["A. x"], "answer": "A"}],
        version="2",
    )
    deck = load_deck(decks_dir / slug)
    local = store.load_tiku(deck)
    local["1"][0]["user_note"] = "MY NOTE"
    store.save_tiku(deck, local)

    backup_dir = tmp_path / "backups" / "demo-update-20260623-120000"
    backup_dir.mkdir(parents=True)
    (backup_dir / "tiku.json").write_text(
        json.dumps({"1": [{"id": "q-1", "topic": "old topic", "options": ["A. x"], "answer": "A", "user_note": "maintainer"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_min_manifest(backup_dir, slug="demo", version="1")
    (backup_dir / "meta.json").write_text(
        json.dumps({"slug": "demo", "content_version": "1", "op": "update", "timestamp": "20260623-120000"}),
        encoding="utf-8",
    )

    result = bootstrap.switch_bundled("demo", decks_dir, backup_dir)

    deck2 = load_deck(decks_dir / slug)
    tiku2 = store.load_tiku(deck2)
    assert deck2.content_version == "1"
    assert tiku2["1"][0]["topic"] == "old topic"
    assert tiku2["1"][0]["user_note"] == "MY NOTE"
    assert result.updated >= 1
