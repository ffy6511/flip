# Importing Decks

`flip import <slug> <source>` registers a deck from a JSON or CSV file. The
format is inferred from the extension (`.json` / `.csv` / `.tsv`), or forced
with `--format json|csv`.

## JSON source

A compliant `tiku.json` (see `schema.md`). `flip import` validates it before
copying:

```bash
flip import se ./tiku.json --name "软件工程"
```

Validation rules enforced (per `validate_tiku`):

- Top level must be `{chapter_str: [question]}`.
- Every question must have non-empty `topic`, `options` (list of strings),
  `answer` (string of letters).
- Every letter in `answer` must appear as the first char of some option.
- Empty options / empty answer lists are rejected.

The widest option set across all questions determines `answer_alphabet` in the
generated manifest (e.g. a deck whose widest question has 5 options gets
`ABCDE`).

## CSV source (MCQ layout)

`flip` defines **one** explicit multiple-choice CSV layout. Anki / Quizlet
front-back flashcards are **not** supported — forcing them into the
options[]/answer schema would lose information. If you have flashcards,
restructure them into the MCQ layout below first (or use the
`flip-deck-init` skill to generate distractors).

### Layout

```csv
topic,A,B,C,D,answer,chapter
"What is 2+2?","3","4","5","6","B","1"
"Capital of France?","Berlin","Madrid","Paris","Rome","C","2"
"RGB primaries? (multi)","Red","Green","Blue","Yellow","ABC","3"
```

| Column | Required | Notes |
|---|---|---|
| `topic` | yes | Question stem. |
| `A`, `B`, `C`, … | yes (≥2) | Single uppercase letter each. The widest letter drives `answer_alphabet`. |
| `answer` | yes | Letters concatenated, sorted. `"AC"` for multi-select. |
| `chapter` | no | Defaults to `"1"`. |
| `user_note` | no | Pre-seeded note text. |
| `zh_topic`, `zh_A`, `zh_B`, … | no | Translations; only read when global translation is on. |

### Options

```bash
flip import demo ./questions.csv --delimiter comma --no-header --dry-run
flip import demo ./questions.tsv --delimiter tab
```

- `--delimiter` accepts `auto` (default; sniffs comma/tab/semicolon/pipe),
  a named delimiter, or a single literal char.
- `--header` / `--no-header`: whether row 1 is a header (default yes).
- `--dry-run`: parse, validate, print the manifest preview, write nothing.

### Per-row validation

Rows that fail validation (empty topic, answer letter not in options, …) are
skipped and reported by line number; the rest of the file still imports.
Structural problems (missing required columns, no option columns) abort the
whole import.
