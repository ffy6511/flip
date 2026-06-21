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

**Upgrade an existing install:**

```bash
brew update && brew upgrade flip
pipx upgrade flip
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
flip                              # interactive: pick a deck, then pick a mode
flip list                         # list registered decks
flip deck train se -c 5-10        # train SE on chapters 5–10 (tiku, scored)
flip deck review se               # drill SE's wrong index (scored)
flip deck train se --ans          # browse SE showing answers, no scoring
flip deck stats se                # per-chapter distribution
flip deck mark se                 # list marked questions
flip deck wrong se                # list wrong-index questions
flip deck translate se            # fill missing zh fields
flip import se ./tiku.json        # register a compliant JSON as a new deck
flip export se -o ./se-deck       # bundle a deck for backup or transfer
flip config                       # show config and explain-backend status
```

> Subcommand order is `flip deck <verb> <slug>` (verb before slug).
> Chapter selectors accept single chapters, ranges, first-N shorthand, and
> comma unions: `5`, `5-10`, `-3`, `5,3-4`.
> Running `flip` with no args is a two-stage picker: choose a deck (with live
> search), then choose a mode — **Train** (tiku), **Review** (wrong index),
> or **List** (stats) — plus the 1-4 filters and an **Ans mode** toggle that
> shows answers without scoring.

## Layout

```
src/flip/      engine, TUI, store, config, deck manifest, explain
docs/          schema.md (tiku.json), deck-manifest.md, import.md
decks/example/ minimal demo deck (also a test fixture)
skills/        flip-deck-init — agent skill to bootstrap a deck from source material
tests/         pytest suite, including focused TUI-loop regressions
```
