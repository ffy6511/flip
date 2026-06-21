# flip

[English](README.md) · [中文](README.zh.md)

A deck-agnostic terminal quiz trainer. Pick a **deck** (a subject like Software Engineering, Compiler Principles), drill questions, mark the tricky ones, and let an agent explain mistakes — all from a single `flip` command.

## Install

**Homebrew (recommended):**

```bash
brew tap ffy6511/tap
brew install flip
```

**From source (pipx):**

```bash
brew install pipx          # if you don't have pipx yet
pipx install git+https://github.com/ffy6511/flip.git
```

**(Optional) Companion skills for the CLI**

```bash
npx skills add ffy6511/flip/skills   # install the companion skills; see below for what each does
```

**For development:**

```bash
git clone https://github.com/ffy6511/flip.git
cd flip
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/flip --help
```



## Optional: companion agent skills

The `skills/` folder in this repo holds skills that teach an AI agent (Claude
Code, Cursor, ZCode, …) how to work with flip. They are **not** shipped with
the pip/brew package — install them with the one-liner in the Install section
above, then just ask your agent to act. The current set:

| Skill | What it does |
|-------|--------------|
| [`flip-deck-init`](skills/flip-deck-init/) | Turn any quiz material (PDF / HTML / Word / notes) — or an existing question-bank JSON — into a schema-compliant deck and register it via `flip import`. Covers both bootstrapping a new deck from raw material and importing an already-structured JSON. |

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
