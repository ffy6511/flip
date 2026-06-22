from pathlib import Path
import shutil

from typer.testing import CliRunner

from flip import store
from flip.cli import app
from flip.config import load_config
from flip.deck import load_deck


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DECK_SRC = REPO_ROOT / "decks" / "example"


def test_import_directory_syncs_inline_marked_questions(config):
    result = CliRunner().invoke(app, ["import", "imported", str(EXAMPLE_DECK_SRC), "--name", "Imported"])

    assert result.exit_code == 0, result.output

    cfg = load_config()
    deck = load_deck(cfg.decks_dir / "imported")
    marked = store.load_marked(deck)

    assert len(marked) == 1
    assert marked[0]["topic"] == "2. Which are prime? (multiple)"

    mark_result = CliRunner().invoke(app, ["deck", "mark", "imported"])
    assert mark_result.exit_code == 0, mark_result.output
    assert "1 marked:" in mark_result.output


def test_import_directory_migrates_history_and_session(config, tmp_path):
    src = tmp_path / "deck-src"
    src.mkdir()
    shutil.copyfile(EXAMPLE_DECK_SRC / "manifest.toml", src / "manifest.toml")
    shutil.copyfile(EXAMPLE_DECK_SRC / "tiku.json", src / "tiku.json")
    history = [{"date": "x", "chapters": ["1"], "total": 1, "incorrect": 0, "mode": "train"}]
    session = {"status": "paused", "source": "tiku", "questions": [], "answered": []}
    store.write_json(src / "history.json", history)
    store.write_json(src / "session.json", session)

    result = CliRunner().invoke(app, ["import", "stateful", str(src), "--name", "Stateful"])

    assert result.exit_code == 0, result.output
    assert "history.json" in result.output
    assert "session.json" in result.output

    cfg = load_config()
    deck = load_deck(cfg.decks_dir / "stateful")
    assert store.load_history(deck) == history
    assert store.load_session(deck) == session


def test_export_includes_history_and_session(deck, tmp_path):
    history = [{"date": "x", "chapters": ["1"], "total": 1, "incorrect": 0, "mode": "train"}]
    session = {"status": "paused", "source": "tiku", "questions": [], "answered": []}
    store.save_history(deck, history)
    store.save_session(deck, session)
    out = tmp_path / "example-export"

    result = CliRunner().invoke(app, ["export", "example", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert "history.json" in result.output
    assert "session.json" in result.output
    assert store.read_json(out / "history.json") == history
    assert store.read_json(out / "session.json") == session
