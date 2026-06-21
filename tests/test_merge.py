from flip.merge import merge_tiku


def _q(qid=None, topic="t", answer="A", note=""):
    q = {
        "topic": topic,
        "options": ["A. x", "B. y"],
        "answer": answer,
        "user_note": note,
    }
    if qid:
        q["id"] = qid
    return q


def test_append_adds_new_question_and_assigns_id():
    base = {"1": [_q("demo-1-001", "old")]}
    incoming = {"1": [_q(topic="new")]}

    result = merge_tiku(base, incoming, policy="append", prefix="demo")

    assert result.added == 1
    assert result.assigned_ids == 1
    assert result.data["1"][1]["id"] == "demo-1-002"


def test_append_conflicts_on_same_id_with_different_content():
    base = {"1": [_q("q1", "old")]}
    incoming = {"1": [_q("q1", "new")]}

    result = merge_tiku(base, incoming, policy="append", prefix="demo")

    assert result.added == 0
    assert result.conflicts


def test_upsert_updates_by_id_and_preserves_user_state():
    base = {
        "1": [
            dict(
                _q("q1", "old", note="keep note"),
                marked=True,
                marked_at="2026-06-21T10:00:00",
                ai_explanation="keep explanation",
            )
        ]
    }
    incoming = {"1": [_q("q1", "new", answer="B", note="")]}

    result = merge_tiku(base, incoming, policy="upsert", prefix="demo")
    q = result.data["1"][0]

    assert result.updated == 1
    assert q["topic"] == "new"
    assert q["answer"] == "B"
    assert q["user_note"] == "keep note"
    assert q["marked"] is True
    assert q["marked_at"] == "2026-06-21T10:00:00"
    assert q["ai_explanation"] == "keep explanation"


def test_upsert_conflicts_on_same_topic_without_id():
    base = {"1": [_q("q1", "same topic", answer="A")]}
    incoming = {"1": [_q(topic="same topic", answer="B")]}

    result = merge_tiku(base, incoming, policy="upsert", prefix="demo")

    assert result.updated == 0
    assert result.conflicts


def test_overwrite_can_update_same_topic_without_id():
    base = {"1": [_q("q1", "same topic", answer="A")]}
    incoming = {"1": [_q(topic="same topic", answer="B")]}

    result = merge_tiku(base, incoming, policy="overwrite", prefix="demo")

    assert result.updated == 1
    assert result.data["1"][0]["id"] == "q1"
    assert result.data["1"][0]["answer"] == "B"


def test_merge_fills_missing_ids_in_existing_deck():
    base = {"1": [_q(topic="old")]}
    incoming = {"2": [_q(topic="new")]}

    result = merge_tiku(base, incoming, policy="append", prefix="demo")

    assert result.assigned_ids == 2
    assert result.data["1"][0]["id"] == "demo-1-001"
    assert result.data["2"][0]["id"] == "demo-2-001"


def test_chapter_titles_are_merged():
    base = {"_chapter_titles": {"1": "Old"}, "1": [_q("q1", "old")]}
    incoming = {"_chapter_titles": {"1": "New", "2": "Added"}, "2": [_q("q2", "new")]}

    result = merge_tiku(base, incoming, policy="append", prefix="demo")

    assert result.title_updates == 2
    assert result.data["_chapter_titles"] == {"1": "New", "2": "Added"}
