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
import re

from . import store
from . import translate
from . import explain as explain_mod
from .config import Config
from .deck import Deck


# ---- pure helpers (also unit-tested) ----

def question_id(q):
    """Return a normalized stable question id, or None when absent."""
    qid = q.get("id") if isinstance(q, dict) else None
    if qid is None:
        return None
    qid = str(qid).strip()
    return qid or None


def content_question_key(chapter, q):
    """Legacy content-addressed key used before stable question ids existed."""
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


def question_key(chapter, q):
    """Stable identity string for a question, used as the join key across
    tiku.json, marked.json, and wrong/*.json.

    Newer decks identify questions by their explicit `id`, so edits to
    topic/options/answer do not orphan marks or wrong-history. Older decks
    without `id` fall back to the legacy content-addressed key for compatibility.
    """
    qid = question_id(q)
    if qid:
        return json.dumps({"id": qid}, ensure_ascii=False, sort_keys=True)
    return content_question_key(chapter, q)


def question_keys(chapter, q):
    """All keys that may identify a question.

    The first key is the canonical key to write now. Extra keys are legacy
    aliases so old index files still resolve after ids are added to tiku.json.
    """
    keys = [question_key(chapter, q)]
    legacy = content_question_key(chapter, q)
    if legacy not in keys:
        keys.append(legacy)
    return keys


def _id_part(value):
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "x"


def _generated_question_id(prefix, chapter, index):
    base = _id_part(prefix) if prefix else "q"
    return f"{base}-{_id_part(chapter)}-{index:03d}"


def ensure_question_ids(data, *, prefix=None):
    """Fill missing question `id` fields in-place and return how many were added."""
    if not isinstance(data, dict):
        return 0
    used = set()
    for _, q in iter_question_records(data):
        qid = question_id(q)
        if qid:
            used.add(qid)

    added = 0
    counters = {}
    for chapter, q in iter_question_records(data):
        if question_id(q):
            continue
        counters[chapter] = counters.get(chapter, 0) + 1
        index = counters[chapter]
        candidate = _generated_question_id(prefix, chapter, index)
        while candidate in used:
            index += 1
            candidate = _generated_question_id(prefix, chapter, index)
        counters[chapter] = index
        q["id"] = candidate
        used.add(candidate)
        added += 1
    return added


def parse_answer(s, alphabet="ABCD"):
    """Normalize a learner's answer input into a sorted, deduped letter string.

    Accepts a free-form mix of two conventions and unifies them:

    - Digits `1..N`: positional shortcuts (1 -> first option). Mapped through
      `alphabet`, so `parse_answer("1", "ABCDE") == "A"` and `parse_answer("5",
      "ABCDE") == "E"`. A digit beyond the alphabet length is silently
      ignored (no crash on stray input).
    - Letters already in `alphabet`: kept as-is.

    The result is always sorted and deduped, so `"21"` and `"12"` and `"AA2"`
    all collapse to `"AB"`. Multi-select answers (e.g. `"AC"`) survive intact.

    `alphabet` comes from the deck manifest (`answer_alphabet`); this is what
    makes a 5-option deck work without special-casing E.
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
    """Resolve a chapter selector expression into a set of 1-based chapter nums.

    Accepted forms (all stringly-typed from CLI/menu):
      - `None`  -> all chapters `{1..max_chapters}`
      - `"5"`   -> `{5}`
      - `"5-10"`-> `{5,6,7,8,9,10}` (inclusive both ends)
      - `"-3"`  -> first 3 chapters `{1,2,3}` (negative means "first N")
      - `"-0"`  -> `{1..max_chapters}` (0 means "all")
      - `"5,3-4"`-> `{3,4,5}` (comma-separated segments are unioned; dups drop)

    Raises ValueError if a range's start exceeds its end. Non-numeric chapter
    labels (e.g. "appA") are handled elsewhere — this function only deals with
    the numeric selection layer.
    """
    if selector is None:
        return set(range(1, max_chapters + 1))
    selector = str(selector).strip()
    # Comma-separated segments: union each segment's resolved set. This lets
    # users combine ranges and singles, e.g. "5,3-4" -> {3,4,5}.
    if "," in selector:
        out = set()
        for seg in selector.split(","):
            seg = seg.strip()
            if seg:
                out |= chapter_selector(seg, max_chapters)
        return out
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


def _is_chapter_key(key):
    """True if a top-level tiku key is a real chapter (not metadata).

    Keys starting with `_` are reserved for non-chapter metadata (e.g.
    `_chapter_titles`); they are skipped by record iteration and validation.
    """
    return not str(key).startswith("_")


def iter_question_records(data):
    """Yield (chapter_str, question) pairs, accepting either tiku shape.

    tiku.json is canonically a dict `{chapter: [questions]}`, but the legacy
    se_regressor also produced/accepted list-shaped data (each item a
    `[chapter, question] pair). This iterator papers over both so callers
    don't need to branch on `isinstance` everywhere.

    Metadata keys (those starting with `_`, like `_chapter_titles`) are
    skipped — they are not chapters.
    """
    if isinstance(data, dict):
        for chapter, question_list in data.items():
            if not _is_chapter_key(chapter):
                continue
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
    """Resolve index-file records (marked.json / wrong/*.json) back to live
    questions in tiku.json.

    Index files do NOT store full questions — only `question_key` references
    (plus metadata like marked_at, wrong_answer). To get the actual question
    text/options we must look each key up in tiku via `build_tiku_index`.

    Returns (records, sources) where:
      - records: [(chapter, q)] pairs that exist in tiku (stale index entries
        pointing at edited/deleted questions are silently dropped).
      - sources: {question_key: {source_file, ...}} so a later `r` (remove)
        can delete the entry from every file it came from.

    `selector`, if given, further narrows to the selected numeric chapters.
    """
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
                kept_keys.update(question_keys(chapter, q))
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
    """Map every current/legacy question key -> (chapter, q) for the whole deck."""
    data = store.load_tiku(deck)
    if data is None:
        return {}
    index = {}
    for chapter, q in iter_question_records(data):
        for key in question_keys(chapter, q):
            index[key] = (str(chapter), q)
    return index


# ---- marking ----

def is_marked(deck, chapter, q):
    if q.get("marked"):
        return True
    keys = set(question_keys(chapter, q))
    return any(_index_key(item) in keys for item in store.load_marked(deck))


def _sync_marked_from_tiku(deck):
    data = store.load_tiku(deck)
    records = []
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for chapter, q in iter_question_records(data):
        if q.get("marked"):
            records.append({
                "key": question_key(chapter, q),
                "chapter": str(chapter),
                "topic": q.get("topic", ""),
                "marked_at": q.get("marked_at", now),
            })
    store.save_marked(deck, records)


def toggle_marked(deck, chapter, q):
    """Flip a question's marked flag, persisting to BOTH tiku.json and marked.json.

    Why two stores: tiku.json carries the authoritative `marked`/`marked_at`
    fields on each question (so a re-export preserves them), while marked.json
    is a slim index that `--filter mark` can scan without loading the whole
    tiku. They must stay in sync, hence _sync_marked_from_tiku rebuilds the
    index from tiku's `marked` flags after every toggle.

    Mutates `q` in place and returns the new boolean state.
    """
    if q.get("marked"):
        q["marked"] = False
        q.pop("marked_at", None)
        save_question_field(deck, chapter, q)
        _sync_marked_from_tiku(deck)
        return False
    q["marked"] = True
    q["marked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_question_field(deck, chapter, q)
    _sync_marked_from_tiku(deck)
    return True


# ---- note / explanation persistence ----

def save_question_field(deck, chapter, q):
    """Persist any mutation on q back into the deck's tiku.json.

    Since q is a reference into the in-memory tiku dict, we re-read, swap in the
    updated question by key, and write.
    """
    data = store.load_tiku(deck)
    if isinstance(data, dict):
        target_id = question_id(q)
        target_keys = set(question_keys(chapter, q))
        for ch, qlist in data.items():
            for i, existing in enumerate(qlist):
                if target_id and question_id(existing) == target_id:
                    qlist[i] = q
                    store.save_tiku(deck, data)
                    return
                if not target_id and question_key(ch, existing) in target_keys:
                    qlist[i] = q
                    store.save_tiku(deck, data)
                    return
    # list-shape or not found: best-effort write
    store.save_tiku(deck, data)


def ensure_ai_explanation(deck, config, chapter, q, extra_prompt="", force=False):
    from .tui.render import has_translation as _has_zh
    extra_prompt = (extra_prompt or "").strip()
    if q.get("ai_explanation") and not extra_prompt and not force:
        return q["ai_explanation"]
    # Deck's default model overrides the global default when set.
    deck_model = deck.explain.default_model or None
    prompt = explain_mod.build_prompt(
        deck, chapter, q, extra_prompt,
        with_translation=_has_zh(q),
    )
    explanation = explain_mod.run_explanation(prompt, config=config, model=deck_model)
    # Record which model actually ran (env-overridden, if any).
    used_model = deck.explain.resolve_model() if deck.explain.default_model else config.explain.model
    q["ai_explanation"] = explanation
    if extra_prompt:
        q["ai_explanation_user_prompt"] = extra_prompt
    else:
        q.pop("ai_explanation_user_prompt", None)
    q["ai_explanation_model"] = used_model
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
    """Pick the question set for one training/review run.

    The `source` argument forks into two very different code paths:

    - `source="tiku"` (default, training mode): read the deck's full tiku.json,
      filter by chapter selector and tags, shuffle. The returned SelectedSet
      is *not* an index — `input_is_index=False` — so a wrong-index file IS
      written at epoch end.

    - `source="wrong"` (review mode): aggregate every wrong/*.json index file,
      resolve each entry back to its live question via build_tiku_index, dedupe
      across files. `input_is_index=True`, so the TUI offers the `r` (remove)
      key and no new wrong file is written on exit.

    Filters (mark/note/ai) apply in both modes and are OR-combined: a question
    passes if it matches ANY active filter.
    """
    filters = filters or []
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
        numeric_chapters = [int(ch) for ch, _ in iter_question_records(data) if str(ch).isdigit()]
        max_chapter = max(numeric_chapters) if numeric_chapters else 0
        selected = chapter_selector(selector, max_chapter) if selector else None
        for ch, qlist in data.items():
            if not _is_chapter_key(ch):
                continue
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
    keys = set(question_keys(chapter, q))
    source_files = set()
    for key in keys:
        source_files.update(selected.index_sources.get(key, set()))
    removed = False
    for path in list(source_files):
        removed = _remove_keys_from_index_file(path, keys) or removed
    return removed


def _remove_keys_from_index_file(path, remove_keys):
    data = store.read_json(path, default=None)
    if not isinstance(data, list):
        return False
    kept = [item for item in data if _index_key(item) not in remove_keys]
    if len(kept) == len(data):
        return False
    store.write_json(path, kept)
    return True


# ---- wrong-record writer ----

def incorrect_record(chapter, q, wrong_input, alphabet):
    return {
        "key": question_key(chapter, q),
        "chapter": str(chapter),
        "topic": q.get("topic", ""),
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
