"""Tests for deck drill history (history.json) and stats aggregation."""

from flip import store
from flip.engine import stats_snapshot


class TestAppendHistory:
    def test_first_append_creates_file(self, deck):
        assert not deck.history_path.exists()
        store.append_history(deck, {
            "date": "2026-06-22T11:00", "chapters": ["1", "2"],
            "total": 10, "incorrect": 2, "mode": "train",
        })
        history = store.load_history(deck)
        assert len(history) == 1
        assert history[0]["mode"] == "train"

    def test_second_append_preserves_first(self, deck):
        store.append_history(deck, {
            "date": "2026-06-22T11:00", "chapters": ["1"],
            "total": 5, "incorrect": 1, "mode": "train",
        })
        store.append_history(deck, {
            "date": "2026-06-22T14:00", "chapters": ["1"],
            "total": 5, "incorrect": 0, "mode": "review",
        })
        history = store.load_history(deck)
        assert len(history) == 2
        assert history[0]["mode"] == "train"
        assert history[1]["mode"] == "review"

    def test_load_history_missing_file_returns_empty(self, deck):
        # Fresh fixture has no history.json
        assert store.load_history(deck) == []

    def test_load_history_non_list_returns_empty(self, deck):
        # Defensive: a corrupted file (dict instead of list) shouldn't crash
        store.write_json(deck.history_path, {"not": "a list"})
        assert store.load_history(deck) == []


class TestStatsDrillsAggregation:
    def _record(self, chapters, mode="train"):
        return {
            "date": "2026-06-22T11:00",
            "chapters": chapters,
            "total": 10,
            "incorrect": 2,
            "mode": mode,
        }

    def test_no_history_means_zero_drills(self, deck):
        stats = stats_snapshot(deck)
        assert stats["drills_per_chapter"] == {}

    def test_single_record_counts_each_chapter(self, deck):
        store.append_history(deck, self._record(["1", "2"]))
        stats = stats_snapshot(deck)
        assert stats["drills_per_chapter"]["1"] == 1
        assert stats["drills_per_chapter"]["2"] == 1

    def test_multiple_records_accumulate(self, deck):
        store.append_history(deck, self._record(["5", "6"]))
        store.append_history(deck, self._record(["5", "7"]))
        store.append_history(deck, self._record(["5"]))
        stats = stats_snapshot(deck)
        # ch5 drilled 3 times, ch6 once, ch7 once
        assert stats["drills_per_chapter"]["5"] == 3
        assert stats["drills_per_chapter"]["6"] == 1
        assert stats["drills_per_chapter"]["7"] == 1

    def test_review_mode_also_counts(self, deck):
        store.append_history(deck, self._record(["1"], mode="review"))
        stats = stats_snapshot(deck)
        assert stats["drills_per_chapter"]["1"] == 1

    def test_drills_key_present_in_stats(self, deck):
        stats = stats_snapshot(deck)
        assert "drills_per_chapter" in stats

    def test_drills_do_not_affect_wrong_counts(self, deck):
        store.append_history(deck, self._record(["1"]))
        stats = stats_snapshot(deck)
        # Adding drills shouldn't change wrong_per_chapter
        assert stats["wrong_per_chapter"] == {}

    def test_chapter_keys_are_strings(self, deck):
        # Even if a record stored int chapters, aggregation normalizes to str
        store.append_history(deck, {
            "date": "x", "chapters": [1, 2],  # ints
            "total": 1, "incorrect": 0, "mode": "train",
        })
        stats = stats_snapshot(deck)
        assert "1" in stats["drills_per_chapter"]
        assert "2" in stats["drills_per_chapter"]


class TestRecordDrillHelper:
    def test_record_drill_builds_record_from_selected(self, deck):
        # Simulate what engine_loop._record_drill does, without the TUI.
        from flip.engine import SelectedSet
        q1 = {"topic": "t1", "options": ["A. x"], "answer": "A", "user_note": ""}
        q2 = {"topic": "t2", "options": ["A. x"], "answer": "A", "user_note": ""}
        selected = SelectedSet(
            [("1", q1), ("1", q2), ("2", q1)],
            input_is_index=False,
        )
        import datetime
        chapters = sorted({str(ch) for ch, _ in selected.questions})
        store.append_history(deck, {
            "date": datetime.datetime.now().isoformat(timespec="seconds"),
            "chapters": chapters,
            "total": len(selected.questions),
            "incorrect": 1,
            "mode": "train",
        })
        history = store.load_history(deck)
        assert len(history) == 1
        record = history[0]
        assert record["chapters"] == ["1", "2"]   # deduped + sorted
        assert record["total"] == 3
        assert record["mode"] == "train"
