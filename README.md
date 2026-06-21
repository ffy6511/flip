# flip

A deck-agnostic terminal quiz trainer. Pick a **deck** (a subject like Software Engineering, Compiler Principles), drill questions, mark the tricky ones, and let an agent explain mistakes — all from a single `flip` command.

## Why

Born from a single-file SE quiz script (`se_regressor.py`), generalized into a template so any subject can plug in by providing a `tiku.json` + `manifest.toml`. The TUI engine is deck-agnostic; subjects are just data.

## Install

```bash
brew install pipx
pipx install .
```

Then `flip` is on your PATH.

## Concepts

- **deck** — a subject (SE, compilers, ...). Lives in `~/.local/share/flip/decks/<slug>/`.
- **topic** — the *stem text* of a single question (kept as the `topic` field for backward compat with existing data).
- **chapter** — a grouping key inside a deck's `tiku.json`.

See `docs/schema.md` and `docs/deck-manifest.md` for the data contracts.

## Usage

```bash
flip                              # interactive entry menu
flip list                         # list registered decks
flip deck train se -c 5-10        # train SE, chapters 5–10
flip deck review se               # browse SE questions without scoring
flip deck stats se                # per-chapter distribution
flip deck translate se            # fill missing zh fields
flip import se ./tiku.json        # register a compliant JSON as a new deck
```

> Subcommand order is `flip deck <verb> <slug>` (verb before slug).

## Layout

```
src/flip/      engine, TUI, store, config, deck manifest, explain
docs/          schema.md (tiku.json), deck-manifest.md
decks/example/ minimal demo deck (also a test fixture)
skills/        flip-deck-init — agent skill to bootstrap a deck from source material
tests/         pure-function pytest suite (no TUI interaction tests)
```

## What this template deliberately does NOT do

- No hidden "E. Both" option suppression — every option is visible.
- No Windows terminal support (relies on `termios`).
- No PDF/HTML extraction in-process — that's the `flip-deck-init` skill's job.
