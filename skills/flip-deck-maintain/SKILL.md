---
name: flip-deck-maintain
description: Maintain an existing flip deck by choosing between CLI merge and direct tiku.json edits. Use when updating, patching, extending, deduplicating, or reviewing an existing flip deck, especially when an agent must preserve question ids, marked/wrong history, notes, translations, and Agent Said fields.
---

# flip-deck-maintain

Maintain an existing flip deck without losing learner state.

Use this skill after a deck already exists. For creating a new deck from source material, use `flip-deck-init` instead.

## First checks

Identify the deck and inspect its current state before editing:

```bash
flip list
flip deck stats <slug>
flip deck mark <slug>
flip deck wrong <slug>
```

If the user gives a raw source file and the deck already exists, do not use `flip import --force` unless they explicitly want to replace the deck. Prefer `flip deck merge`.

## Choose the update path

Use `flip deck merge` when the change adds questions, updates answers, changes options, moves chapter content, imports regenerated JSON/CSV, or may affect question identity.

Use direct JSON editing only for small, local edits: typo fixes, notes, translations, Agent Said text, or a few explicit question corrections where the target question is unambiguous.

## Merge workflow

Always dry-run first:

```bash
flip deck merge <slug> /path/to/new-tiku.json --dry-run
```

Then apply with the narrowest policy:

```bash
flip deck merge <slug> /path/to/new-tiku.json --policy append
flip deck merge <slug> /path/to/new-tiku.json --policy upsert
flip deck merge <slug> /path/to/new-tiku.json --policy overwrite
```

Policy meanings:

- `append`: only add new questions; conflicts mean the source overlaps existing questions.
- `upsert`: update existing questions when `id` matches; add new questions.
- `overwrite`: allow same-topic replacement when id is absent; use only when the user's intent is clear.

The command writes a backup by default before applying. Keep `--backup` enabled for real decks; use `--no-backup` only for disposable test decks.

After applying:

```bash
flip deck stats <slug>
flip deck mark <slug>
flip deck wrong <slug>
```

## Direct JSON editing

Locate the live deck with:

```bash
flip config
```

Open `<flip home>/decks/<slug>/tiku.json`. Preserve these fields unless the user explicitly asks to change them:

- `id`
- `marked`, `marked_at`
- `user_note`
- `zh`
- `ai_explanation`, `ai_explanation_user_prompt`, `ai_explanation_model`, `ai_explanation_updated_at`

Rules:

- Never delete or rewrite `id`. It is the stable link to `marked.json` and `wrong/`.
- Do not edit `marked.json` or `wrong/` directly unless the user explicitly asks to clear history.
- Keep option prefixes as `"A. "`, `"B. "`, etc.
- Keep multi-select answers sorted, for example `"AC"` not `"CA"`.
- Keep `_chapter_titles` display-only; select chapters by numeric/string chapter id.

Validate after editing:

```bash
flip import check /path/to/edited-tiku.json --dry-run
flip deck stats <slug>
```

## Agent output rules

When handing work back to the user, report:

- Which path was used: merge command or direct JSON edit.
- The commands run and their results.
- Added, updated, skipped, and conflict counts when using merge.
- Whether a backup was written.
- Any unresolved conflicts or questions skipped because the answer was uncertain.
