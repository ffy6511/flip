# Importing Decks

`flip import <slug> <source>` registers a deck from a JSON file, a CSV/TSV
file, or a whole deck directory. The format is inferred from the path: a
directory is migrated as-is, a `.json`/`.csv`/`.tsv` file by its extension,
or forced with `--format json|csv` (only for file sources).

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

## Directory source

Pointing `flip import` at a **directory** migrates a whole deck folder in one
step — useful for adopting a deck authored elsewhere, or for migrating off the
legacy single-file `se_regressor.py` data layout.

```bash
flip import se /path/to/old_deck_dir --name "软件工程"
```

The directory **must contain `tiku.json`** (validated against the schema just
like a standalone JSON file). Optional siblings are migrated verbatim so
learner history survives the move:

| File / dir | Required | Behavior |
|---|---|---|
| `tiku.json` | yes | Validated, then copied. Drives `answer_alphabet` detection. |
| `marked.json` | no | Copied as the deck's marked index. |
| `wrong/*.json` | no | Whole directory copied into the deck's `wrong/`. |
| `history.json` | no | Copied as the deck's drill-count history. |
| `session.json` | no | Copied as the paused-session checkpoint. |
| `manifest.toml` | no | Its `[deck].name` / `source_lang` are used as defaults (command-line flags still win); a fresh compliant manifest is always generated. |

Notes:

- The old `marked_questions.json` name is **not** recognized — rename it to
  `marked.json` first.
- `wrong/` records missing `wrong_at` are kept as-is. The engine never reads
  that field (it only writes it), so legacy records load cleanly.
- `--format` is ignored for directory input. `--dry-run` still works (validates
  `tiku.json` and previews the manifest without writing).

## Exporting a deck

`flip export <slug>` writes a portable deck directory. It copies `tiku.json`,
`manifest.toml`, `marked.json` when present, the full `wrong/` directory when
present, `history.json` when present, and `session.json` when present.

```bash
flip export se --out ./se-deck
flip import se-copy ./se-deck --name "软件工程副本"
```

The destination directory must not already exist. The exported directory uses
the same shape accepted by directory import, so it can be backed up or moved to
another machine and registered again with `flip import`.

## Incremental updates

`flip deck merge <slug> <source>` merges a JSON/CSV/deck-directory source into
an existing deck. It keeps learner state in the target deck: marks, mark
timestamps, notes, translations, and Agent Said fields are preserved when the
incoming question does not provide replacement content.

```bash
flip deck merge se ./new-tiku.json --dry-run
flip deck merge se ./new-tiku.json --policy upsert
```

Policies:

| Policy | Behavior |
|---|---|
| `append` | Add only new questions. Existing id/topic changes are reported as conflicts. |
| `upsert` | Update existing questions when `id` matches; add new questions. Same-topic updates without id remain conflicts. |
| `overwrite` | Update id matches and same-topic matches; add new questions. |

Merge writes a backup by default before changing the deck:
`~/.local/share/flip/backups/<slug>-deck-YYYYMMDD-HHMMSS/`. Use
`--no-backup` only for disposable test decks.

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
