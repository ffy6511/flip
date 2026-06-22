# flip

[English](README.md) · [中文](README.zh.md)

A deck-agnostic terminal quiz trainer. Pick a **deck** (a subject like Software Engineering, Compiler Principles), drill questions, mark the tricky ones, and let an agent explain mistakes — all from a single `flip` command.

## Install

**Homebrew:**

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



## Getting started

Run `flip` with no arguments. You land in a deck picker with two tabs at the top — switch with **←/→**:

- **Library** — your installed decks. Pick one with ↑/↓ + Enter (typing filters by slug/name). When empty it shows a hint pointing at the Bootstrap tab (and the `flip import` command) instead of aborting, so you can install your first deck without leaving the picker.
- **Bootstrap** — bundled decks not yet installed. Select with **space** (multi-select, marked `[x]`), then **Enter** twice to confirm and install.

Bundled decks ship inside the package (currently the Software Engineering template, 561 questions, English→Chinese). Installing is **explicit and one-shot**: `flip deck remove <slug>` deletes a deck entirely, and it will **not** come back on the next launch — you'd re-pick it from the Bootstrap tab yourself. Nothing is auto-installed at startup.

## Optional: companion agent skills

The `skills/` folder in this repo holds skills that teach an AI agent (Claude
Code, Cursor, ZCode, …) how to work with flip. They are **not** shipped with
the pip/brew package — install them with the one-liner in the Install section
above, then just ask your agent to act. The current set:

| Skill | What it does |
|-------|--------------|
| [`flip-deck-init`](skills/flip-deck-init/) | Turn any quiz material (PDF / HTML / Word / notes) — or an existing question-bank JSON — into a schema-compliant deck and register it via `flip import`. Covers both bootstrapping a new deck from raw material and importing an already-structured JSON. |
| [`flip-deck-maintain`](skills/flip-deck-maintain/) | Safely update an existing deck by choosing between `flip deck merge` and direct `tiku.json` edits while preserving ids, marks, wrong-history, notes, translations, and Agent Said fields. |

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
flip deck continue se             # resume the latest paused scored drill
flip deck train se --ans          # browse SE showing answers, no scoring
flip deck stats se                # per-chapter distribution
flip deck clear-count se --mode all  # clear stored train/review counts only
flip deck mark se                 # list marked questions
flip deck wrong se                # list wrong-index questions
flip deck merge se ./new.json --dry-run  # preview an incremental deck update
flip deck repair se --dry-run     # validate tiku and rebuild marked index
flip deck translate se            # fill missing zh fields
flip import se ./tiku.json        # register a compliant JSON as a new deck
flip export se -o ./se-deck       # bundle a deck for backup or transfer
flip config                       # show config and explain-backend status
```

> Subcommand order is `flip deck <verb> <slug>` (verb before slug).
> Chapter selectors accept single chapters, ranges, first-N shorthand, and
> comma unions: `5`, `5-10`, `-3`, `5,3-4`.
> Running `flip` with no args starts in the deck picker (see [Getting started](#getting-started)): pick a deck on the **Library** tab (↑/↓ + Enter, live search) or install a bundled one on the **Bootstrap** tab (←/→ to switch). After choosing a deck you pick a mode — **Train** (tiku), **Review** (wrong index), **Continue** (paused scored drill), or **List** (stats) — plus the 1-5 filters/display toggles, a **clear-count** action, and an **Ans mode** toggle that shows answers without scoring.

## Layout

```
src/flip/      engine, TUI, store, config, deck manifest, explain
docs/          schema.md (tiku.json), deck-manifest.md, import.md
decks/example/ minimal demo deck (also a test fixture)
skills/        flip-deck-init — agent skill to bootstrap a deck from source material
tests/         pytest suite, including focused TUI-loop regressions
```

## Acknowledgements

- This project was inspired by [Zhang-Each/SE-FSE-exercise](https://github.com/Zhang-Each/SE-FSE-exercise.git).
- Some of the original `tiku` question data used to build decks for flip was sourced from that project's JSON files.
