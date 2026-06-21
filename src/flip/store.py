"""Storage layer: JSON read/write keyed by deck.

Replaces the SCRIPT_DIR-relative globals in se_regressor.py. All paths resolve
through a Deck object, so the engine stays deck-agnostic.
"""

import json
import os
from pathlib import Path

from .deck import Deck


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
