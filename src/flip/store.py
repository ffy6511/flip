"""Storage layer: JSON read/write keyed by deck.

Replaces the SCRIPT_DIR-relative globals in se_regressor.py. All paths resolve
through a Deck object, so the engine stays deck-agnostic.
"""

import json
import os
from pathlib import Path

from .deck import Deck, TIKU_NAME, MARKED_NAME, WRONG_DIR_NAME


def write_json(path, data):
    """Write JSON atomically-ish, UTF-8, 2-space indent, trailing newline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_json(path, default=None):
    """Read JSON, returning `default` on FileNotFoundError."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


# ---- tiku.json ----

def load_tiku(deck: Deck):
    return read_json(deck.tiku_path, default=None)


def save_tiku(deck: Deck, data):
    write_json(deck.tiku_path, data)


# ---- marked.json ----

def load_marked(deck: Deck):
    data = read_json(deck.marked_path, default=[])
    return data if isinstance(data, list) else []


def save_marked(deck: Deck, marked):
    write_json(deck.marked_path, marked)


# ---- history.json (drill session log) ----

def load_history(deck: Deck):
    """Return the deck's drill history as a list of records (empty if absent).

    Each record is {date, chapters, total, incorrect, mode}. Read-only; callers
    mutate via append_history.
    """
    data = read_json(deck.history_path, default=[])
    return data if isinstance(data, list) else []


def append_history(deck: Deck, record):
    """Append one drill record to history.json (read-modify-rewrite).

    Mirrors the marked.json convention (full overwrite, not true append/JSONL)
    for codebase consistency. History stays small (a few thousand records max)
    so the rewrite cost is negligible.

    `record` is constructed by the caller; this function only does IO.
    """
    history = load_history(deck)
    history.append(record)
    write_json(deck.history_path, history)


def save_history(deck: Deck, history):
    write_json(deck.history_path, history)


def clear_history_mode(deck: Deck, mode):
    kept = [
        record for record in load_history(deck)
        if record.get("mode") != mode
    ]
    save_history(deck, kept)


# ---- session.json (paused drill checkpoint) ----

def load_session(deck: Deck):
    data = read_json(deck.session_path, default=None)
    return data if isinstance(data, dict) else None


def save_session(deck: Deck, session):
    write_json(deck.session_path, session)


def clear_session(deck: Deck):
    try:
        Path(deck.session_path).unlink()
    except FileNotFoundError:
        pass


# ---- directory import (migrate a whole deck folder) ----

def import_dir(src_dir, deck: Deck):
    """Copy a legacy/external deck folder into a freshly created deck dir.

    `src_dir` must contain `tiku.json` (required). Optional siblings:
      - `marked.json`  -> copied verbatim to the deck's marked path
      - `wrong/`       -> copied verbatim into the deck's wrong dir

    The old `marked_questions.json` name is NOT recognized — callers must
    rename it to `marked.json` first. wrong-file records missing `wrong_at`
    are kept as-is (the engine never reads that field, only writes it).

    Returns a dict of what was copied, for the CLI to report.
    """
    src_dir = Path(src_dir)
    src_tiku = src_dir / "tiku.json"
    if not src_tiku.is_file():
        raise FileNotFoundError(f"tiku.json not found in {src_dir}")

    deck.path.mkdir(parents=True, exist_ok=True)

    # tiku.json is validated upstream by the caller; here we only relocate it.
    write_json(deck.tiku_path, read_json(src_tiku))

    copied = {"tiku": True, "marked": False, "wrong_files": 0}

    src_marked = src_dir / "marked.json"
    if src_marked.is_file():
        write_json(deck.marked_path, read_json(src_marked))
        copied["marked"] = True

    src_wrong = src_dir / "wrong"
    if src_wrong.is_dir():
        deck.wrong_dir.mkdir(parents=True, exist_ok=True)
        for path in json_files_in_directory(src_wrong):
            write_json(deck.wrong_dir / path.name, read_json(path))
            copied["wrong_files"] += 1

    return copied


def export_deck(deck: Deck, dest_dir):
    """Copy a deck's files into `dest_dir` (the inverse of import_dir).

    Bundles tiku.json, manifest.toml, marked.json (if present), and the
    whole wrong/ directory — everything needed to re-import the deck on
    another machine via `flip import <slug> <dir>`. Returns the destination
    Path. Creates dest_dir (and wrong/ as needed); refuses to overwrite an
    existing non-empty dest_dir.
    """
    import shutil

    dest_dir = Path(dest_dir)
    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise FileExistsError(f"destination not empty: {dest_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(deck.tiku_path, dest_dir / TIKU_NAME)
    shutil.copyfile(deck.manifest_path, dest_dir / "manifest.toml")
    if deck.marked_path.is_file():
        shutil.copyfile(deck.marked_path, dest_dir / MARKED_NAME)
    if deck.wrong_dir.is_dir():
        target_wrong = dest_dir / WRONG_DIR_NAME
        target_wrong.mkdir(parents=True, exist_ok=True)
        for path in json_files_in_directory(deck.wrong_dir):
            shutil.copyfile(path, target_wrong / path.name)
    return dest_dir


def index_summary(record):
    """Reduce an index record (from marked.json / wrong/*.json) to a readable line.

    The record's `key` is a serialized JSON of the question's content
    projection (chapter + topic + answer + options). This parses it back and
    returns (chapter, topic_text, extra) where extra is whatever non-key
    metadata the record carries (e.g. wrong_input, marked_at). Used by the
    `flip deck mark/wrong` read-only listings — and safe for agents to parse.
    """
    import json as _json
    chapter = record.get("chapter", "?")
    topic = record.get("topic", "")
    key = record.get("key", "")
    if not topic:
        try:
            parsed = _json.loads(key) if isinstance(key, str) else (key or {})
            topic = parsed.get("topic", "")
        except (ValueError, TypeError):
            topic = ""
    extra = {k: v for k, v in record.items() if k not in ("key", "chapter")}
    return chapter, topic, extra


# ---- wrong/ directory ----

def json_files_in_directory(directory):
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return [
        directory / name
        for name in sorted(os.listdir(directory))
        if name.endswith(".json") and (directory / name).is_file()
    ]


def wrong_files(deck: Deck):
    return json_files_in_directory(deck.wrong_dir)


def build_result_filename(questions, deck: Deck):
    """Pick (and create) the wrong-file path for a chapter set."""
    deck.wrong_dir.mkdir(parents=True, exist_ok=True)
    return deck.wrong_dir / (_result_prefix_for_questions(questions) + ".json")


# ---- filename prefix logic (pure, also tested directly) ----

def _chapter_sort_key(chapter):
    chapter = str(chapter)
    if chapter.isdigit():
        return (0, int(chapter))
    return (1, chapter)


def _safe_chapter_name(chapter):
    return "".join(c if c.isalnum() else "_" for c in str(chapter))


def _result_prefix_for_questions(questions):
    """Build the wrong-filename prefix from the set of chapters a run covered.

    The prefix encodes *which* chapters were drilled so that re-running the
    same range writes to the same file (incremental wrong-index per range).
    Examples (see test_filename.py for the full contract):

      {"5"}                 -> "ch5"            single chapter
      {"5","6","7"}         -> "ch5_7"          contiguous range collapses
      {"3","5","8"}         -> "ch3_5_8"        discrete set listed
      {"appA"}              -> "chappA"         non-numeric label
      {"appA","appB"}       -> "chappA_appB"    non-numeric set
      {}                    -> "ch_unknown"     nothing to name

    The numeric range-collapse branch only fires when the chapters are a
    *contiguous* run of integers; otherwise we fall through to underscore-join.
    """
    chapters = []
    seen = set()
    for question in questions:
        chapter, _ = question
        chapter = str(chapter)
        if chapter not in seen:
            chapters.append(chapter)
            seen.add(chapter)
    chapters = sorted(chapters, key=_chapter_sort_key)
    if not chapters:
        return "ch_unknown"
    if all(chapter.isdigit() for chapter in chapters):
        nums = [int(chapter) for chapter in chapters]
        if len(nums) == 1:
            return "ch" + str(nums[0])
        if nums == list(range(nums[0], nums[-1] + 1)):
            return "ch" + str(nums[0]) + "_" + str(nums[-1])
        return "ch" + "_".join(str(num) for num in nums)
    return "ch" + "_".join(_safe_chapter_name(chapter) for chapter in chapters)


def relative_to_cwd(path):
    """Render a path for display, relative to CWD when nicer."""
    try:
        return str(Path(path).relative_to(os.getcwd()))
    except ValueError:
        return str(path)


# ---- deck summary rows (shared by `flip list` and the deck picker) ----

DECK_TABLE_HEADERS = ["SLUG", "NAME", "QUESTIONS", "CHAPTERS", "LANG", "ALPHABET", "MARKED", "WRONG"]


def deck_rows(config):
    """Compute one summary row per registered deck.

    Each row is a list of 8 strings aligned with DECK_TABLE_HEADERS. Pure
    function (no TUI) so `flip list` and the interactive deck picker render
    identical tables from the same source. Decks whose manifest fails to
    load still get a row with the error in the NAME column.
    """
    from .deck import list_decks, load_deck, DeckError
    slugs = list_decks(config.decks_dir)
    rows = []
    for slug in slugs:
        try:
            deck = load_deck(config.decks_dir / slug)
        except DeckError as exc:
            rows.append([slug, f"(invalid: {exc})", "", "", "", "", "", ""])
            continue
        data = load_tiku(deck)
        questions = sum(len(qs) for qs in (data or {}).values()) if isinstance(data, dict) else 0
        chapters = len(data) if isinstance(data, dict) else 0
        marked = len(load_marked(deck))
        wrong = sum(len(read_json(p, default=[])) for p in wrong_files(deck))
        rows.append([
            deck.slug, deck.name, str(questions), str(chapters),
            deck.source_lang, deck.answer_alphabet,
            str(marked), str(wrong),
        ])
    return rows


def display_width(s):
    """Display width of a string, counting East Asian wide chars as 2.

    `len()` counts code points, but CJK characters (e.g. 软件工程) occupy two
    terminal columns. Without this, ljust() mis-pads and the table columns
    after a CJK cell drift out of alignment.
    """
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(s))


def _pad(cell, width):
    """Left-align `cell` to `width` display columns (CJK-aware)."""
    return cell + " " * max(0, width - display_width(cell))


def table_widths(rows, headers=DECK_TABLE_HEADERS):
    """Per-column display width needed to fit headers and all rows."""
    return [max(display_width(h), max((display_width(r[i]) for r in rows), default=0))
            for i, h in enumerate(headers)]


def format_table(rows, headers=DECK_TABLE_HEADERS):
    """Left-align a table (CJK-aware); returns (header_line, body_lines)."""
    widths = table_widths(rows, headers)

    def fmt(row):
        return "  ".join(_pad(c, widths[i]) for i, c in enumerate(row))

    return fmt(headers), [fmt(r) for r in rows]
