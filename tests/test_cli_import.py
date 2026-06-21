from pathlib import Path

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
