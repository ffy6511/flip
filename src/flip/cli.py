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
    add_completion=False,
    no_args_is_help=False,
    help="flip — terminal quiz trainer. Pick a deck and drill.",
)


# ---- `flip` (no args) -> interactive entry menu ----

@app.callback()
def main(ctx: typer.Context):
    """flip — terminal quiz trainer."""
    if ctx.invoked_subcommand is None:
        config = load_config()
        from .engine_loop import entry_menu, run_train
        choice = entry_menu(config)
        if choice is None:
            raise typer.Exit(0)
        deck, review_mode, selector, filters = choice
        raise typer.Exit(run_train(deck, config, selector, review_mode, filters))


# ---- `flip list` ----

@app.command("list")
def list_cmd():
    """List registered decks."""
    config = load_config()
    slugs = list_decks(config.decks_dir)
    if not slugs:
        typer.echo("No decks found under " + str(config.decks_dir))
        typer.echo("Register one with: flip import <slug> <tiku.json>")
        raise typer.Exit(0)
    for slug in slugs:
        try:
            deck = load_deck(config.decks_dir / slug)
            typer.echo(f"{deck.slug:20} {deck.name}")
        except DeckError as exc:
            typer.echo(f"{slug:20} (invalid: {exc})", err=True)


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
    review: bool = typer.Option(False, "--review", help="Review wrong-index instead of tiku."),
    marked: bool = typer.Option(False, "--marked", help="Only marked questions."),
    note: bool = typer.Option(False, "--note", help="Only questions with a note."),
    ai: bool = typer.Option(False, "--ai", help="Only questions with an Agent Said."),
    filter_csv: str = typer.Option(None, "--filter", help="Comma list: mark,note,ai"),
):
    """Train on a deck (or review its wrong-index with --review)."""
    config, deck = _resolve_deck(slug)
    filters = _collect_filters(marked, note, ai, filter_csv)
    from .engine_loop import run_train
    raise typer.Exit(run_train(deck, config, chapter, review, filters))


@deck_app.command("review")
def deck_review(
    slug: str = typer.Argument(...),
    chapter: str = typer.Option(None, "--chapter", "-c"),
    marked: bool = typer.Option(False, "--marked"),
    note: bool = typer.Option(False, "--note"),
    ai: bool = typer.Option(False, "--ai"),
    filter_csv: str = typer.Option(None, "--filter"),
):
    """Browse a deck's questions without scoring."""
    config, deck = _resolve_deck(slug)
    filters = _collect_filters(marked, note, ai, filter_csv)
    from .engine import pick_questions
    from .engine_loop import review_questions
    selected = pick_questions(deck, config, selector=chapter, shuffle=False,
                              filters=filters, source="tiku")
    review_questions(deck, config, selected)
    raise typer.Exit(0)


@deck_app.command("stats")
def deck_stats(slug: str = typer.Argument(...)):
    """Show per-chapter distribution for a deck."""
    config, deck = _resolve_deck(slug)
    from .engine_loop import _run_stats_loop
    _run_stats_loop(deck, config)
    raise typer.Exit(0)


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
    source: Path = typer.Argument(..., exists=True, dir_okay=False,
                                  help="tiku.json or MCQ .csv/.tsv source file."),
    name: str = typer.Option(None, "--name", help="Display name; defaults to slug."),
    source_lang: str = typer.Option("en", "--source-lang"),
    role: str = typer.Option(None, "--role", help="AI persona; defaults to '<name> 助教'."),
    fmt: str = typer.Option(None, "--format", help="Force json|csv; default: by extension."),
    delimiter: str = typer.Option("auto", "--delimiter", help="csv delim: auto/comma/tab/semicolon/pipe."),
    has_header: bool = typer.Option(True, "--header/--no-header", help="CSV has a header row."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing deck."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and preview; write nothing."),
):
    """Register a deck from a JSON or CSV source.

    JSON sources are validated against the tiku schema and copied verbatim.
    CSV sources (see docs/import.md for the MCQ layout) are converted; the
    answer_alphabet is auto-detected from the widest option column.

    Extracting a source from PDF/HTML is the flip-deck-init skill's job.
    """
    import json
    import shutil

    from .importers import import_csv, validate_tiku

    config = load_config()
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
        typer.echo(f"parsed {result.question_count} questions across "
                   f"{len(result.chapters)} chapter(s); alphabet={detected_alphabet}")
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
        qcount = sum(len(qs) for qs in tiku_data.values())
        typer.echo(f"validated {qcount} questions across {len(tiku_data)} chapter(s); "
                   f"alphabet={detected_alphabet}")

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

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_tiku = dest_dir / "tiku.json"
    if fmt_resolved == "csv":
        dest_tiku.write_text(
            json.dumps(tiku_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        shutil.copyfile(source, dest_tiku)
    (dest_dir / "manifest.toml").write_text(manifest, encoding="utf-8")
    typer.echo(f"imported deck {slug} -> {dest_tiku}")
    typer.echo(f"manifest: {dest_dir / 'manifest.toml'}")


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
    letters = set("ABCD")
    for questions in data.values() if isinstance(data, dict) else []:
        for q in questions:
            if isinstance(q, dict):
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
