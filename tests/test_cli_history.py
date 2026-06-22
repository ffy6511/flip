from typer.testing import CliRunner

from flip import store
from flip.cli import app


def _record(mode):
    return {
        "date": "x",
        "chapters": ["1"],
        "total": 1,
        "incorrect": 0,
        "mode": mode,
    }


def test_deck_clear_count_train_preserves_review(deck):
    store.append_history(deck, _record("train"))
    store.append_history(deck, _record("review"))

    result = CliRunner().invoke(app, ["deck", "clear-count", "example", "--mode", "train"])

    assert result.exit_code == 0, result.output
    assert "cleared train count: example" in result.output
    assert [item["mode"] for item in store.load_history(deck)] == ["review"]


def test_deck_clear_count_review_preserves_train(deck):
    store.append_history(deck, _record("train"))
    store.append_history(deck, _record("review"))

    result = CliRunner().invoke(app, ["deck", "clear-count", "example", "--mode", "review"])

    assert result.exit_code == 0, result.output
    assert "cleared review count: example" in result.output
    assert [item["mode"] for item in store.load_history(deck)] == ["train"]


def test_deck_clear_count_all_clears_train_and_review(deck):
    store.append_history(deck, _record("train"))
    store.append_history(deck, _record("review"))

    result = CliRunner().invoke(app, ["deck", "clear-count", "example", "--mode", "all"])

    assert result.exit_code == 0, result.output
    assert "cleared all count: example" in result.output
    assert store.load_history(deck) == []
