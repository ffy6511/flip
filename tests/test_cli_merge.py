import json

from typer.testing import CliRunner

from flip import store
from flip.cli import app


def test_deck_merge_dry_run_does_not_write(deck, tmp_path):
    source = tmp_path / "incoming.json"
    source.write_text(
        json.dumps(
            {
                "1": [
                    {
                        "id": "incoming-1",
                        "topic": "New dry-run question",
                        "options": ["A. x", "B. y"],
                        "answer": "A",
                        "user_note": "",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = store.load_tiku(deck)

    result = CliRunner().invoke(app, ["deck", "merge", "example", str(source), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "added=1" in result.output
    assert "dry run: nothing written" in result.output
    assert store.load_tiku(deck) == before


def test_deck_merge_writes_backup_and_updates_tiku(deck, config, tmp_path):
    source = tmp_path / "incoming.json"
    source.write_text(
        json.dumps(
            {
                "2": [
                    {
                        "id": "example-2-003",
                        "topic": "3. A pentagon option E sanity check.",
                        "options": ["A. one", "B. two", "C. three", "D. four", "E. five"],
                        "answer": "E",
                        "user_note": "merged note",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["deck", "merge", "example", str(source), "--policy", "upsert"])

    assert result.exit_code == 0, result.output
    assert "updated=1" in result.output
    assert "backup:" in result.output
    assert (config.home / "backups").is_dir()
    assert store.load_tiku(deck)["2"][2]["user_note"] == "merged note"
