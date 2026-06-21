"""flip CLI — Typer entry point.

Typer handles only argument parsing, subcommand routing, and --help. The
interactive TUI loops live in flip.engine_loop and are invoked verbatim.
"""

from pathlib import Path

import typer

from . import store
from .config import load_config
from .deck import load_deck, list_decks, DeckError

app = typer.Typer(
    invoke_without_command=True,
    add_completion=True,
    no_args_is_help=False,
    help="flip — terminal quiz trainer. Pick a deck and drill.",
)


# ---- `flip` (no args) -> interactive deck picker -> mode menu ----

@app.callback()
def main(ctx: typer.Context):
    """flip — terminal quiz trainer."""
    if ctx.invoked_subcommand is None:
        config = load_config()
        from .engine_loop import deck_picker, entry_menu, run_train
        # Two-stage flow: pick a deck, then pick a mode/filters for it.
        # Esc at the mode stage returns here to re-pick a deck; only the deck
        # picker quitting exits flip.
        while True:
            deck = deck_picker(config)
            if deck is None:
                raise typer.Exit(0)
            choice = entry_menu(config, deck)
            if choice is None:
                continue  # back to deck picker
            mode, selector, ans_mode, filters = choice
            source = "wrong" if mode == "review" else "tiku"
            raise typer.Exit(run_train(deck, config, selector, source=source,
                                       ans_mode=ans_mode, filters=filters))


# ---- `flip list` ----

@app.command("list")
def list_cmd():
    """List registered decks."""
    from . import store

    config = load_config()
    slugs = list_decks(config.decks_dir)
    if not slugs:
        typer.echo("No decks found under " + str(config.decks_dir))
        typer.echo("Register one with: flip import <slug> <tiku.json>")
        raise typer.Exit(0)

    header_line, body_lines = store.format_table(store.deck_rows(config))
    typer.echo(header_line)
    for line in body_lines:
        typer.echo(line)


# ---- `flip deck <slug> ...` (nested group) ----

deck_app = typer.Typer(help="Per-deck commands: train, review, stats, translate.")
app.add_typer(deck_app, name="deck")


def _resolve_deck(slug: str):
    config = load_config()
    try:
        return config, load_deck(config.decks_dir / slug)
    except DeckError as exc:
        typer.echo(f"deck error: {exc}", err=True)
        raise typer.Exit(1)


@deck_app.command("train")
def deck_train(
    slug: str = typer.Argument(..., help="Deck slug, e.g. `se`."),
    chapter: str = typer.Option(None, "--chapter", "-c", help="e.g. 5, 5-10, -3"),
    ans: bool = typer.Option(False, "--ans", help="Browse mode: show answers, no scoring."),
    marked: bool = typer.Option(False, "--marked", help="Only marked questions."),
    note: bool = typer.Option(False, "--note", help="Only questions with a note."),
    ai: bool = typer.Option(False, "--ai", help="Only questions with an Agent Said."),
    filter_csv: str = typer.Option(None, "--filter", help="Comma list: mark,note,ai"),
):
    """Train on a deck's full question bank (source = tiku).

    With --ans, runs in browse mode: answers are shown immediately and nothing
    is scored. Corresponds to the entry-menu 'Train' entry.
    """
    config, deck = _resolve_deck(slug)
    filters = _collect_filters(marked, note, ai, filter_csv)
    from .engine_loop import run_train
    raise typer.Exit(run_train(deck, config, chapter, source="tiku",
                                ans_mode=ans, filters=filters))


@deck_app.command("review")
def deck_review(
    slug: str = typer.Argument(..., help="Deck slug, e.g. `se`."),
    chapter: str = typer.Option(None, "--chapter", "-c", help="e.g. 5, 5-10, -3"),
    ans: bool = typer.Option(False, "--ans", help="Browse mode: show answers, no scoring."),
    marked: bool = typer.Option(False, "--marked", help="Only marked questions."),
    note: bool = typer.Option(False, "--note", help="Only questions with a note."),
    ai: bool = typer.Option(False, "--ai", help="Only questions with an Agent Said."),
    filter_csv: str = typer.Option(None, "--filter", help="Comma list: mark,note,ai"),
):
    """Drill a deck's wrong index (source = wrong).

    Trains on exactly the questions previously answered wrong. With --ans,
    browses them with answers shown instead of scoring. Corresponds to the
    entry-menu 'Review' entry.
    """
    config, deck = _resolve_deck(slug)
    filters = _collect_filters(marked, note, ai, filter_csv)
    from .engine_loop import run_train
    raise typer.Exit(run_train(deck, config, chapter, source="wrong",
                                ans_mode=ans, filters=filters))


@deck_app.command("stats")
def deck_stats(slug: str = typer.Argument(...)):
    """Show per-chapter distribution for a deck."""
    config, deck = _resolve_deck(slug)
    from .engine_loop import _run_stats_loop
    _run_stats_loop(deck, config)
    raise typer.Exit(0)


@deck_app.command("mark")
def deck_mark(
    slug: str = typer.Argument(..., help="Deck slug."),
    chapter: str = typer.Option(None, "--chapter", "-c", help="Filter to one chapter."),
):
    """List a deck's marked questions (read-only).

    Intended for agents and quick inspection: prints one line per marked
    question (chapter, topic, marked_at). Mutating marks happens in the TUI.
    """
    config, deck = _resolve_deck(slug)
    from . import store as _store
    marked = _store.load_marked(deck)
    rows = []
    for rec in marked:
        ch, topic, extra = _store.index_summary(rec)
        if chapter is not None and str(ch) != str(chapter):
            continue
        rows.append((ch, topic, extra.get("marked_at", "")))
    if not rows:
        typer.echo("(no marked questions)")
        raise typer.Exit(0)
    typer.echo(f"{len(rows)} marked:")
    for ch, topic, when in rows:
        typer.echo(f"  ch{ch}  [{when}]  {topic[:60]}")


@deck_app.command("wrong")
def deck_wrong(
    slug: str = typer.Argument(..., help="Deck slug."),
    chapter: str = typer.Option(None, "--chapter", "-c", help="Filter to one chapter."),
):
    """List a deck's wrong-index questions (read-only).

    Intended for agents and quick inspection: prints one line per previously
    wrong answer (chapter, topic, what you answered). Clearing happens in the
    TUI (`r` key) or by deleting wrong/*.json.
    """
    config, deck = _resolve_deck(slug)
    from . import store as _store
    rows = []
    for path in _store.wrong_files(deck):
        for rec in _store.read_json(path, default=[]):
            ch, topic, extra = _store.index_summary(rec)
            if chapter is not None and str(ch) != str(chapter):
                continue
            rows.append((ch, topic, extra.get("wrong_input", ""), extra.get("wrong_answer", "")))
    if not rows:
        typer.echo("(no wrong-index questions)")
        raise typer.Exit(0)
    typer.echo(f"{len(rows)} wrong:")
    for ch, topic, inp, ans in rows:
        typer.echo(f"  ch{ch}  你答={inp}  {topic[:55]}")


@deck_app.command("translate")
def deck_translate(
    slug: str = typer.Argument(...),
    chapter: str = typer.Option(None, "--chapter", "-c"),
    force: bool = typer.Option(False, "--force", help="Re-translate even if zh exists."),
):
    """Fill the zh field for questions that lack it."""
    config, deck = _resolve_deck(slug)
    from .engine_loop import run_translate
    raise typer.Exit(run_translate(deck, config, selector=chapter, force=force))


@deck_app.command("remove")
def deck_remove(
    slug: str = typer.Argument(..., help="Deck slug to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Delete a deck and all its data (tiku, marked, wrong indices).

    This is irreversible: the deck directory
    `~/.local/share/flip/decks/<slug>/` is removed entirely. The source file
    you imported from is never touched.
    """
    import shutil

    config = load_config()
    deck_dir = config.decks_dir / slug
    if not deck_dir.is_dir():
        typer.echo(f"no such deck: {slug} (looked in {deck_dir})", err=True)
        raise typer.Exit(1)

    # Show what's about to go so the user can sanity-check before confirming.
    try:
        from .deck import load_deck
        deck = load_deck(deck_dir)
        label = f"{deck.name} ({deck.slug})"
    except Exception:
        label = slug
    typer.echo(f"About to permanently delete deck {label!r}:")
    typer.echo(f"  {deck_dir}")

    if not yes:
        # Red confirmation prompt — but only emit ANSI when stderr is a tty,
        # otherwise the raw escape codes would show up in logs/redirects.
        import sys
        RED = "\033[31m" if sys.stderr.isatty() else ""
        RESET = "\033[0m" if sys.stderr.isatty() else ""
        confirm = typer.confirm(
            f"{RED}Delete this deck? This cannot be undone.{RESET}",
            default=False,
        )
        if not confirm:
            typer.echo("aborted.")
            raise typer.Exit(0)

    shutil.rmtree(deck_dir)
    typer.echo(f"deleted deck {slug}.")


def _collect_filters(marked, note, ai, filter_csv):
    filters = []
    if marked:
        filters.append("mark")
    if note:
        filters.append("note")
    if ai:
        filters.append("ai")
    if filter_csv:
        filters.extend(part.strip() for part in filter_csv.split(",") if part.strip())
    return filters


# ---- `flip import <slug> <source.json>` (Phase 1: register a compliant JSON) ----

@app.command("import")
def import_cmd(
    slug: str = typer.Argument(..., help="New deck slug, e.g. `compiler`."),
    source: Path = typer.Argument(..., exists=True,
                                  help="A tiku.json / MCQ .csv/.tsv file, OR a deck "
                                       "directory (must contain tiku.json; optional "
                                       "marked.json and wrong/ are migrated too)."),
    name: str = typer.Option(None, "--name", help="Display name; defaults to slug."),
    source_lang: str = typer.Option("en", "--source-lang"),
    role: str = typer.Option(None, "--role", help="AI persona; defaults to '<name> 助教'."),
    fmt: str = typer.Option(None, "--format", help="Force json|csv; ignored for directory input."),
    delimiter: str = typer.Option("auto", "--delimiter", help="csv delim: auto/comma/tab/semicolon/pipe."),
    has_header: bool = typer.Option(True, "--header/--no-header", help="CSV has a header row."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing deck."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and preview; write nothing."),
):
    """Register a deck from a JSON / CSV file or a deck directory.

    File inputs (json/csv) are validated/converted as before. A *directory*
    input migrates a whole deck folder: its `tiku.json` (required) is
    validated and copied; `marked.json` and `wrong/`, if present, are copied
    verbatim so learner history survives the move. The old
    `marked_questions.json` name is not recognized — rename it first.

    Extracting a source from PDF/HTML is the flip-deck-init skill's job.
    """
    import json
    from .importers import import_csv, validate_tiku
    from . import engine as _engine

    config = load_config()

    # ---- directory mode: tiku.json from the dir, plus marked/wrong if present ----
    is_dir_mode = source.is_dir()
    if is_dir_mode:
        src_dir = source
        tiku_file = src_dir / "tiku.json"
        if not tiku_file.is_file():
            typer.echo(f"directory has no tiku.json: {src_dir}", err=True)
            raise typer.Exit(1)
        try:
            tiku_data = json.loads(tiku_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            typer.echo(f"invalid tiku.json: {exc}", err=True)
            raise typer.Exit(1)
        errs = validate_tiku(tiku_data)
        if errs:
            typer.echo(f"tiku validation failed ({len(errs)} errors):", err=True)
            for e in errs[:20]:
                typer.echo(f"  {e}", err=True)
            raise typer.Exit(1)
        detected_alphabet = _detect_alphabet_from_tiku(tiku_data)
        assigned_ids = _engine.ensure_question_ids(tiku_data, prefix=slug)
        qcount = sum(1 for _ in _engine.iter_question_records(tiku_data))
        chcount = len({ch for ch, _ in _engine.iter_question_records(tiku_data)})
        typer.echo(f"validated {qcount} questions across {chcount} chapter(s); "
                   f"alphabet={detected_alphabet}; assigned_ids={assigned_ids}")
        # If the source dir has a manifest, prefer its name/source_lang as defaults.
        src_manifest = src_dir / "manifest.toml"
        if src_manifest.is_file():
            from ._toml import load_toml
            mdata = load_toml(src_manifest).get("deck", {})
            name = name or mdata.get("name") or None
            source_lang = mdata.get("source_lang", source_lang)
            role = role or mdata.get("role") or None
    else:
        fmt_resolved = _resolve_import_format(source, fmt)
        if fmt_resolved == "csv":
            result = import_csv(
                source, delimiter=delimiter, has_header=has_header,
                translation_enabled=config.translation_enabled,
            )
            if result.errors:
                typer.echo(f"CSV import failed ({len(result.errors)} errors):", err=True)
                for line_no, msg in result.errors[:20]:
                    where = f"line {line_no}: " if line_no else ""
                    typer.echo(f"  {where}{msg}", err=True)
                raise typer.Exit(1)
            tiku_data = result.chapters
            detected_alphabet = result.answer_alphabet
            assigned_ids = _engine.ensure_question_ids(tiku_data, prefix=slug)
            qcount = sum(1 for _ in _engine.iter_question_records(tiku_data))
            chcount = len({ch for ch, _ in _engine.iter_question_records(tiku_data)})
            typer.echo(f"parsed {qcount} questions across "
                       f"{chcount} chapter(s); alphabet={detected_alphabet}; "
                       f"assigned_ids={assigned_ids}")
        else:
            try:
                tiku_data = json.loads(source.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                typer.echo(f"invalid JSON: {exc}", err=True)
                raise typer.Exit(1)
            errs = validate_tiku(tiku_data)
            if errs:
                typer.echo(f"tiku validation failed ({len(errs)} errors):", err=True)
                for e in errs[:20]:
                    typer.echo(f"  {e}", err=True)
                raise typer.Exit(1)
            detected_alphabet = _detect_alphabet_from_tiku(tiku_data)
            assigned_ids = _engine.ensure_question_ids(tiku_data, prefix=slug)
            qcount = sum(1 for _ in _engine.iter_question_records(tiku_data))
            chcount = len({ch for ch, _ in _engine.iter_question_records(tiku_data)})
            typer.echo(f"validated {qcount} questions across {chcount} chapter(s); "
                       f"alphabet={detected_alphabet}; assigned_ids={assigned_ids}")

    dest_dir = config.decks_dir / slug
    if dest_dir.exists() and not force and not dry_run:
        typer.echo(f"deck already exists: {dest_dir} (use --force)", err=True)
        raise typer.Exit(1)

    display_name = name or slug
    role_text = role or f"{display_name} 助教"
    manifest = _build_manifest_text(
        slug=slug, display_name=display_name, source_lang=source_lang,
        answer_alphabet=detected_alphabet, role_text=role_text,
    )

    if dry_run:
        typer.echo("--- dry run: manifest preview ---")
        typer.echo(manifest)
        typer.echo("--- dry run: nothing written ---")
        raise typer.Exit(0)

    from . import store
    from .deck import Deck
    deck = Deck(
        slug=slug, name=display_name, path=dest_dir,
        source_lang=source_lang, answer_alphabet=detected_alphabet,
    )

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_tiku = dest_dir / "tiku.json"
    if is_dir_mode:
        report = store.import_dir(source, deck)
        store.save_tiku(deck, tiku_data)
        (dest_dir / "manifest.toml").write_text(manifest, encoding="utf-8")
        typer.echo(f"imported deck {slug} -> {dest_tiku} (from directory {source})")
        extra = []
        if report["marked"]:
            extra.append("marked.json")
        if report["wrong_files"]:
            extra.append(f"wrong/{report['wrong_files']} file(s)")
        if extra:
            typer.echo(f"migrated: {', '.join(extra)}")
        typer.echo(f"manifest: {dest_dir / 'manifest.toml'}")
    else:
        store.write_json(dest_tiku, tiku_data)
        (dest_dir / "manifest.toml").write_text(manifest, encoding="utf-8")
        typer.echo(f"imported deck {slug} -> {dest_tiku}")
        typer.echo(f"manifest: {dest_dir / 'manifest.toml'}")


@app.command("export")
def export_cmd(
    slug: str = typer.Argument(..., help="Deck slug to export."),
    out: Path = typer.Option(
        None, "--out", "-o",
        help="Destination directory (default: ./<slug>-deck/ in the cwd).",
    ),
):
    """Bundle a deck into a directory (the inverse of `flip import <dir>`).

    Copies tiku.json, manifest.toml, marked.json (if any), and the whole
    wrong/ directory. The result can be re-imported on another machine with
    `flip import <slug> <dir>`.
    """
    config, deck = _resolve_deck(slug)
    from . import store

    dest = Path(out) if out else Path.cwd() / f"{slug}-deck"
    try:
        result = store.export_deck(deck, dest)
    except FileExistsError as exc:
        typer.echo(f"export failed: {exc} (use a different --out)", err=True)
        raise typer.Exit(1)
    typer.echo(f"exported deck {slug} -> {result}")
    parts = []
    if deck.marked_path.is_file():
        parts.append("marked.json")
    if deck.wrong_dir.is_dir():
        parts.append(f"wrong/ ({len(store.wrong_files(deck))} file(s))")
    if parts:
        typer.echo("included: " + ", ".join(parts))


def _resolve_import_format(source: Path, fmt):
    if fmt:
        if fmt not in {"json", "csv"}:
            raise typer.BadParameter(f"--format must be json|csv, got {fmt!r}")
        return fmt
    suffix = source.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        return "csv"
    if suffix == ".json":
        return "json"
    raise typer.BadParameter(
        f"cannot infer format from extension {suffix!r}; pass --format json|csv"
    )


def _detect_alphabet_from_tiku(data):
    """Find the widest option set across all questions; default ABCD."""
    from . import engine as _engine
    letters = set("ABCD")
    for _, q in _engine.iter_question_records(data):
        for opt in q.get("options", []):
            if isinstance(opt, str) and opt:
                letters.add(opt[0].upper())
    # Keep only A-J, sort.
    valid = sorted(l for l in letters if l in "ABCDEFGHIJ")
    return "".join(valid) if valid else "ABCD"


def _build_manifest_text(*, slug, display_name, source_lang, answer_alphabet, role_text):
    return (
        '[deck]\n'
        f'name = "{display_name}"\n'
        f'slug = "{slug}"\n'
        f'source_lang = "{source_lang}"\n'
        f'answer_alphabet = "{answer_alphabet}"\n'
        '\n'
        '[explain]\n'
        f'role = "{role_text}"\n'
        'max_chars = 200\n'
        '# default_model and model_env override the global [explain].model.\n'
    )


# ---- `flip config` ----

@app.command("config")
def config_cmd():
    """Show the resolved global config and backend status."""
    import shutil
    config = load_config()

    typer.echo(f"home:       {config.home}")
    typer.echo(f"config:     {config.config_path}")
    typer.echo(f"decks dir:  {config.decks_dir}")
    typer.echo(f"languages:  {config.source_lang} -> {config.target_lang} "
               f"(translation {'on' if config.translation_enabled else 'off'})")
    typer.echo("")
    typer.echo("[explain]")
    typer.echo(f"  command: {config.explain.command}")
    typer.echo(f"  model:   {config.explain.model}")
    typer.echo(f"  output:  {config.explain.output}")
    typer.echo(f"  timeout: {config.explain.timeout}s")

    errs = config.validate()
    if errs:
        typer.echo("")
        typer.echo("config errors:", err=True)
        for e in errs:
            typer.echo(f"  {e}", err=True)
        raise typer.Exit(1)

    from .backends import which_backend
    backend = which_backend(config.explain)
    on_path = shutil.which(backend) if backend else None
    typer.echo("")
    if backend is None:
        typer.echo("backend:    (could not parse command template)", err=True)
    elif on_path:
        typer.echo(f"backend:    {backend} ({on_path})")
    else:
        typer.echo(f"backend:    {backend}  ⚠ not on PATH", err=True)


if __name__ == "__main__":
    app()
