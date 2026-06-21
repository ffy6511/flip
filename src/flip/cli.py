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
    source: Path = typer.Argument(..., exists=True, dir_okay=False, help="Compliant tiku.json."),
    name: str = typer.Option(None, "--name", help="Display name; defaults to slug."),
    source_lang: str = typer.Option("en", "--source-lang"),
    role: str = typer.Option(None, "--role", help="AI persona; defaults to '<name> 助教'."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing deck."),
):
    """Register a compliant tiku.json as a new deck under ~/.local/share/flip/decks/.

    This command only does mechanical bookkeeping (copy file, write manifest).
    Extracting tiku.json from PDF/HTML source is the flip-deck-init skill's job.
    """
    import shutil
    config = load_config()
    dest_dir = config.decks_dir / slug
    if dest_dir.exists() and not force:
        typer.echo(f"deck already exists: {dest_dir} (use --force)", err=True)
        raise typer.Exit(1)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_tiku = dest_dir / "tiku.json"
    shutil.copyfile(source, dest_tiku)

    display_name = name or slug
    role_text = role or f"{display_name} 助教"
    manifest = (
        '[deck]\n'
        f'name = "{display_name}"\n'
        f'slug = "{slug}"\n'
        f'source_lang = "{source_lang}"\n'
        'answer_alphabet = "ABCD"\n'
        '\n'
        '[explain]\n'
        f'role = "{role_text}"\n'
        'max_chars = 200\n'
    )
    (dest_dir / "manifest.toml").write_text(manifest, encoding="utf-8")
    typer.echo(f"imported deck {slug} -> {dest_tiku}")
    typer.echo(f"manifest written: {dest_dir / 'manifest.toml'}")
    typer.echo("Edit the manifest to tune answer_alphabet / role / model.")


if __name__ == "__main__":
    app()
