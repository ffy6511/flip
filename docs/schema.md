# tiku.json Schema

`docs/schema.md` is the **source of truth** for the question-bank JSON format. Engine read/write paths, the `flip-deck-init` skill, and test fixtures MUST align with this file. If a field's semantics change, change this file first, then code and tests.

## Top-level shape

```jsonc
{
  "1": [ <question>, <question>, ... ],   // chapter "1"
  "2": [ <question>, ... ],               // chapter "2"
  ...
}
```

- **Type:** object.
- **Key:** chapter identifier, a string. Conventionally a stringified integer (`"1"`, `"2"`, …) but non-numeric chapter labels are also allowed.
- **Value:** non-empty array of question objects.

## Question object

### Required fields (present on every question)

| Field    | Type     | Notes |
|----------|----------|-------|
| `topic`  | string   | The question **stem text**. Often starts with `"N. "` (the question's own ordinal), but the engine does not parse that prefix. |
| `options`| string[] | Each entry is `LABEL + ". " + text` — a **3-character prefix** (`"A. "`, `"B. "`, …). `_split_option` relies on this exact shape. |
| `answer` | string   | Letters of the correct option(s), concatenated and sorted, e.g. `"A"`, `"AC"`, `"BDE"`. Single select → one letter; multiple select → multiple letters. |
| `zh`     | object   | Chinese translation, present **only when translation is enabled** (global `source_lang != target_lang`). Shape: `{ "topic": string, "options": string[] }`, parallel to `topic`/`options`. When translation is off, this field is absent and the engine MUST NOT write it. |
| `user_note` | string | Free-form learner note. Default `""`. |

### Optional persisted fields (only on questions that have them)

| Field                          | Type   | When present |
|--------------------------------|--------|--------------|
| `marked`                       | bool   | Only on questions the learner flagged. Absent ⇒ unmarked. |
| `marked_at`                    | string | ISO-8601 timestamp; present whenever `marked: true`. |
| `ai_explanation`               | string | Agent-generated explanation. Present only after first generation. |
| `ai_explanation_user_prompt`   | string | The learner's follow-up prompt, only when the explanation was regenerated with an extra instruction. |
| `ai_explanation_model`         | string | Model id that produced `ai_explanation`. |
| `ai_explanation_updated_at`    | string | ISO-8601 timestamp of the last regeneration. |

All optional fields are **persisted to disk** (not runtime-only). A re-read of `tiku.json` must reproduce them.

## Answer alphabet

`answer` letters come from the deck's `answer_alphabet` (see `deck-manifest.md`). Default `ABCD`. A 5-option question uses `ABCDE`; **`E` is a normal fifth option** — there is no hidden-option suppression in flip (the legacy `E. Both …` SE behavior is intentionally dropped).

## marked.json (index file)

`~/.local/share/flip/decks/<slug>/marked.json` is a flat array of index records:

```jsonc
[
  { "key": "<serialized question key>", "chapter": "5", "marked_at": "2026-06-20T14:40:11" },
  ...
]
```

- `key` is the output of `question_key(chapter, q)` — a stable, sorted JSON serialization of `{chapter, topic, answer, options}`. It is the identity of a question across `tiku.json` and all index files.
- `chapter` is duplicated at the top level for convenience (also appears serialized inside `key`).

## wrong/ (error index directory)

`~/.local/share/flip/decks/<slug>/wrong/` holds chapter-range error index files, each a flat array of index records like `marked.json` but additionally carrying the wrong attempt metadata:

```jsonc
[
  {
    "key": "...",
    "chapter": "5",
    "wrong_input": "B",
    "wrong_answer": "B",
    "wrong_at": "2026-06-20T14:40:11"
  }
]
```

Filename convention (produced by `result_prefix_for_questions`):

| Chapter set              | Filename            |
|--------------------------|---------------------|
| single numeric `5`       | `ch5.json`          |
| contiguous `5..7`        | `ch5_7.json`        |
| discrete `3,5,8`         | `ch3_5_8.json`      |
| non-numeric `appA`       | `chappA.json`       |
| empty / unresolvable     | `ch_unknown.json`   |
