import json

from typer.testing import CliRunner

from flip import engine, store
from flip.cli import app


def test_deck_repair_dry_run_does_not_rewrite_marked(deck):
    stale_marked = [
        {
            "key": "{\"id\": \"old\"}",
            "chapter": "1",
            "topic": "old topic",
            "marked_at": "2026-01-01T00:00:00",
        }
    ]
    store.save_marked(deck, stale_marked)

    result = CliRunner().invoke(app, ["deck", "repair", "example", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "repair preview: example" in result.output
    assert "marked: rebuild 1 records from tiku marked flags" in result.output
    assert "dry run: nothing written" in result.output
    assert store.load_marked(deck) == stale_marked


def test_deck_repair_rebuilds_marked_from_tiku(deck):
    store.save_marked(
        deck,
        [
            {
                "key": "{\"id\": \"old\"}",
                "chapter": "1",
                "topic": "old topic",
                "marked_at": "2026-01-01T00:00:00",
            }
        ],
    )

    result = CliRunner().invoke(app, ["deck", "repair", "example"])

    assert result.exit_code == 0, result.output
    assert "repaired deck example" in result.output
    assert "marked.json rebuilt: 1 records" in result.output

    marked = store.load_marked(deck)
    assert marked == [
        {
            "key": json.dumps({"id": "example-1-002"}, ensure_ascii=False, sort_keys=True),
            "chapter": "1",
            "topic": "2. Which are prime? (multiple)",
            "marked_at": "2026-06-20T10:00:00",
        }
    ]


def test_deck_repair_checks_wrong_without_rewriting(deck):
    data = store.load_tiku(deck)
    q = data["1"][0]
    wrong_file = deck.wrong_dir / "ch1.json"
    wrong_records = [
        {
            "key": engine.question_key("1", q),
            "chapter": "1",
            "topic": q["topic"],
            "wrong_input": "A",
            "wrong_answer": "A",
            "wrong_at": "2026-01-01T00:00:00",
        },
        {
            "key": json.dumps({"id": "missing"}, ensure_ascii=False, sort_keys=True),
            "chapter": "9",
            "topic": "stale topic",
            "wrong_input": "B",
            "wrong_answer": "B",
            "wrong_at": "2026-01-02T00:00:00",
        },
    ]
    store.write_json(wrong_file, wrong_records)

    result = CliRunner().invoke(app, ["deck", "repair", "example"])

    assert result.exit_code == 0, result.output
    assert "wrong: files=1, records=2, resolvable=1, stale=1" in result.output
    assert store.read_json(wrong_file) == wrong_records


def test_doctor_reports_missing_ids_and_fix_command(flip_home):
    deck_dir = flip_home / "decks" / "noid"
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

    result = CliRunner().invoke(app, ["doctor", "noid"])

    assert result.exit_code == 0, result.output
    assert "doctor: noid" in result.output
    assert "missing ids: 2" in result.output
    assert "fix: flip deck migrate noid --ids" in result.output
