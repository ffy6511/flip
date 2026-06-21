"""flip engine: deck-agnostic quiz loop.

Behavior preserved from se_regressor.py except where deck manifest / global
config replaces hardcoded assumptions:

- parse_answer takes the deck's answer_alphabet (digits 1..N -> A..N)
- no hidden E-option filtering; all options are visible
- translation UI gated by config.translation_enabled
- AI explanation prompt built from deck.explain
- all storage resolved through store.* keyed by a Deck
"""

import datetime
import json
import random

from . import store
from . import translate
from . import explain as explain_mod
from .config import Config
from .deck import Deck


# ---- pure helpers (also unit-tested) ----

def question_key(chapter, q):
    return json.dumps(
        {
            "chapter": str(chapter),
            "topic": q.get("topic", ""),
            "answer": q.get("answer", ""),
            "options": q.get("options", []),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def parse_answer(s, alphabet="ABCD"):
    """Parse a learner input into a sorted, deduped letter string.

    Digits 1..N map to A..N per `alphabet`; existing letters are kept if they
    are in the alphabet. e.g. parse_answer("bd", "ABCD") == "BD",
    parse_answer("12", "ABCD") == "AB".
    """
    s = str(s).upper()
    letters = []
    for c in s:
        if c.isdigit():
            idx = int(c) - 1
            if 0 <= idx < len(alphabet):
                letters.append(alphabet[idx])
        elif c in alphabet:
            letters.append(c)
    return "".join(sorted(set(letters)))


def chapter_selector(selector, max_chapters):
    if selector is None:
        return set(range(1, max_chapters + 1))
    selector = str(selector).strip()
    if '-' in selector and not selector.startswith('-'):
        start, end = selector.split('-', 1)
        start = int(start)
        end = int(end)
        if start > end:
            raise ValueError("chapter range start cannot be greater than end")
        return set(range(start, end + 1))
    num_list = int(selector)
    if num_list <= 0:
        end = -num_list
        if end == 0:
            end = max_chapters
        return set(range(1, end + 1))
    return {num_list}


def iter_question_records(data):
    """Yield (chapter_str, question) from either dict or list shape."""
    if isinstance(data, dict):
        for chapter, question_list in data.items():
            for q in question_list:
                yield str(chapter), q
    elif isinstance(data, list):
        for item in data:
            try:
                chapter, q = _question_payload(item)
            except Exception:
                continue
            if isinstance(q, dict):
                yield str(chapter), q


def _question_payload(item):
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return item[0], item[1]
    raise ValueError("not a payload pair")


def _selected_chapters_for_records(selector, records):
    if selector is None:
        return None
    numeric_chapters = [
        int(chapter) for chapter, _ in records if str(chapter).isdigit()
    ]
    max_chapters = max(numeric_chapters) if numeric_chapters else 0
    if max_chapters <= 0:
        return None
    return chapter_selector(selector, max_chapters)


def _records_from_index_data(data, deck, selector=None, source_file=None):
    """Resolve index records (marked/wrong JSON) back to live questions in tiku."""
    records = []
    sources = {}
    if not isinstance(data, list):
        return records, sources
    tiku_index = build_tiku_index(deck)
    for item in data:
        key = _index_key(item)
        resolved = tiku_index.get(key)
        if not resolved:
            continue
        chapter, q = resolved
        records.append((chapter, q))
        if source_file:
            sources.setdefault(key, set()).add(source_file)

    selected = _selected_chapters_for_records(selector, records)
    if selected is not None:
        kept = []
        kept_keys = set()
        for chapter, q in records:
            if str(chapter).isdigit() and int(chapter) in selected:
                kept.append((chapter, q))
                kept_keys.add(question_key(chapter, q))
        sources = {k: v for k, v in sources.items() if k in kept_keys}
        records = kept
    return records, sources


def _index_key(item):
    if isinstance(item, dict):
        return item.get("key")
    if isinstance(item, list):
        try:
            chapter, q = _question_payload(item)
        except Exception:
            return None
        if isinstance(q, dict):
            return question_key(chapter, q)
    return None


def _index_meta(item):
    if isinstance(item, dict):
        return {k: v for k, v in item.items() if k not in {"key", "chapter"}}
    if isinstance(item, list) and len(item) >= 3 and isinstance(item[2], dict):
        return dict(item[2])
    return {}


def build_tiku_index(deck):
    """Map question_key -> (chapter, q) for the whole deck."""
    data = store.load_tiku(deck)
    if data is None:
        return {}
    return {
        question_key(chapter, q): (str(chapter), q)
        for chapter, q in iter_question_records(data)
    }


# ---- marking ----

def is_marked(deck, chapter, q):
    if q.get("marked"):
        return True
    key = question_key(chapter, q)
    return any(_index_key(item) == key for item in store.load_marked(deck))


def _sync_marked_from_tiku(deck):
    data = store.load_tiku(deck)
    records = []
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for chapter, q in iter_question_records(data):
        if q.get("marked"):
            records.append({
                "key": question_key(chapter, q),
                "chapter": str(chapter),
                "marked_at": q.get("marked_at", now),
            })
    store.save_marked(deck, records)


def toggle_marked(deck, chapter, q):
    if q.get("marked"):
        q["marked"] = False
        q.pop("marked_at", None)
        store.save_tiku(deck, _deck_data_with(deck, chapter, q))
        _sync_marked_from_tiku(deck)
        return False
    q["marked"] = True
    q["marked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    store.save_tiku(deck, _deck_data_with(deck, chapter, q))
    _sync_marked_from_tiku(deck)
    return True


def _deck_data_with(deck, chapter, q):
    """Re-read tiku so the saved object reflects all in-memory mutations."""
    # Simplest correct behavior: engine always re-reads before save callers do.
    return store.load_tiku(deck)


# ---- note / explanation persistence ----

def save_question_field(deck, chapter, q):
    """Persist any mutation on q back into the deck's tiku.json.

    Since q is a reference into the in-memory tiku dict, we re-read, swap in the
    updated question by key, and write.
    """
    data = store.load_tiku(deck)
    if isinstance(data, dict):
        target_key = question_key(chapter, q)
        for ch, qlist in data.items():
            for i, existing in enumerate(qlist):
                if question_key(ch, existing) == target_key:
                    qlist[i] = q
                    store.save_tiku(deck, data)
                    return
    # list-shape or not found: best-effort write
    store.save_tiku(deck, data)


def ensure_ai_explanation(deck, chapter, q, extra_prompt="", force=False):
    from .tui.render import has_translation as _has_zh
    extra_prompt = (extra_prompt or "").strip()
    if q.get("ai_explanation") and not extra_prompt and not force:
        return q["ai_explanation"]
    model = deck.explain.resolve_model()
    prompt = explain_mod.build_prompt(
        deck, chapter, q, extra_prompt,
        with_translation=_has_zh(q),
    )
    explanation = explain_mod.run_codex_explanation(prompt, model=model)
    q["ai_explanation"] = explanation
    if extra_prompt:
        q["ai_explanation_user_prompt"] = extra_prompt
    else:
        q.pop("ai_explanation_user_prompt", None)
    q["ai_explanation_model"] = model
    q["ai_explanation_updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_question_field(deck, chapter, q)
    return explanation


# ---- filtering ----

def filter_questions(records, filters):
    if not filters:
        return records
    normalized = {f.lower() for f in filters}
    selected = []
    seen = set()
    for chapter, q in records:
        matched = False
        if "mark" in normalized or "marked" in normalized:
            matched = matched or bool(q.get("marked"))
        if "note" in normalized:
            matched = matched or bool(str(q.get("user_note", "")).strip())
        if "ai" in normalized or "codex" in normalized or "explain" in normalized:
            matched = matched or bool(str(q.get("ai_explanation", "")).strip())
        if matched:
            key = question_key(chapter, q)
            if key not in seen:
                selected.append((chapter, q))
                seen.add(key)
    return selected


# ---- record selection (the deck-aware port of pick_random_questions) ----

class SelectedSet:
    """Result of picking questions for an epoch/review."""
    def __init__(self, questions, *, input_is_index, index_sources=None):
        self.questions = questions
        self.input_is_index = input_is_index
        self.index_sources = index_sources or {}


def pick_questions(deck, config, selector=None, shuffle=True, filters=None,
                   source=None):
    """Pick questions for a run.

    source: 'tiku' to train on the full bank (default), 'wrong' to review the
    wrong-index directory. Filters apply in both modes.
    """
    filters = filters or []
    build_tiku_index  # ensure symbol referenced
    index_sources = {}

    if source == "wrong":
        records = []
        for path in store.wrong_files(deck):
            data = store.read_json(path, default=[])
            file_records, file_sources = _records_from_index_data(
                data, deck, selector, source_file=str(path)
            )
            for k, s in file_sources.items():
                index_sources.setdefault(k, set()).update(s)
            seen = set()
            for chapter, q in file_records:
                k = question_key(chapter, q)
                if k in seen:
                    continue
                seen.add(k)
                records.append((chapter, q))
        records = filter_questions(records, filters)
        ordered = random.sample(records, len(records)) if shuffle else records
        return SelectedSet(ordered, input_is_index=True, index_sources=index_sources)

    # source == tiku
    data = store.load_tiku(deck)
    records = records_from_data(data, selector)
    records = filter_questions(records, filters)
    ordered = random.sample(records, len(records)) if shuffle else records
    return SelectedSet(ordered, input_is_index=False, index_sources={})


def records_from_data(data, selector=None):
    records = []
    if isinstance(data, dict):
        selected = chapter_selector(selector, len(data)) if selector else None
        for ch, qlist in data.items():
            if selected is not None and not str(ch).isdigit():
                continue
            if selected is not None and int(ch) not in selected:
                continue
            for q in qlist:
                records.append((str(ch), q))
    elif isinstance(data, list):
        records, _ = _records_from_index_data(data, None, selector)
    return records


def remove_from_active_index(selected, chapter, q):
    key = question_key(chapter, q)
    source_files = selected.index_sources.get(key, set())
    removed = False
    for path in list(source_files):
        removed = _remove_key_from_index_file(path, key) or removed
    return removed


def _remove_key_from_index_file(path, remove_key):
    data = store.read_json(path, default=None)
    if not isinstance(data, list):
        return False
    kept = [item for item in data if _index_key(item) != remove_key]
    if len(kept) == len(data):
        return False
    store.write_json(path, kept)
    return True


# ---- wrong-record writer ----

def incorrect_record(chapter, q, wrong_input, alphabet):
    return {
        "key": question_key(chapter, q),
        "chapter": str(chapter),
        "wrong_input": wrong_input,
        "wrong_answer": parse_answer(str(wrong_input), alphabet),
        "wrong_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


# ---- stats ----

def stats_snapshot(deck):
    """Aggregate counts for the stats screen."""
    data = store.load_tiku(deck) or {}
    questions = [(ch, q) for ch, q in iter_question_records(data)]
    wrong_questions = []
    for path in store.wrong_files(deck):
        file_data = store.read_json(path, default=[])
        # wrong files are index records (list shape) — resolve via the tiku index.
        resolved, _ = _records_from_index_data(file_data, deck)
        wrong_questions.extend(resolved)

    wrong_keys = set()
    wrong_per_chapter = {}
    for ch, q in wrong_questions:
        k = question_key(ch, q)
        if k in wrong_keys:
            continue
        wrong_keys.add(k)
        wrong_per_chapter[ch] = wrong_per_chapter.get(ch, 0) + 1

    per_chapter = {}
    for ch, _ in questions:
        per_chapter[ch] = per_chapter.get(ch, 0) + 1

    return {
        "total": len(questions),
        "chapters": len(per_chapter),
        "marked": sum(1 for _, q in questions if q.get("marked")),
        "note": sum(1 for _, q in questions if str(q.get("user_note", "")).strip()),
        "ai": sum(1 for _, q in questions if str(q.get("ai_explanation", "")).strip()),
        "wrong": len(wrong_keys),
        "wrong_files": len(store.wrong_files(deck)),
        "per_chapter": per_chapter,
        "wrong_per_chapter": wrong_per_chapter,
    }
