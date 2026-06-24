"""Deck repair helpers for rebuilding derived indexes from tiku.json."""

import datetime
from dataclasses import dataclass, field

from . import engine, store
from .importers import validate_tiku


@dataclass
class WrongCheck:
    files: int = 0
    records: int = 0
    resolvable: int = 0
    stale: int = 0


@dataclass
class RepairPlan:
    tiku_errors: list[str] = field(default_factory=list)
    question_count: int = 0
    chapter_count: int = 0
    marked_records: list[dict] = field(default_factory=list)
    wrong: WrongCheck = field(default_factory=WrongCheck)

    @property
    def ok(self):
        return not self.tiku_errors


def build_repair_plan(deck):
    """Inspect a deck and prepare derived-index repairs without writing."""
    data = store.load_tiku(deck)
    plan = RepairPlan(tiku_errors=validate_tiku(data))
    if plan.tiku_errors:
        return plan

    records = list(engine.iter_question_records(data))
    plan.question_count = len(records)
    plan.chapter_count = len({chapter for chapter, _ in records})
    plan.marked_records = _marked_records_from_tiku(records)
    plan.wrong = _check_wrong_records(deck)
    return plan


def apply_repair_plan(deck, plan):
    """Write repairable derived indexes and remove stale wrong-index records."""
    if not plan.ok:
        raise ValueError("cannot apply repair plan with invalid tiku")
    store.save_marked(deck, plan.marked_records)
    return _remove_stale_wrong_records(deck)


def _marked_records_from_tiku(records):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    out = []
    for chapter, q in records:
        if q.get("marked"):
            out.append({
                "key": engine.question_key(chapter, q),
                "chapter": str(chapter),
                "topic": q.get("topic", ""),
                "marked_at": q.get("marked_at", now),
            })
    return out


def _check_wrong_records(deck):
    index = engine.build_tiku_index(deck)
    result = WrongCheck(files=len(store.wrong_files(deck)))
    for path in store.wrong_files(deck):
        data = store.read_json(path, default=[])
        if not isinstance(data, list):
            continue
        for item in data:
            result.records += 1
            if engine._index_key(item) in index:
                result.resolvable += 1
            else:
                result.stale += 1
    return result


def _remove_stale_wrong_records(deck):
    index = engine.build_tiku_index(deck)
    removed = 0
    for path in store.wrong_files(deck):
        data = store.read_json(path, default=[])
        if not isinstance(data, list):
            continue
        kept = []
        for item in data:
            if engine._index_key(item) in index:
                kept.append(item)
            else:
                removed += 1
        if len(kept) != len(data):
            store.write_json(path, kept)
    return removed
