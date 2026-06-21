from flip.engine import (
    build_tiku_index,
    content_question_key,
    ensure_question_ids,
    question_key,
    question_keys,
)
from flip import store


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

    def test_id_overrides_chapter_and_content(self):
        q1 = _make(topic="a", answer="A", chapter="1")
        q2 = _make(topic="b", answer="B", chapter="2", options=["A. x", "B. y"])
        q1["id"] = "stable-001"
        q2["id"] = "stable-001"
        assert question_key("1", q1) == question_key("2", q2)

    def test_question_keys_include_legacy_content_alias(self):
        q = _make(topic="hello")
        q["id"] = "stable-001"
        keys = question_keys("1", q)
        assert question_key("1", q) in keys
        assert content_question_key("1", q) in keys

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


class TestEnsureQuestionIds:
    def test_fills_missing_ids_in_place(self):
        data = {
            "1": [_make(topic="a"), _make(topic="b")],
            "2": [_make(topic="c")],
        }
        assert ensure_question_ids(data, prefix="demo") == 3
        assert [q["id"] for q in data["1"]] == ["demo-1-001", "demo-1-002"]
        assert data["2"][0]["id"] == "demo-2-001"

    def test_preserves_existing_ids_and_avoids_collisions(self):
        data = {
            "1": [
                dict(_make(topic="a"), id="demo-1-001"),
                _make(topic="b"),
            ],
        }
        assert ensure_question_ids(data, prefix="demo") == 1
        assert data["1"][1]["id"] == "demo-1-002"

    def test_build_index_resolves_legacy_key_after_id_added(self, deck):
        data = store.load_tiku(deck)
        q = data["1"][0]
        legacy_key = content_question_key("1", q)
        q["id"] = "example-1-001"
        store.save_tiku(deck, data)

        index = build_tiku_index(deck)

        assert index[question_key("1", q)] == ("1", q)
        assert index[legacy_key] == ("1", q)
