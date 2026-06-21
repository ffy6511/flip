"""Merge question-bank data into an existing deck."""

import copy
from dataclasses import dataclass, field

from . import engine


POLICIES = {"append", "upsert", "overwrite"}
PRESERVED_FIELDS = {
    "marked",
    "marked_at",
    "user_note",
    "zh",
    "ai_explanation",
    "ai_explanation_user_prompt",
    "ai_explanation_model",
    "ai_explanation_updated_at",
}


@dataclass
class MergeResult:
    data: dict
    added: int = 0
    updated: int = 0
    skipped: int = 0
    assigned_ids: int = 0
    title_updates: int = 0
    conflicts: list = field(default_factory=list)

    @property
    def changed(self):
        return bool(self.added or self.updated or self.assigned_ids or self.title_updates)


def merge_tiku(base_data, incoming_data, *, policy="append", prefix=None):
    """Merge incoming tiku data into base tiku data.

    `append` only adds new questions.
    `upsert` updates existing questions when ids match.
    `overwrite` also allows same-topic replacements when ids are absent.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown merge policy: {policy}")
    if not isinstance(base_data, dict):
        base_data = {}
    if not isinstance(incoming_data, dict):
        incoming_data = {}

    out = copy.deepcopy(base_data)
    incoming = copy.deepcopy(incoming_data)
    result = MergeResult(data=out)
    result.assigned_ids += engine.ensure_question_ids(out, prefix=prefix)

    _merge_chapter_titles(out, incoming, result)

    used_ids = {
        qid for _, q in engine.iter_question_records(out)
        for qid in [engine.question_id(q)] if qid
    }
    target_by_id, target_by_content, target_by_topic = _build_target_maps(out)

    for chapter, incoming_q in engine.iter_question_records(incoming):
        match = _find_match(chapter, incoming_q, target_by_id, target_by_content, target_by_topic)
        if match is None:
            result.assigned_ids += _ensure_one_id(incoming_q, used_ids, prefix, chapter)
            out.setdefault(str(chapter), []).append(incoming_q)
            _register_question(chapter, incoming_q, out[str(chapter)], len(out[str(chapter)]) - 1,
                               target_by_id, target_by_content, target_by_topic)
            result.added += 1
            continue

        match_kind, target_chapter, target_index, existing_q = match
        if _same_question_payload(existing_q, incoming_q):
            result.skipped += 1
            continue

        if policy == "append":
            result.conflicts.append(_conflict_message(chapter, incoming_q, match_kind, "append"))
            continue
        if policy == "upsert" and match_kind != "id":
            result.conflicts.append(_conflict_message(chapter, incoming_q, match_kind, "upsert"))
            continue

        merged_q = _merged_question(existing_q, incoming_q)
        out[target_chapter][target_index] = merged_q
        target_by_id, target_by_content, target_by_topic = _build_target_maps(out)
        result.updated += 1

    return result


def _merge_chapter_titles(out, incoming, result):
    titles = incoming.get("_chapter_titles")
    if not isinstance(titles, dict):
        return
    target = out.setdefault("_chapter_titles", {})
    if not isinstance(target, dict):
        target = {}
        out["_chapter_titles"] = target
    for chapter, title in titles.items():
        chapter = str(chapter)
        title = str(title)
        if target.get(chapter) != title:
            target[chapter] = title
            result.title_updates += 1


def _build_target_maps(data):
    by_id = {}
    by_content = {}
    by_topic = {}
    for chapter, qlist in data.items():
        if str(chapter).startswith("_") or not isinstance(qlist, list):
            continue
        for index, q in enumerate(qlist):
            if isinstance(q, dict):
                _register_question(chapter, q, qlist, index, by_id, by_content, by_topic)
    return by_id, by_content, by_topic


def _register_question(chapter, q, qlist, index, by_id, by_content, by_topic):
    chapter = str(chapter)
    qid = engine.question_id(q)
    if qid:
        by_id[qid] = (chapter, index, q)
    by_content[engine.content_question_key(chapter, q)] = (chapter, index, q)
    topic = str(q.get("topic", "")).strip()
    if topic:
        by_topic[(chapter, topic)] = (chapter, index, q)


def _find_match(chapter, q, by_id, by_content, by_topic):
    qid = engine.question_id(q)
    if qid and qid in by_id:
        return ("id",) + by_id[qid]
    content_key = engine.content_question_key(chapter, q)
    if content_key in by_content:
        return ("content",) + by_content[content_key]
    topic = str(q.get("topic", "")).strip()
    if topic and (str(chapter), topic) in by_topic:
        return ("topic",) + by_topic[(str(chapter), topic)]
    return None


def _ensure_one_id(q, used_ids, prefix, chapter):
    qid = engine.question_id(q)
    if qid:
        used_ids.add(qid)
        return 0
    index = 1
    qid = engine._generated_question_id(prefix, chapter, index)
    while qid in used_ids:
        index += 1
        qid = engine._generated_question_id(prefix, chapter, index)
    q["id"] = qid
    used_ids.add(qid)
    return 1


def _same_question_payload(a, b):
    keys = ("topic", "options", "answer", "zh", "user_note", "ai_explanation")
    return all(a.get(k) == b.get(k) for k in keys if k in a or k in b)


def _merged_question(existing, incoming):
    merged = copy.deepcopy(incoming)
    if engine.question_id(existing) and not engine.question_id(merged):
        merged["id"] = engine.question_id(existing)
    for field in PRESERVED_FIELDS:
        existing_value = existing.get(field)
        incoming_value = merged.get(field)
        if existing_value in (None, ""):
            continue
        if field in {"marked", "marked_at"}:
            merged[field] = existing_value
            continue
        if incoming_value in (None, "", [], {}):
            merged[field] = existing_value
    return merged


def _conflict_message(chapter, q, match_kind, policy):
    qid = engine.question_id(q) or "-"
    topic = str(q.get("topic", "")).strip()[:80]
    return f"chapter {chapter}: {match_kind} match conflicts under {policy} policy (id={qid}, topic={topic!r})"
