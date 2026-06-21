from flip.engine import question_key


def _make(topic="t", answer="A", options=None, chapter="1"):
    return {
        "chapter": str(chapter),
        "topic": topic,
        "answer": answer,
        "options": options or ["A. x"],
    }


class TestQuestionKey:
    def test_same_question_same_key(self):
        q = _make(topic="hello", answer="B", options=["A. 1", "B. 2"])
        assert question_key("1", q) == question_key("1", q)

    def test_different_chapter_different_key(self):
        q = _make()
        assert question_key("1", q) != question_key("2", q)

    def test_different_topic_different_key(self):
        assert question_key("1", _make(topic="a")) != question_key("1", _make(topic="b"))

    def test_different_answer_different_key(self):
        assert question_key("1", _make(answer="A")) != question_key("1", _make(answer="B"))

    def test_options_order_matters(self):
        # sort_keys=True sorts the *object* keys, not list contents — option
        # order inside the list is preserved, so different orders differ.
        q1 = _make(options=["A. 1", "B. 2"])
        q2 = _make(options=["B. 2", "A. 1"])
        assert question_key("1", q1) != question_key("1", q2)

    def test_key_is_string(self):
        assert isinstance(question_key("1", _make()), str)
