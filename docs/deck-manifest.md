# Deck Manifest

Each deck lives at `~/.local/share/flip/decks/<slug>/` and carries a `manifest.toml` that makes the deck's subject-specific assumptions **explicit** instead of hardcoded in the engine.

## File

`~/.local/share/flip/decks/<slug>/manifest.toml` — TOML format.

## Example

```toml
[deck]
name = "软件工程"          # human-readable subject name
slug = "se"               # == directory name; used in `flip deck se ...`
source_lang = "en"        # source language of topic/options; compared against global target_lang
answer_alphabet = "ABCDE" # max option letters across this deck

[explain]
role = "软件工程课程助教"   # injected into the AI explanation prompt
max_chars = 200           # soft cap on explanation length
default_model = "gpt-5.3-codex-spark"
model_env = "CODEX_EXPLAIN_MODEL"  # env var name; if set, overrides default_model
```

## `[deck]` fields

| Field             | Type   | Required | Notes |
|-------------------|--------|----------|-------|
| `name`            | string | yes      | Display name shown in the entry menu and stats. |
| `slug`            | string | yes      | Directory name and CLI identifier. Must match `[a-z0-9-]+`. |
| `source_lang`     | string | yes      | Language code of the raw `topic`/`options`. Translation is enabled only if `source_lang != config.target_lang`. |
| `answer_alphabet` | string | no       | Default `"ABCD"`. Letters that may appear in `answer`. Length drives `parse_answer`'s digit mapping (`1..N → A..`). |

## `[explain]` fields

| Field           | Type   | Required | Notes |
|-----------------|--------|----------|-------|
| `role`          | string | yes      | Persona prefix for the explanation prompt (replaces the legacy hardcoded "软件工程课程助教"). |
| `max_chars`     | int    | no       | Default `200`. Hint passed to the model. |
| `default_model` | string | no       | Model id used if the env var below is unset. |
| `model_env`     | string | no       | Name of an env var that, when set, overrides `default_model`. Defaults to `FLIP_EXPLAIN_MODEL`. |

## Validation rules (enforced by `deck.py`)

1. `slug` must match the directory name.
2. `[deck].name`, `[deck].slug`, `[deck].source_lang`, `[explain].role` are required — missing any raises a load error.
3. `answer_alphabet`, if present, must be uppercase ASCII letters, no duplicates.
4. Unknown top-level tables/keys are ignored but logged as a warning (forward-compat).
