"""Source-format importers.

Currently supports one explicit MCQ CSV layout (see docs/import.md). Anki /
Quizlet front-back flashcards are deliberately NOT supported — forcing them
into the options[]/answer schema loses information.

All functions here are pure: they take a path or parsed data and return a
{chapter_str: [question]} dict (for CSV) or a list of error strings (for
validation). No filesystem writes, no side effects.
"""

import csv
from pathlib import Path


OPTION_LETTERS = "ABCDEFGHIJ"   # support up to 10 options per question


# ---- delimiter detection ----

_DELIMITER_NAMES = {
    "comma": ",",
    "tab": "\t",
    "semicolon": ";",
    "pipe": "|",
}


def _resolve_delimiter(name, sample_line):
    """Map a delimiter name (or 'auto') to an actual char using a sample line."""
    if name == "auto":
        return _sniff_delimiter(sample_line)
    if name in _DELIMITER_NAMES:
        return _DELIMITER_NAMES[name]
    # Allow passing a literal single-char delimiter too.
    if len(name) == 1:
        return name
    raise ValueError(f"unknown delimiter: {name!r}")


def _sniff_delimiter(sample_line):
    """Pick the delimiter that appears most often on the first data line.

    CSV/TSV exporters are inconsistent (Anki defaults to tab, Quizlet lets the
    user pick). Rather than require the user to know, we count occurrences of
    each candidate on one sample line and take the max. Falls back to comma
    when none are present (e.g. a single-column file, which will fail later
    validation anyway).
    """
    if sample_line is None:
        return ","
    counts = {d: sample_line.count(d) for d in [",", "\t", ";", "|"]}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


# ---- CSV import ----

def import_csv(path, *, delimiter="auto", has_header=True, translation_enabled=False):
    """Parse an MCQ CSV into a {chapter_str: [question]} dict.

    Required columns: topic, answer, plus at least 2 option columns named by
    single uppercase letters (A, B, C, …).

    Optional columns: chapter (default "1"), user_note.
    Translation columns (only read when translation_enabled): zh_topic, zh_A,
    zh_B, … — they populate the `zh` object on each question.

    Raises ValueError on structural problems (missing required columns, no
    option columns). Per-row problems are collected into the `errors` field of
    the returned result object.
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8", newline="") as f:
        raw_lines = f.read().splitlines()

    if not raw_lines:
        raise ValueError(f"CSV is empty: {path}")

    delim = _resolve_delimiter(delimiter, raw_lines[0])
    reader = csv.reader(raw_lines, delimiter=delim)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"CSV has no non-empty rows: {path}")

    if has_header:
        header = [cell.strip() for cell in rows[0]]
        data_rows = rows[1:]
    else:
        # No header: assume positional topic, A, B, C, D, answer, chapter?
        header = ["topic", "A", "B", "C", "D", "answer", "chapter"]
        data_rows = rows

    col_index = {name: i for i, name in enumerate(header) if name}
    _require_columns(col_index, header)

    option_cols = sorted(
        c for c in header if len(c) == 1 and c.isalpha() and c.upper() in OPTION_LETTERS
    )
    if len(option_cols) < 2:
        raise ValueError(
            f"need at least 2 option columns (A, B, …); found {option_cols or 'none'}"
        )
    max_letter = option_cols[-1]
    answer_alphabet = OPTION_LETTERS[:OPTION_LETTERS.index(max_letter) + 1]

    zh_option_cols = [c for c in header if c.lower().startswith("zh_")
                      and len(c) == 4 and c[3].upper() in OPTION_LETTERS]

    chapters = {}
    errors = []
    for line_no, row in enumerate(data_rows, start=2 if has_header else 1):
        try:
            q, chapter = _parse_csv_row(
                row, col_index, option_cols, zh_option_cols,
                translation_enabled=translation_enabled,
            )
        except _RowError as exc:
            errors.append((line_no, str(exc)))
            continue
        chapters.setdefault(str(chapter), []).append(q)

    if not chapters and not errors:
        errors.append((0, "no data rows parsed"))

    return CsvImportResult(
        chapters=chapters,
        answer_alphabet=answer_alphabet,
        errors=errors,
        row_count=len(data_rows),
    )


class CsvImportResult:
    """Structured result of import_csv — chapters + metadata."""
    def __init__(self, *, chapters, answer_alphabet, errors, row_count):
        self.chapters = chapters
        self.answer_alphabet = answer_alphabet
        self.errors = errors
        self.row_count = row_count

    @property
    def question_count(self):
        return sum(len(qs) for qs in self.chapters.values())

    @property
    def ok(self):
        return not self.errors


class _RowError(Exception):
    """Raised for one bad CSV row; collected into the result.errors list."""


def _require_columns(col_index, header):
    for required in ("topic", "answer"):
        if required not in col_index:
            raise ValueError(
                f"CSV missing required column {required!r}; header was: {header}"
            )


def _parse_csv_row(row, col_index, option_cols, zh_option_cols, *, translation_enabled):
    """Parse one CSV data row into a (question, chapter) tuple.

    Raises _RowError on per-row problems (empty topic, answer references a
    non-existent option, …). The caller catches these and collects them into
    result.errors rather than aborting the whole import — a typo on row 17
    shouldn't sink rows 1-16 and 18-100.

    Option handling has one subtlety: a trailing empty option column is
    *allowed* (some exporters pad every row to the widest question's width with
    empty strings), but a gap in the middle is not (that would misalign
    answers). E.g. `[A, B, "", D]` is rejected; `[A, B, C, ""]` keeps A/B/C.
    """
    def cell(name):
        i = col_index.get(name)
        if i is None or i >= len(row):
            return ""
        return row[i].strip()

    topic = cell("topic")
    if not topic:
        raise _RowError("empty topic")
    answer = cell("answer").upper()
    if not answer:
        raise _RowError("empty answer")

    options = []
    for letter in option_cols:
        text = cell(letter)
        if not text:
            # Skip trailing empty option columns; an empty middle column is an error.
            if letter == option_cols[-1]:
                continue
            raise _RowError(f"option {letter} is empty")
        prefix = letter + ". "
        options.append(prefix + text)

    # Validate answer letters are within the options present.
    present = {opt[0] for opt in options}
    for a in answer:
        if a not in present:
            raise _RowError(
                f"answer {a!r} not in available options {sorted(present)}"
            )

    chapter = cell("chapter") or "1"
    q = {
        "topic": topic,
        "options": options,
        "answer": answer,
        "user_note": cell("user_note"),
    }

    # Translation columns are only honored when global translation is on AND
    # every option has a translation (partial translations are silently
    # dropped rather than written half-populated).
    if translation_enabled:
        zh_topic = cell("zh_topic")
        zh_options = []
        for letter in option_cols:
            zh_text = cell("zh_" + letter)
            if zh_text:
                zh_options.append(letter + ". " + zh_text)
        if zh_topic and len(zh_options) == len(options):
            q["zh"] = {"topic": zh_topic, "options": zh_options}

    return q, chapter


# ---- JSON validation ----

def validate_tiku(data):
    """Validate an already-parsed tiku dict. Returns a list of error strings.

    Empty list = valid. Checks the structural rules from docs/schema.md that,
    if violated, would crash the engine at training time.

    Validation is layered and *collects* all errors rather than failing fast:
    a deck with 5 problems reports all 5 at once, so the user can fix them in
    one pass instead of fixing one, re-importing, finding the next, etc.

    Checks per question, in order:
      1. Required fields present (topic, options, answer).
      2. options is a non-empty list of non-empty strings.
      3. answer is a non-empty string whose every letter is the initial of
         some option (catches "answer E" when only A-D exist).
    Each error is prefixed with chapter + question index for easy locating.
    """
    errs = []
    if not isinstance(data, dict):
        return [f"top-level must be an object {{chapter: [question]}}, got {type(data).__name__}"]

    if not data:
        return ["tiku is empty (no chapters)"]

    seen_ids = {}
    for chapter, questions in data.items():
        # Skip metadata keys like `_chapter_titles` (not real chapters).
        if str(chapter).startswith("_"):
            continue
        if not isinstance(questions, list):
            errs.append(f"chapter {chapter!r}: value must be a list, got {type(questions).__name__}")
            continue
        if not questions:
            errs.append(f"chapter {chapter!r}: question list is empty")
            continue
        for i, q in enumerate(questions):
            prefix = f"chapter {chapter!r} question {i + 1}"
            if not isinstance(q, dict):
                errs.append(f"{prefix}: not an object")
                continue
            for field in ("topic", "options", "answer"):
                if field not in q:
                    errs.append(f"{prefix}: missing required field {field!r}")
            if "id" in q:
                qid = q["id"]
                if not isinstance(qid, str) or not qid.strip():
                    errs.append(f"{prefix}: id must be a non-empty string")
                else:
                    if qid in seen_ids:
                        errs.append(f"{prefix}: duplicate id {qid!r} (first seen at {seen_ids[qid]})")
                    else:
                        seen_ids[qid] = prefix
            if "options" in q and not isinstance(q["options"], list):
                errs.append(f"{prefix}: options must be a list")
            elif isinstance(q.get("options"), list):
                if not q["options"]:
                    errs.append(f"{prefix}: options list is empty")
                else:
                    for j, opt in enumerate(q["options"]):
                        if not isinstance(opt, str) or not opt.strip():
                            errs.append(f"{prefix}: option {j + 1} is empty or not a string")
            if "answer" in q:
                ans = q["answer"]
                if not isinstance(ans, str) or not ans.strip():
                    errs.append(f"{prefix}: answer must be a non-empty string")
                elif isinstance(q.get("options"), list) and q["options"]:
                    present = {o[0].upper() for o in q["options"] if isinstance(o, str) and o}
                    for a in ans.upper():
                        if a not in present:
                            errs.append(f"{prefix}: answer {a!r} not in options {sorted(present)}")
    return errs
