"""flip CLI — Typer entry point.

Typer handles only argument parsing, subcommand routing, and --help. The
interactive TUI loops live in flip.engine_loop and are invoked verbatim.
"""

from pathlib import Path
from typing import Optional

import typer

from . import store
from .config import load_config
from .deck import load_deck, list_decks, DeckError


def _version() -> str:
    """Return the installed package version, read from packaging metadata.

    Single source of truth: pyproject.toml's `version`. Falls back to "0.0.0"
    when the package isn't installed (e.g. running from a source checkout
    without metadata) so `--version` never crashes.
    """
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("flip")
        except PackageNotFoundError:
            return "0.0.0"
    except ImportError:  # py < 3.8 without importlib_metadata backport
        return "0.0.0"


app = typer.Typer(
    invoke_without_command=True,
    add_completion=True,
    no_args_is_help=False,
    help="flip — a terminal quiz tool powered by skill-driven agent.",
)


# ---- `flip` (no args) -> interactive deck picker -> mode menu ----

@app.callback()
def main(
    ctx: typer.Context,
    show_version: bool = typer.Option(
        False, "--version", "-V", help="Show the flip version and exit.",
        is_eager=True,
    ),
):
    """flip — a terminal quiz tool powered by skill-driven agent."""
    if show_version:
        typer.echo(_version())
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        config = load_config()
        from .engine_loop import deck_picker, entry_menu, run_continue, run_train, BACK_TO_SELECTOR
        # Two-stage flow: pick a deck, then pick a mode/filters for it.
        # Esc at the mode stage returns here to re-pick a deck; only the deck
        # picker quitting exits flip.
        while True:
            deck = deck_picker(config)
            if deck is None:
                raise typer.Exit(0)
        # `resume` carries (mode_index, ans_mode, filters) for explicit
        # back-to-picker flows. Normal in-question Esc now exits like q and
        # keeps the session for `continue`. None on first entry.
            resume = None
            while True:
                choice = entry_menu(config, deck, resume=resume)
                if choice is None:
                    break  # back to deck picker
                mode, selector, ans_mode, filters = choice
                if mode == "continue":
                    outcome = run_continue(deck, config)
                else:
                    source = "wrong" if mode == "review" else "tiku"
                    outcome = run_train(deck, config, selector, source=source,
                                        ans_mode=ans_mode, filters=filters)
                if outcome == BACK_TO_SELECTOR:
                    if mode == "continue":
                        resume = None
                        continue
                    # Esc pressed: re-enter entry_menu at the chapter picker,
                    # keep mode/ans/filters, clear chapters.
                    mode_index = 0 if mode == "train" else 1
                    resume = (mode_index, ans_mode, filters)
                    continue
                # Normal completion: loop back to entry_menu so the user can
                # pick a different chapter range / mode / deck section without
                # relaunching flip. (Direct subcommands like `flip deck train`
                # still exit after one pass — only the interactive `flip` entry
                # point loops.)
                resume = None
                continue
            resume = None


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


@app.command("doctor")
def doctor_cmd(
    slug: Optional[str] = typer.Argument(None, help="Deck slug. Omit to inspect every deck."),
):
    """Inspect deck compatibility issues and print repair commands."""
    from .doctor import build_doctor_report, format_report

    config = load_config()
    slugs = [slug] if slug else list_decks(config.decks_dir)
    if not slugs:
        typer.echo("No decks found under " + str(config.decks_dir))
        raise typer.Exit(0)

    for index, item in enumerate(slugs):
        if index:
            typer.echo()
        try:
            deck = load_deck(config.decks_dir / item)
        except DeckError as exc:
            _status_echo(f"deck error: {exc}", ok=False, err=True)
            if slug:
                raise typer.Exit(1)
            continue
        for line in format_report(build_doctor_report(deck)):
            typer.echo(line)


# ---- `flip deck <slug> ...` (nested group) ----

deck_app = typer.Typer(help="Per-deck commands: train, review, stats, merge, repair, translate.")
app.add_typer(deck_app, name="deck")


def _resolve_deck(slug: str):
    config = load_config()
    try:
        return config, load_deck(config.decks_dir / slug)
    except DeckError as exc:
        _status_echo(f"deck error: {exc}", ok=False, err=True)
        raise typer.Exit(1)


def _status_echo(message: str, *, ok: bool = True, err: bool = False):
    color = typer.colors.GREEN if ok else typer.colors.RED
    typer.secho(message, fg=color, err=err)


def _run_deck_train_after_esc(deck, config, mode, ans_mode, filters):
    """Loop a fresh entry_menu after an explicit back-to-picker action.

    Called only after a `deck train`/`deck review` pass returns BACK_TO_SELECTOR.
    Re-enters the chapter picker (chapters cleared) preserving the mode/ans/
    filters that came from the command line; subsequent back-to-picker actions keep looping
    here with whatever flags the learner last picked. Finishing a run or
    quitting the menu exits flip normally.
    """
    from .engine_loop import run_continue, run_train, entry_menu, BACK_TO_SELECTOR
    mode_index = 0 if mode == "train" else 1
    resume = (mode_index, ans_mode, filters)
    while True:
        choice = entry_menu(config, deck, resume=resume)
        if choice is None:
            raise typer.Exit(0)
        ch_mode, selector, ch_ans, ch_filters = choice
        if ch_mode == "continue":
            outcome = run_continue(deck, config)
        else:
            source = "wrong" if ch_mode == "review" else "tiku"
            outcome = run_train(deck, config, selector, source=source,
                                ans_mode=ch_ans, filters=ch_filters)
        if outcome == BACK_TO_SELECTOR:
            if ch_mode == "continue":
                resume = None
                continue
            mode_index = 0 if ch_mode == "train" else 1
            resume = (mode_index, ch_ans, ch_filters)
            continue
        raise typer.Exit(outcome)


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
    is scored. Corresponds to the entry-menu 'Train' entry. Pressing Esc
    mid-question exits like q and preserves the session for `flip deck continue`.
    """
    config, deck = _resolve_deck(slug)
    filters = _collect_filters(marked, note, ai, filter_csv)
    from .engine_loop import run_train, BACK_TO_SELECTOR
    # chapter is honored only on the first pass; explicit back-to-picker flows
    # re-run via the interactive chapter picker and ignore the original --chapter.
    outcome = run_train(deck, config, chapter, source="tiku",
                        ans_mode=ans, filters=filters)
    if outcome == BACK_TO_SELECTOR:
        _run_deck_train_after_esc(deck, config, "train", ans, filters)
    else:
        raise typer.Exit(outcome)


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
    entry-menu 'Review' entry. Pressing Esc mid-question exits like q and
    preserves the session for `flip deck continue`.
    """
    config, deck = _resolve_deck(slug)
    filters = _collect_filters(marked, note, ai, filter_csv)
    from .engine_loop import run_train, BACK_TO_SELECTOR
    outcome = run_train(deck, config, chapter, source="wrong",
                        ans_mode=ans, filters=filters)
    if outcome == BACK_TO_SELECTOR:
        _run_deck_train_after_esc(deck, config, "review", ans, filters)
    else:
        raise typer.Exit(outcome)


@deck_app.command("continue")
def deck_continue(slug: str = typer.Argument(..., help="Deck slug, e.g. `se`.")):
    """Continue the latest paused scored drill for a deck."""
    config, deck = _resolve_deck(slug)
    from .engine_loop import run_continue
    raise typer.Exit(run_continue(deck, config))


@deck_app.command("stats")
def deck_stats(slug: str = typer.Argument(...)):
    """Show per-chapter distribution for a deck."""
    config, deck = _resolve_deck(slug)
    from .engine_loop import _run_stats_loop
    _run_stats_loop(deck, config)
    raise typer.Exit(0)


@deck_app.command("clear-count")
def deck_clear_count(
    slug: str = typer.Argument(..., help="Deck slug."),
    mode: str = typer.Option("all", "--mode", help="train|review|all."),
):
    """Clear stored drill-count history for a deck.

    This only updates history.json. It does not touch tiku.json, wrong/ indexes,
    marked.json, or the paused session checkpoint.
    """
    _config, deck = _resolve_deck(slug)
    mode = (mode or "all").lower()
    if mode not in {"train", "review", "all"}:
        _status_echo("mode must be one of: train, review, all", ok=False, err=True)
        raise typer.Exit(1)
    store.clear_history_mode(deck, mode)
    _status_echo(f"cleared {mode} count: {slug}")


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


@deck_app.command("merge")
def deck_merge(
    slug: str = typer.Argument(..., help="Deck slug."),
    source: Path = typer.Argument(..., exists=True, help="tiku.json / CSV / deck directory to merge."),
    policy: str = typer.Option("append", "--policy", help="append|upsert|overwrite."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing."),
    backup: bool = typer.Option(True, "--backup/--no-backup", help="Export a backup before writing."),
    fmt: str = typer.Option(None, "--format", help="Force json|csv; ignored for directory input."),
    delimiter: str = typer.Option("auto", "--delimiter", help="csv delim: auto/comma/tab/semicolon/pipe."),
    has_header: bool = typer.Option(True, "--header/--no-header", help="CSV has a header row."),
):
    """Incrementally merge questions into an existing deck."""
    import datetime

    config, deck = _resolve_deck(slug)
    from . import bootstrap
    from . import engine as _engine
    from . import store as _store
    from .merge import POLICIES, merge_tiku

    if policy not in POLICIES:
        _status_echo(
            f"unknown policy: {policy} (choose: {', '.join(sorted(POLICIES))})",
            ok=False,
            err=True,
        )
        raise typer.Exit(1)

    try:
        incoming = _load_tiku_source(source, config, fmt=fmt, delimiter=delimiter, has_header=has_header)
    except (ValueError, FileNotFoundError) as exc:
        _status_echo(f"merge source error: {exc}", ok=False, err=True)
        raise typer.Exit(1)

    base = _store.load_tiku(deck) or {}
    result = merge_tiku(base, incoming, policy=policy, prefix=slug)
    merged_alphabet = _detect_alphabet_from_tiku(result.data)
    alphabet_changed = merged_alphabet != deck.answer_alphabet

    _status_echo(
        "merge preview: "
        f"added={result.added}, updated={result.updated}, skipped={result.skipped}, "
        f"assigned_ids={result.assigned_ids}, titles={result.title_updates}, "
        f"conflicts={len(result.conflicts)}",
        ok=not result.conflicts,
    )
    if alphabet_changed:
        typer.echo(f"answer_alphabet: {deck.answer_alphabet} -> {merged_alphabet}")
    if result.conflicts:
        _status_echo("conflicts:", ok=False, err=True)
        for conflict in result.conflicts[:20]:
            typer.echo(f"  {conflict}", err=True)
        raise typer.Exit(1)

    if dry_run:
        typer.echo("dry run: nothing written")
        raise typer.Exit(0)

    if backup:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = config.home / "backups" / f"{slug}-deck-{stamp}"
        _store.export_deck(deck, backup_dir)
        bootstrap._write_backup_meta(
            backup_dir,
            slug=slug,
            content_version=deck.content_version,
            op="merge",
            timestamp=stamp,
        )
        typer.echo(f"backup: {backup_dir}")

    _store.save_tiku(deck, result.data)
    _engine._sync_marked_from_tiku(deck)
    if alphabet_changed:
        _update_manifest_answer_alphabet(deck, merged_alphabet)
    _status_echo(f"merged deck {slug}")


@deck_app.command("assign-ids")
def deck_assign_ids(
    slug: str = typer.Argument(..., help="Deck slug."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview how many ids would be assigned."),
):
    """Assign stable UUID ids to every question missing one.

    Ids already present are preserved verbatim. Use this on a freshly-authored
    deck before it ships, or to backfill ids on an old deck. Ids are
    content-independent (`q-<12hex>`): once assigned, never reassign — that's
    what keeps marks/notes/wrong-history attached across content edits.
    """
    config, deck = _resolve_deck(slug)
    from . import engine as _engine
    from . import store as _store

    data = _store.load_tiku(deck) or {}
    missing_before = sum(1 for _, q in _engine.iter_question_records(data)
                         if not _engine.question_id(q))
    if dry_run:
        typer.echo(f"would assign {missing_before} new id(s); existing ids preserved")
        raise typer.Exit(0)
    if missing_before == 0:
        _status_echo(f"all questions already have ids; nothing to do")
        raise typer.Exit(0)
    added = _engine.ensure_question_ids(data, prefix=slug)
    _store.save_tiku(deck, data)
    _status_echo(f"assigned {added} new id(s) to deck {slug}")


@deck_app.command("migrate")
def deck_migrate(
    slug: str = typer.Argument(..., help="Deck slug."),
    ids: bool = typer.Option(False, "--ids", help="Normalize missing/legacy question ids to UUID ids."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview migrations without writing."),
):
    """Migrate older deck schema details with a full backup before writes."""
    if not ids:
        _status_echo("no migration selected; pass --ids", ok=False, err=True)
        raise typer.Exit(1)

    import datetime
    from . import bootstrap
    from .doctor import LEGACY_POSITIONAL_RE, UUID_RE
    from . import engine as _engine
    from . import store as _store

    config, deck = _resolve_deck(slug)
    data = _store.load_tiku(deck) or {}
    used_ids = {
        _engine.question_id(q)
        for _, q in _engine.iter_question_records(data)
        if _engine.question_id(q)
    }
    missing = 0
    migrated = 0
    id_map = {}
    for _chapter, q in _engine.iter_question_records(data):
        qid = _engine.question_id(q)
        should_assign = not qid
        should_migrate = bool(qid and LEGACY_POSITIONAL_RE.fullmatch(qid))
        if not should_assign and not should_migrate:
            continue
        if should_assign:
            missing += 1
        else:
            migrated += 1
        candidate = _engine.generate_question_id()
        while candidate in used_ids or not UUID_RE.fullmatch(candidate):
            candidate = _engine.generate_question_id()
        if qid:
            id_map[qid] = candidate
            used_ids.discard(qid)
        q["id"] = candidate
        used_ids.add(candidate)

    total = missing + migrated
    if dry_run:
        typer.echo(
            f"would assign {missing} missing id(s) and migrate {migrated} legacy id(s)"
        )
        raise typer.Exit(0)
    if total == 0:
        _status_echo(f"deck {slug} already uses stable ids; nothing to migrate")
        raise typer.Exit(0)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = config.home / "backups" / f"{slug}-migrate-{stamp}"
    _store.export_deck(deck, backup_dir)
    bootstrap._write_backup_meta(
        backup_dir,
        slug=slug,
        content_version=deck.content_version,
        op="migrate",
        timestamp=stamp,
    )
    typer.echo(f"backup: {backup_dir}")

    _store.save_tiku(deck, data)
    if id_map:
        bootstrap._rewrite_index_keys(deck, id_map)
    _engine._sync_marked_from_tiku(deck)
    _status_echo(f"assigned {missing} stable id(s)")
    if migrated:
        _status_echo(f"migrated {migrated} legacy id(s)")


@deck_app.command("update")
def deck_update(
    slug: str = typer.Argument(..., help="Bundled deck slug to update."),
    overwrite_notes: bool = typer.Option(False, "--overwrite-notes", help="Allow bundled user_note to overwrite local notes for this update."),
):
    """Update an installed bundled deck to the shipped content version.

    Pulls the bundled tiku as the incoming source and merges it into the local
    deck by question id (upsert), so marks, notes, ai_explanations and the
    wrong-index survive. Legacy positional ids are migrated to UUIDs first.
    A full-deck backup is written before any change. Use this after a maintainer
    bumps a bundled deck's content_version.
    """
    from . import bootstrap

    config, deck = _resolve_deck(slug)
    if slug not in bootstrap._bundled_slugs():
        _status_echo(f"{slug!r} is not a bundled deck; update only applies to bundled decks", ok=False, err=True)
        raise typer.Exit(1)

    upd = bootstrap.updatable_bundled_decks(config.decks_dir)
    if not any(u["slug"] == slug for u in upd):
        _status_echo(f"{slug} is already up to date (content_version={deck.content_version})")
        raise typer.Exit(0)

    result = bootstrap.update_bundled(slug, config.decks_dir, overwrite_notes=overwrite_notes)
    typer.echo(f"backup: {result.backup_dir}")
    _status_echo(
        "update preview: "
        f"added={result.added}, updated={result.updated}, skipped={result.skipped}, "
        f"assigned_ids={result.assigned_ids}, conflicts={len(result.conflicts)}",
        ok=not result.conflicts,
    )
    if result.conflicts:
        _status_echo("conflicts:", ok=False, err=True)
        for conflict in result.conflicts[:20]:
            typer.echo(f"  {conflict}", err=True)
    if result.unmigrated:
        _status_echo(
            f"{len(result.unmigrated)} question(s) could not be migrated to new ids "
            "(content changed upstream); their history is orphaned:",
            ok=False, err=True,
        )
        for chapter, qid, topic in result.unmigrated[:20]:
            typer.echo(f"  chapter {chapter}: id={qid}, topic={topic!r}", err=True)
    _status_echo(f"updated deck {slug} to content_version={bootstrap._read_bundled_metadata(slug)['content_version']}")


@deck_app.command("prune")
def deck_prune(
    slug: str = typer.Argument(..., help="Bundled deck slug to prune."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Remove local questions that no longer exist in the bundled source.

    After `flip deck update`, questions the maintainer deleted upstream remain
    in the local deck (merge never deletes). This command drops them. A backup
    is written first; the action is irreversible.
    """
    from . import bootstrap

    config, deck = _resolve_deck(slug)
    if slug not in bootstrap._bundled_slugs():
        _status_echo(f"{slug!r} is not a bundled deck; prune only applies to bundled decks", ok=False, err=True)
        raise typer.Exit(1)

    from . import engine as _engine
    from . import store as _store
    import datetime

    local = _store.load_tiku(deck) or {}
    bundled = __import__("json").loads(bootstrap._read_bundled_tiku_text(slug))
    bundled_ids = {_engine.question_id(q) for _, q in _engine.iter_question_records(bundled) if _engine.question_id(q)}
    orphaned = [(ch, q) for ch, q in _engine.iter_question_records(local)
                if _engine.question_id(q) and _engine.question_id(q) not in bundled_ids]

    if not orphaned:
        _status_echo(f"no orphaned questions in {slug}; nothing to prune")
        raise typer.Exit(0)

    _status_echo(f"{len(orphaned)} question(s) in {slug} are no longer in the bundled source:")
    for ch, q in orphaned[:20]:
        typer.echo(f"  chapter {ch}: id={_engine.question_id(q)}, topic={str(q.get('topic',''))[:80]!r}")
    if not yes:
        if not typer.confirm("Permanently remove these questions? This cannot be undone.", default=False):
            typer.echo("aborted.")
            raise typer.Exit(0)

    backup_dir = config.home / "backups" / f"{slug}-prune-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    _store.export_deck(deck, backup_dir)
    bootstrap._write_backup_meta(
        backup_dir,
        slug=slug,
        content_version=deck.content_version,
        op="prune",
        timestamp=backup_dir.name.rsplit("-", 1)[-1],
    )
    typer.echo(f"backup: {backup_dir}")

    keep_ids = bundled_ids | {_engine.question_id(q) for _, q in _engine.iter_question_records(local)
                              if not _engine.question_id(q)}  # keep id-less questions untouched
    for chapter in list(local.keys()):
        if chapter == "_chapter_titles":
            continue
        if isinstance(local[chapter], list):
            local[chapter] = [q for q in local[chapter]
                              if (not _engine.question_id(q)) or (_engine.question_id(q) in keep_ids)]
    _store.save_tiku(deck, local)
    _engine._sync_marked_from_tiku(deck)
    _status_echo(f"pruned {len(orphaned)} question(s) from {slug}")

@deck_app.command("versions")
def deck_versions(
    slug: str = typer.Argument(..., help="Bundled deck slug."),
    overwrite_notes: bool = typer.Option(False, "--overwrite-notes", help="Allow backup/bundled user_note to overwrite local notes for this switch."),
):
    """List historical versions from backups and switch to one."""
    from . import bootstrap

    config, deck = _resolve_deck(slug)
    if slug not in bootstrap._bundled_slugs():
        _status_echo(f"{slug!r} is not a bundled deck", ok=False, err=True)
        raise typer.Exit(1)

    backups = bootstrap.list_backups(config.decks_dir, slug)
    typer.echo(f"current: content_version={deck.content_version}")
    if not backups:
        _status_echo(f"no backups available for {slug}", ok=False, err=True)
        raise typer.Exit(1)
    typer.echo("available backups:")
    for i, item in enumerate(backups, start=1):
        typer.echo(
            f"  {i}. version={item['content_version']} op={item['op']} "
            f"time={item['timestamp']} ({item['name']})"
        )
    choice = typer.prompt("Pick a backup to switch to", type=int)
    if choice < 1 or choice > len(backups):
        _status_echo("invalid backup selection", ok=False, err=True)
        raise typer.Exit(1)

    result = bootstrap.switch_bundled(
        slug,
        config.decks_dir,
        backups[choice - 1]["path"],
        overwrite_notes=overwrite_notes,
    )
    typer.echo(f"backup: {result.backup_dir}")
    _status_echo(
        "switch preview: "
        f"added={result.added}, updated={result.updated}, skipped={result.skipped}, "
        f"assigned_ids={result.assigned_ids}, conflicts={len(result.conflicts)}",
        ok=not result.conflicts,
    )
    if result.conflicts:
        _status_echo("conflicts:", ok=False, err=True)
        for conflict in result.conflicts[:20]:
            typer.echo(f"  {conflict}", err=True)
    if result.unmigrated:
        _status_echo(
            f"{len(result.unmigrated)} question(s) could not be migrated to new ids "
            "(content changed across versions); their history is orphaned:",
            ok=False,
            err=True,
        )
        for chapter, qid, topic in result.unmigrated[:20]:
            typer.echo(f"  chapter {chapter}: id={qid}, topic={topic!r}", err=True)
    current_version = bootstrap._read_local_version(config.decks_dir / slug)
    _status_echo(f"switched deck {slug} to content_version={current_version}")


@deck_app.command("repair")
def deck_repair(
    slug: str = typer.Argument(..., help="Deck slug."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview repairs without writing."),
):
    """Validate tiku.json and rebuild derived indexes after direct JSON edits."""
    config, deck = _resolve_deck(slug)
    from .repair import apply_repair_plan, build_repair_plan

    plan = build_repair_plan(deck)
    typer.echo(f"repair preview: {slug}")
    if not plan.ok:
        _status_echo(
            f"tiku validation failed ({len(plan.tiku_errors)} errors):",
            ok=False,
            err=True,
        )
        for error in plan.tiku_errors[:20]:
            typer.echo(f"  {error}", err=True)
        raise typer.Exit(1)

    _status_echo(
        f"tiku: ok, questions={plan.question_count}, chapters={plan.chapter_count}"
    )
    _status_echo(
        f"marked: rebuild {len(plan.marked_records)} records from tiku marked flags"
    )
    wrong_line = (
        "wrong: "
        f"files={plan.wrong.files}, records={plan.wrong.records}, "
        f"resolvable={plan.wrong.resolvable}, stale={plan.wrong.stale}"
    )
    _status_echo(wrong_line, ok=plan.wrong.stale == 0)

    if dry_run:
        typer.echo("dry run: nothing written")
        raise typer.Exit(0)

    apply_repair_plan(deck, plan)
    _status_echo(f"repaired deck {slug}")
    _status_echo(f"marked.json rebuilt: {len(plan.marked_records)} records")
    _status_echo(
        f"wrong checked: {plan.wrong.resolvable} resolvable, {plan.wrong.stale} stale",
        ok=plan.wrong.stale == 0,
    )


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
                                       "marked.json, wrong/, history.json, and "
                                       "session.json are migrated too)."),
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
    validated and copied; `marked.json`, `wrong/`, `history.json`, and
    `session.json`, if present, are copied verbatim so learner state survives
    the move. The old
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
            _status_echo(f"directory has no tiku.json: {src_dir}", ok=False, err=True)
            raise typer.Exit(1)
        try:
            tiku_data = json.loads(tiku_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _status_echo(f"invalid tiku.json: {exc}", ok=False, err=True)
            raise typer.Exit(1)
        errs = validate_tiku(tiku_data)
        if errs:
            _status_echo(f"tiku validation failed ({len(errs)} errors):", ok=False, err=True)
            for e in errs[:20]:
                typer.echo(f"  {e}", err=True)
            raise typer.Exit(1)
        detected_alphabet = _detect_alphabet_from_tiku(tiku_data)
        assigned_ids = _engine.ensure_question_ids(tiku_data, prefix=slug)
        qcount = sum(1 for _ in _engine.iter_question_records(tiku_data))
        chcount = len({ch for ch, _ in _engine.iter_question_records(tiku_data)})
        _status_echo(f"validated {qcount} questions across {chcount} chapter(s); "
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
                _status_echo(f"CSV import failed ({len(result.errors)} errors):", ok=False, err=True)
                for line_no, msg in result.errors[:20]:
                    where = f"line {line_no}: " if line_no else ""
                    typer.echo(f"  {where}{msg}", err=True)
                raise typer.Exit(1)
            tiku_data = result.chapters
            detected_alphabet = result.answer_alphabet
            assigned_ids = _engine.ensure_question_ids(tiku_data, prefix=slug)
            qcount = sum(1 for _ in _engine.iter_question_records(tiku_data))
            chcount = len({ch for ch, _ in _engine.iter_question_records(tiku_data)})
            _status_echo(f"parsed {qcount} questions across "
                         f"{chcount} chapter(s); alphabet={detected_alphabet}; "
                         f"assigned_ids={assigned_ids}")
        else:
            try:
                tiku_data = json.loads(source.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                _status_echo(f"invalid JSON: {exc}", ok=False, err=True)
                raise typer.Exit(1)
            errs = validate_tiku(tiku_data)
            if errs:
                _status_echo(f"tiku validation failed ({len(errs)} errors):", ok=False, err=True)
                for e in errs[:20]:
                    typer.echo(f"  {e}", err=True)
                raise typer.Exit(1)
            detected_alphabet = _detect_alphabet_from_tiku(tiku_data)
            assigned_ids = _engine.ensure_question_ids(tiku_data, prefix=slug)
            qcount = sum(1 for _ in _engine.iter_question_records(tiku_data))
            chcount = len({ch for ch, _ in _engine.iter_question_records(tiku_data)})
            _status_echo(f"validated {qcount} questions across {chcount} chapter(s); "
                         f"alphabet={detected_alphabet}; assigned_ids={assigned_ids}")

    dest_dir = config.decks_dir / slug
    if dest_dir.exists() and not force and not dry_run:
        _status_echo(f"deck already exists: {dest_dir} (use --force)", ok=False, err=True)
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
        if not report["marked"] and _has_inline_marks(tiku_data):
            _engine._sync_marked_from_tiku(deck)
        (dest_dir / "manifest.toml").write_text(manifest, encoding="utf-8")
        _status_echo(f"imported deck {slug} -> {dest_tiku} (from directory {source})")
        extra = []
        if report["marked"]:
            extra.append("marked.json")
        if report["wrong_files"]:
            extra.append(f"wrong/{report['wrong_files']} file(s)")
        if report["history"]:
            extra.append("history.json")
        if report["session"]:
            extra.append("session.json")
        if extra:
            typer.echo(f"migrated: {', '.join(extra)}")
        typer.echo(f"manifest: {dest_dir / 'manifest.toml'}")
    else:
        store.write_json(dest_tiku, tiku_data)
        if _has_inline_marks(tiku_data):
            _engine._sync_marked_from_tiku(deck)
        (dest_dir / "manifest.toml").write_text(manifest, encoding="utf-8")
        _status_echo(f"imported deck {slug} -> {dest_tiku}")
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

    Copies tiku.json, manifest.toml, marked.json (if any), wrong/ (if any),
    history.json (if any), and session.json (if any). The result can be
    re-imported on another machine with `flip import <slug> <dir>`.
    """
    config, deck = _resolve_deck(slug)
    from . import store

    dest = Path(out) if out else Path.cwd() / f"{slug}-deck"
    try:
        result = store.export_deck(deck, dest)
    except FileExistsError as exc:
        _status_echo(f"export failed: {exc} (use a different --out)", ok=False, err=True)
        raise typer.Exit(1)
    _status_echo(f"exported deck {slug} -> {result}")
    parts = []
    if deck.marked_path.is_file():
        parts.append("marked.json")
    if deck.wrong_dir.is_dir():
        parts.append(f"wrong/ ({len(store.wrong_files(deck))} file(s))")
    if deck.history_path.is_file():
        parts.append("history.json")
    if deck.session_path.is_file():
        parts.append("session.json")
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


def _load_tiku_source(source: Path, config, *, fmt=None, delimiter="auto", has_header=True):
    import json
    from .importers import import_csv, validate_tiku

    source = Path(source)
    if source.is_dir():
        source = source / "tiku.json"
        if not source.is_file():
            raise FileNotFoundError(f"directory has no tiku.json: {source.parent}")
        fmt_resolved = "json"
    else:
        fmt_resolved = _resolve_import_format(source, fmt)

    if fmt_resolved == "csv":
        result = import_csv(
            source, delimiter=delimiter, has_header=has_header,
            translation_enabled=config.translation_enabled,
        )
        if result.errors:
            message = "; ".join(
                f"line {line}: {msg}" if line else msg
                for line, msg in result.errors[:5]
            )
            raise ValueError(f"CSV import failed: {message}")
        data = result.chapters
    else:
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}")

    errs = validate_tiku(data)
    if errs:
        raise ValueError("tiku validation failed: " + "; ".join(errs[:5]))
    return data


def _has_inline_marks(data):
    from . import engine as _engine
    return any(q.get("marked") for _, q in _engine.iter_question_records(data))


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


def _update_manifest_answer_alphabet(deck, alphabet):
    path = deck.manifest_path
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("answer_alphabet") and "=" in line:
            lines[i] = f'answer_alphabet = "{alphabet}"'
            break
    else:
        insert_at = len(lines)
        for i, line in enumerate(lines):
            if line.strip().startswith("[explain]"):
                insert_at = i
                break
        lines.insert(insert_at, f'answer_alphabet = "{alphabet}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_manifest_text(*, slug, display_name, source_lang, answer_alphabet, role_text):
    return (
        '[deck]\n'
        f'name = "{display_name}"\n'
        f'slug = "{slug}"\n'
        f'source_lang = "{source_lang}"\n'
        f'answer_alphabet = "{answer_alphabet}"\n'
        'max_display_options = 4\n'
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
    if config.explain.uses_argv():
        typer.echo("  argv:    (active, overrides command)")
        for tok in config.explain.argv:
            typer.echo(f"            {tok}")
        typer.echo(f"  command: {config.explain.command}  (inactive)")
    else:
        typer.echo(f"  command: {config.explain.command}")
    typer.echo(f"  model:   {config.explain.model}")
    typer.echo(f"  output:  {config.explain.output}")
    typer.echo(f"  timeout: {config.explain.timeout}s")

    errs = config.validate()
    if errs:
        typer.echo("")
        _status_echo("config errors:", ok=False, err=True)
        for e in errs:
            typer.echo(f"  {e}", err=True)
        raise typer.Exit(1)

    from .backends import which_backend
    backend = which_backend(config.explain)
    on_path = shutil.which(backend) if backend else None
    typer.echo("")
    if backend is None:
        _status_echo("backend:    (could not parse command template)", ok=False, err=True)
    elif on_path:
        _status_echo(f"backend:    {backend} ({on_path})")
    else:
        _status_echo(f"backend:    {backend}  ⚠ not on PATH", ok=False, err=True)


if __name__ == "__main__":
    app()
