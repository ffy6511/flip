from flip.engine import filter_questions


def _q(marked=False, note="", ai=""):
    q = {"topic": "t", "answer": "A", "options": ["A. x"], "user_note": note}
    if marked:
        q["marked"] = True
    if ai:
        q["ai_explanation"] = ai
    return q


class TestFilterQuestions:
    def test_no_filter_returns_all(self):
        records = [("1", _q()), ("1", _q())]
        assert filter_questions(records, []) == records

    def test_marked_only(self):
        records = [("1", _q()), ("2", _q(marked=True))]
        result = filter_questions(records, ["mark"])
        assert len(result) == 1
        assert result[0][0] == "2"

    def test_note_only(self):
        records = [("1", _q(note="hi")), ("2", _q())]
        result = filter_questions(records, ["note"])
        assert len(result) == 1
        assert result[0][0] == "1"

    def test_ai_only(self):
        records = [("1", _q(ai="explanation")), ("2", _q())]
        result = filter_questions(records, ["ai"])
        assert len(result) == 1
        assert result[0][0] == "1"

    def test_multiple_filters_or_semantics(self):
        # mark OR note
        records = [("1", _q(marked=True)), ("2", _q(note="x")), ("3", _q())]
        result = filter_questions(records, ["mark", "note"])
        assert {r[0] for r in result} == {"1", "2"}

    def test_dedup_by_key(self):
        q = _q(marked=True)
        records = [("1", q), ("1", q)]  # same object, same key
        result = filter_questions(records, ["mark"])
        assert len(result) == 1

    def test_filter_aliases(self):
        # "codex" and "explain" are aliases for "ai"
        records = [("1", _q(ai="x"))]
        assert len(filter_questions(records, ["codex"])) == 1
        assert len(filter_questions(records, ["explain"])) == 1

    def test_case_insensitive(self):
        records = [("1", _q(marked=True))]
        assert len(filter_questions(records, ["MARK"])) == 1
