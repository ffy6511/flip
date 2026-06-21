---
name: flip-deck-init
description: Convert quiz material from any source (PDF / HTML / Word / plain text etc.) into flip CLI's tiku.json format and register it as a trainable deck via `flip import`. Use this when a user wants to "import material into flip" / "make a quiz deck" / "generate tiku.json".
---

# flip-deck-init

Turn a body of quiz material into a deck that flip can train on.

This skill **does not prescribe which tool to use to read the source** — PDF via `pdftotext`, HTML via Read, Word via `pandoc`: pick based on the source. This skill only does two things:

1. Teach you how to produce a **schema-compliant tiku.json**
2. Teach you how to validate and land it with the `flip` CLI

## When to use

- The user gives you quiz material (any format) and wants a flip deck
- The user says "import this into flip" / "make a quiz deck" / "generate tiku.json"
- The user wants to build a training set from course notes, past exams, Anki exports, etc.

## Prerequisite: ensure flip is installed

```bash
which flip || echo "NOT_INSTALLED"
```

If it prints `NOT_INSTALLED`, have the user install it (give the command, don't install it for them without asking):

```bash
# Recommended (Homebrew)
brew tap ffy6511/tap
brew install flip

# Or from source
pipx install git+https://github.com/ffy6511/flip.git
```

Once installed, verify with `flip --help` before continuing.

## Core task

Produce a compliant tiku.json → self-check with `flip import --dry-run` → land it with `flip import`.

---

### Step 1 — Produce a schema-compliant tiku.json

**Top-level shape**: an object whose keys are chapter identifiers (conventionally stringified numbers `"1"`, `"2"`) and whose values are arrays of that chapter's questions.

```jsonc
{
  "1": [ <question>, <question>, ... ],
  "2": [ <question>, ... ]
}
```

**Fields on each question object**:

| Field        | Type     | Required | Notes |
|--------------|----------|----------|-------|
| `id`         | string   | no       | Stable question identity. You may write it yourself; otherwise `flip import` assigns ids. Preserve it on later edits. |
| `topic`      | string   | yes      | The question stem. May carry its own ordinal prefix (e.g. `"3. What is..."`); flip does not parse it. |
| `options`    | string[] | yes      | The choices. **Every entry must be `"X. text"`** (letter + period + space, a 3-char prefix), e.g. `"A. correct choice"`. Letters start at A and increment consecutively. |
| `answer`     | string   | yes      | The correct option letter(s). Single-select `"A"`; multi-select is the **sorted concatenation**, e.g. `"AC"`, `"BDE"`. Every letter must be the first char of some option. |
| `user_note`  | string   | yes      | Learner note; default `""`. |
| `zh`         | object   | no       | Translation. **Only write it when the user explicitly wants translation**; shape is `{"topic": "...", "options": ["A. ...", ...]}`, parallel to and the same length as the original. |
| `ai_explanation` | string | no   | A pre-generated "Agent Said" explanation. **Optional; only write it for selected questions that need special clarification** (see Step 3). |
| `marked` etc.| various  | no       | Usually not written at import time; produced during training. |

#### Minimal compliant example

```json
{
  "1": [
    {
      "id": "demo-1-001",
      "topic": "What is 2 + 2?",
      "options": ["A. 3", "B. 4", "C. 5", "D. 6"],
      "answer": "B",
      "user_note": ""
    },
    {
      "id": "demo-1-002",
      "topic": "Which are prime? (multi-select)",
      "options": ["A. 2", "B. 4", "C. 7", "D. 9"],
      "answer": "AC",
      "user_note": ""
    }
  ],
  "2": [
    {
      "id": "demo-2-001",
      "topic": "Capital of France?",
      "options": ["A. Berlin", "B. Madrid", "C. Paris", "D. Rome"],
      "answer": "C",
      "user_note": ""
    }
  ]
}
```

For full field semantics see `docs/schema.md` in the flip repo.

---

### Step 2 — Extract from the source material

How you read the source is your choice (Read / Bash calling a converter / another skill). Follow these principles when extracting:

- **Identify the triple**: every question must have all three of stem + options + correct answer. If any is missing, it's not a complete question — skip it or flag it separately.
- **Group into chapters by the source's own structure**: if the material has sections, map them directly; otherwise dump everything into a single `"1"` chapter.
- **When the answer is uncertain, leave it blank rather than guess**: set uncertain questions aside in a temp file instead of guessing — wrong answers make the user memorize the wrong thing.
- **Option count is variable**: 2–10 are all fine. Letters increment consecutively from A to whatever is needed (e.g. 5 options use E). flip has **no** "hidden E option" logic; E is a normal option.
- **Sort multi-select answers**: always concatenate in alphabetical order (`"AC"`, not `"CA"`), otherwise training works but stats get confused.
- **Translation (`zh`) trade-off**: only write `zh` when (a) the user explicitly asks for translation AND (b) you can provide a complete translation for every question (topic + all options). Otherwise omit `zh` entirely (empty beats half-populated; `flip deck translate <slug>` can fill it later).

---

### Step 3 — Pre-generate explanations for hard questions (optional but recommended)

Each question in tiku.json can carry an `ai_explanation` field — this is the "Agent Said" block shown when pressing `x` during training. **By default it's generated on-demand by calling the model during training**, but you can pre-write it for **some** questions at init time, sparing the user the wait on first training and giving hard questions an explanation the very first time they're encountered.

**Selection principle — write only for questions that genuinely need it, not for every question**:

- **Ambiguous questions**: a distractor that looks right too, needing an explanation of why it's not chosen
- **Hard / error-prone questions**: the correct answer is counter-intuitive, or involves easily confused concepts
- **Questions with a trap behind the answer**: e.g. NOT/EXCEPT reverse-selection questions
- **Multi-select questions**: which combinations are right/wrong often needs per-option explanation

Plain, straightforward questions (obvious answer, no distractor value) should **not** get one — leave it blank; the user can press `x` during training to generate it live if they want.

**How to write it**: add an `ai_explanation` field to the question object, plain text, no Markdown markers (no `**`, headings, or tables). Suggested structure: state the correct answer first → briefly explain why it's right → call out why key distractors are wrong. Keep it under ~200 characters-ish.

```jsonc
{
  "topic": "Which is NOT a characteristic of agile methods?",
  "options": ["A. Iterative development", "B. Comprehensive documentation", "C. Customer collaboration", "D. Responding to change"],
  "answer": "B",
  "user_note": "",
  "ai_explanation": "Correct answer is B. The Agile Manifesto explicitly values 'working software over comprehensive documentation', so 'comprehensive documentation' is not an agile trait. A/C/D are all agile core tenets (iterative development, customer collaboration, responding to change). The NOT in the stem is the key — this is reverse-selection and easy to miss."
}
```

**Cadence guidance**: for a 100-question set, pre-write 10–20. Prefer few and sharp over many and diluted — flooding every question with a paragraph wastes effort and dilutes the signal that "questions with an explanation are the ones to watch".

---

### Step 4 — Self-check (the crucial step)

After writing tiku.json, **do not land it directly** — run flip's validator first:

```bash
flip import <slug> /path/to/your_tiku.json --dry-run
```

`--dry-run` only parses + validates; it writes nothing. It will:

- Verify the JSON is structurally valid
- Check each question has `topic` / `options` / `answer`
- Check every letter in `answer` is found among the `options`
- Auto-detect `answer_alphabet` (from the widest option set)
- Print a preview of the manifest to be generated

**Do not proceed until it reports 0 errors.** Validation errors come with the specific line/question number; fix per the hints.

---

### Step 5 — Land it

```bash
flip import <slug> /path/to/your_tiku.json --name "Display Name"
```

- `<slug>` is the deck's CLI identifier (lowercase letters/digits/hyphens, e.g. `compiler`, `se`)
- `--name` is the display name shown in menus (optional; defaults to slug)
- `--source-lang` sets the source language (default `en`; pass `zh` for Chinese material)
- `--role` customizes the AI explanation persona (optional; defaults to `"<name> 助教"`)

Verify after landing:

```bash
flip list                  # should show the new deck
flip deck stats <slug>     # check the question/chapter counts look right
```

> Subcommand order is `flip deck <verb> <slug>` (verb before slug), e.g. `flip deck stats se`.

---

## Common pitfalls

1. **The option prefix must be `"A. "` (letter + period + space)**. Not `"A)"`, `"A:"`, `"A、"`. The validator only checks that `opt[0]` is a letter, but the engine's rendering depends on this exact format — getting it wrong breaks translation alignment and answer matching.
2. **Every letter in `answer` must be found among the `options`**. A 4-option question with `answer: "E"` fails validation outright.
3. **Multi-select answers must be sorted**: `"AC"` ✓, `"CA"` ✗.
4. **Never leave `zh` half-populated**: either write it complete for every question (topic + all options) or don't write it at all. A half-populated `zh` misleads the translation toggle during training.
5. **The top level must be an object, not an array**. `[{...}, {...}]` is invalid; it must be `{"1": [...]}`.
6. **Chapter keys are strings**: `"1"` not `1` (both are valid JSON, but flip normalizes to strings internally).
7. **When the deck already exists**: use `flip deck merge <slug> <file> --dry-run` for incremental updates. Use `flip import --force` only when the user explicitly wants to replace the whole deck.

## After producing

On a successful landing, tell the user:

- The deck is registered at `~/.local/share/flip/decks/<slug>/`
- Start training: `flip deck train <slug>`
- Review wrong answers (drill the wrong index): `flip deck review <slug>`
- Browse answers without scoring: `flip deck train <slug> --ans`
- See stats: `flip deck stats <slug>`
- Incrementally update later: `flip deck merge <slug> <new-tiku.json> --dry-run`
- If translations are incomplete and global translation is on: `flip deck translate <slug>`

## Boundaries: when not to use this skill

- The source is **not multiple-choice** (e.g. pure term definitions, short-answer, fill-in-the-blank) — flip is an MCQ trainer; forcing it in distorts the material. Confirm with the user whether to reshape it into MCQ first.
- The source is an Anki/Quizlet **front-back single card** set — these have no distractors; converting to single-option questions has low training value. Suggest the user reorganize into MCQ.
- You'd need an LLM to **auto-generate distractors** — this skill doesn't do that; quality is uncontrollable. Have the user supply distractors or find another approach.
