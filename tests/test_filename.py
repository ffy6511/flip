from flip.store import _result_prefix_for_questions


def _records(chapters):
    """Build (chapter, q) pairs from a list of chapter labels."""
    return [(str(ch), {"topic": "x", "answer": "A", "options": ["A. y"]}) for ch in chapters]


class TestResultPrefix:
    def test_single_numeric(self):
        assert _result_prefix_for_questions(_records(["5"])) == "ch5"

    def test_contiguous_range(self):
        assert _result_prefix_for_questions(_records(["5", "6", "7"])) == "ch5_7"

    def test_discrete_numeric(self):
        assert _result_prefix_for_questions(_records(["3", "5", "8"])) == "ch3_5_8"

    def test_non_numeric(self):
        assert _result_prefix_for_questions(_records(["appA"])) == "chappA"

    def test_non_numeric_multiple(self):
        assert _result_prefix_for_questions(_records(["appA", "appB"])) == "chappA_appB"

    def test_empty(self):
        assert _result_prefix_for_questions([]) == "ch_unknown"

    def test_duplicates_deduped(self):
        assert _result_prefix_for_questions(_records(["5", "5", "5"])) == "ch5"
