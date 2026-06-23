"""On-demand installation of bundled decks.

Pure helpers consumed by the deck picker's Bootstrap tab. Unlike a "first run"
auto-install, nothing here runs at config load — the user picks what to install
from the Bootstrap tab (engine_loop.deck_picker), and these functions do the
actual work. A deck removed via `flip deck remove` simply re-appears in the
available list because the criterion is "the deck directory doesn't exist".
"""

from __future__ import annotations

import datetime
import json
import re
from copy import deepcopy
from importlib import resources
from pathlib import Path

from ._toml import load_toml
from . import engine, store
from .deck import Deck
from .importers import validate_tiku

def available_bundled_slugs(decks_dir: Path) -> list[str]:
    """Slugs that can be offered by the Bootstrap tab right now.

    A bundled slug is available iff no deck directory of that slug exists under
    `decks_dir`. So a freshly removed bundled deck re-appears here, while an
    installed one is hidden until its directory disappears.
    """
    decks_dir = Path(decks_dir)
    return [slug for slug in _bundled_slugs() if not (decks_dir / slug).exists()]


def updatable_bundled_decks(decks_dir: Path) -> list[dict]:
    """Installed bundled decks whose content_version lags the shipped one.

    Returns one dict per updatable deck: {slug, name, current, latest}. A deck
    with content_version "0" (pre-versioning, or never set) always counts as
    outdated, so the first update triggers the legacy-id migration. The deck
    directory must exist (i.e. already installed) to be considered here.
    """
    decks_dir = Path(decks_dir)
    out = []
    for slug in _bundled_slugs():
        spec = _read_bundled_metadata(slug)
        deck_dir = decks_dir / slug
        if not deck_dir.is_dir():
            continue
        current = _read_local_version(deck_dir)
        latest = spec["content_version"]
        if _version_lt(current, latest):
            out.append({
                "slug": slug,
                "name": spec["name"],
                "current": current,
                "latest": latest,
            })
    return out


def update_bundled(slug: str, decks_dir: Path, *, overwrite_notes: bool = False):
    """In-place update of an installed bundled deck to the shipped content.

    Preserves per-question history (mark/note/ai_explanation/zh) and the
    wrong-index by routing through merge_tiku(upsert): the local tiku is the
    base, the bundled tiku is the incoming, and ids drive matching.

    Before merging, if the local deck still carries legacy *positional* ids
    (pre-UUID installs shaped like `<slug>-<chapter>-<NNN>`), they are migrated
    to the bundled UUIDs via content-key bridging: each legacy-id question is
    matched to a bundled question by (chapter, topic, options, answer), and its
    id — and every index record key referencing it — is rewritten to the UUID.
    Questions whose content also changed upstream cannot be bridged and are
    reported as unmigrated (their history is orphaned, surfaced to the caller).

    Creates a full-deck backup under <home>/backups/<slug>-update-<stamp>/ via
    store.export_deck. Returns the MergeResult from the merge step.
    """
    spec = _read_bundled_metadata(slug)
    decks_dir = Path(decks_dir)
    deck_dir = decks_dir / slug
    if not deck_dir.is_dir():
        raise FileNotFoundError(f"deck not installed: {deck_dir}")

    bundled_tiku = json.loads(_read_bundled_tiku_text(slug))
    return _apply_incoming(
        slug,
        decks_dir,
        bundled_tiku,
        spec.get("content_version", "0"),
        backup_op="update",
        overwrite_notes=overwrite_notes,
    )


def switch_bundled(slug: str, decks_dir: Path, backup_path, *, overwrite_notes: bool = False) -> object:
    spec = _read_bundled_metadata(slug)
    decks_dir = Path(decks_dir)
    backup_path = Path(backup_path)
    if not backup_path.is_dir():
        raise FileNotFoundError(f"backup not found: {backup_path}")
    incoming_tiku = json.loads((backup_path / "tiku.json").read_text(encoding="utf-8"))
    incoming_version = _backup_meta(backup_path).get("content_version", "未知")
    if incoming_version == "未知":
        incoming_version = _read_local_version(backup_path)
    return _apply_incoming(
        slug,
        decks_dir,
        incoming_tiku,
        incoming_version or "0",
        backup_op="switch",
        deck_name=spec["name"],
        source_lang=spec["source_lang"],
        overwrite_notes=overwrite_notes,
    )


def _apply_incoming(slug: str, decks_dir: Path, incoming_tiku: dict, incoming_version: str,
                    *, backup_op: str, deck_name: str | None = None, source_lang: str | None = None,
                    overwrite_notes: bool = False):
    from .merge import merge_tiku

    spec = _read_bundled_metadata(slug)
    decks_dir = Path(decks_dir)
    deck_dir = decks_dir / slug
    if not deck_dir.is_dir():
        raise FileNotFoundError(f"deck not installed: {deck_dir}")
    deck = Deck(
        slug=slug,
        name=deck_name or spec["name"],
        path=deck_dir,
        source_lang=source_lang or spec["source_lang"],
    )
    current_version = _read_local_version(deck_dir)

    backup_dir, stamp = _backup_dir_for(decks_dir.parent / "backups", slug, backup_op)
    store.export_deck(deck, backup_dir)
    _write_backup_meta(
        backup_dir,
        slug=slug,
        content_version=current_version,
        op=backup_op,
        timestamp=stamp,
    )

    local_tiku = store.load_tiku(deck) or {}
    bundled_tiku = deepcopy(incoming_tiku)

    # Default policy preserves the learner's local note. A caller can opt into
    # overwriting with the bundled/deck note for this one operation.
    if not overwrite_notes:
        for _, q in engine.iter_question_records(bundled_tiku):
            if "user_note" in q:
                q["user_note"] = ""

    # 1) Migrate legacy positional ids to bundled UUIDs (rewrites tiku + indexes).
    id_map, unmigrated = _migrate_legacy_ids(local_tiku, bundled_tiku, slug)
    if id_map:
        _rewrite_index_keys(deck, id_map)
        store.save_tiku(deck, local_tiku)

    # 2) Merge bundled (incoming) into local (base) by id; upsert preserves
    #    mark/note/ai_explanation/zh per merge.py's PRESERVED_FIELDS rules.
    result = merge_tiku(local_tiku, bundled_tiku, policy="upsert", prefix=slug)
    store.save_tiku(deck, result.data)
    engine._sync_marked_from_tiku(deck)

    # 3) Bump the manifest content_version to the shipped one.
    manifest_path = deck.manifest_path
    if manifest_path.exists():
        manifest_path.write_text(
            _bump_manifest_version(manifest_path.read_text(encoding="utf-8"),
                                   incoming_version),
            encoding="utf-8",
        )

    # Attach migration report so the CLI/UI can surface orphaned history.
    result.unmigrated = unmigrated  # type: ignore[attr-defined]
    result.backup_dir = str(backup_dir)  # type: ignore[attr-defined]
    return result


def _backup_dir_for(backup_root: Path, slug: str, op: str):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return backup_root / f"{slug}-{op}-{stamp}", stamp


def _write_backup_meta(backup_dir: Path, *, slug: str, content_version: str, op: str, timestamp: str) -> None:
    (Path(backup_dir) / "meta.json").write_text(
        json.dumps({
            "slug": slug,
            "content_version": str(content_version or "0"),
            "op": op,
            "timestamp": timestamp,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _backup_meta(path: Path) -> dict:
    meta_path = Path(path) / "meta.json"
    if not meta_path.exists():
        return {
            "slug": Path(path).name.split("-", 1)[0],
            "content_version": "未知",
            "op": "unknown",
            "timestamp": Path(path).name.rsplit("-", 1)[-1],
        }
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, TypeError):
        data = {}
    return {
        "slug": str(data.get("slug") or Path(path).name.split("-", 1)[0]),
        "content_version": str(data.get("content_version") or "未知"),
        "op": str(data.get("op") or "unknown"),
        "timestamp": str(data.get("timestamp") or Path(path).name.rsplit("-", 1)[-1]),
    }


def list_backups(decks_dir: Path, slug: str) -> list[dict]:
    backup_root = Path(decks_dir).parent / "backups"
    if not backup_root.is_dir():
        return []
    entries = []
    for path in backup_root.glob(f"{slug}-*"):
        if not path.is_dir():
            continue
        meta = _backup_meta(path)
        entries.append({
            "path": str(path),
            "content_version": meta["content_version"],
            "op": meta["op"],
            "timestamp": meta["timestamp"],
            "name": path.name,
        })
    entries.sort(key=lambda item: item["timestamp"], reverse=True)
    return entries


def _read_local_version(deck_dir: Path) -> str:
    """Read content_version from an installed deck's manifest; "0" if absent."""
    manifest = deck_dir / "manifest.toml"
    if not manifest.exists():
        return "0"
    data = load_toml(manifest)
    return str(data.get("deck", {}).get("content_version", "0")).strip() or "0"


def _version_lt(a: str, b: str) -> bool:
    """True if version a is strictly older than b."""
    pa = _parse_version(a)
    pb = _parse_version(b)
    if pa is not None and pb is not None:
        return pa < pb
    return str(a) < str(b)


def _parse_version(value: str):
    text = str(value or "").strip().lower()
    if text.startswith("v"):
        text = text[1:]
    if not text:
        return None
    parts = text.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _migrate_legacy_ids(local_tiku: dict, bundled_tiku: dict, slug: str):
    """Rewrite legacy positional ids in local_tiku to bundled UUIDs in place.

    Returns (id_map, unmigrated) where:
      - id_map: {legacy_id: new_uuid} for every successfully bridged question;
        empty when the local deck already uses UUIDs.
      - unmigrated: list of (chapter, legacy_id, topic) for legacy-id questions
        whose content-key found no bundled match (history will be orphaned).

    Bridging uses the content key (chapter, topic, options, answer). Only ids
    shaped like `<slug>-<chapter>-<NNN>` (the pre-UUID install format) are
    treated as legacy; UUIDs (q-<hex>) and any other shape are left alone.
    """
    import re
    legacy_re = re.compile(r"^[a-z0-9-]+-\d+-\d{3}$")  # e.g. se-template-1-001

    # Index bundled questions by content key for O(1) lookup.
    bundled_by_content = {}
    for chapter, q in engine.iter_question_records(bundled_tiku):
        bundled_by_content[engine.content_question_key(chapter, q)] = q.get("id")

    id_map = {}
    unmigrated = []
    for chapter, q in engine.iter_question_records(local_tiku):
        qid = engine.question_id(q)
        if not qid or not legacy_re.match(qid):
            continue  # already UUID-shaped, or no id — leave as is
        ckey = engine.content_question_key(chapter, q)
        new_uuid = bundled_by_content.get(ckey)
        if new_uuid:
            q["id"] = new_uuid
            id_map[qid] = new_uuid
        else:
            unmigrated.append((str(chapter), qid, str(q.get("topic", ""))[:80]))
    return id_map, unmigrated


def _rewrite_index_keys(deck: Deck, id_map: dict) -> None:
    """Rewrite {"id": old} keys in marked.json and wrong/*.json to new ids.

    id_map: {old_id: new_id}. Index records store their key as a JSON string
    produced by question_key(): `{"id": "<id>"}`. We rebuild that JSON with the
    new id for any record whose current id is in id_map.
    """
    if not id_map:
        return

    def _remap_key(key_str):
        try:
            obj = json.loads(key_str)
        except (TypeError, ValueError):
            return key_str
        old = obj.get("id")
        if old and old in id_map:
            obj["id"] = id_map[old]
            return json.dumps(obj, ensure_ascii=False, sort_keys=True)
        return key_str

    # marked.json
    marked = store.load_marked(deck)
    changed = False
    for rec in marked:
        old_key = rec.get("key")
        new_key = _remap_key(old_key)
        if new_key != old_key:
            rec["key"] = new_key
            changed = True
    if changed:
        store.save_marked(deck, marked)

    # wrong/*.json
    for wf in store.wrong_files(deck):
        records = store.read_json(wf, default=[])
        if not isinstance(records, list):
            continue
        wf_changed = False
        for rec in records:
            old_key = rec.get("key")
            new_key = _remap_key(old_key)
            if new_key != old_key:
                rec["key"] = new_key
                wf_changed = True
        if wf_changed:
            store.write_json(wf, records)


def _bump_manifest_version(manifest_text: str, new_version: str) -> str:
    """Set content_version in a manifest.toml text, adding the line if absent."""
    import re
    lines = manifest_text.splitlines()
    out = []
    wrote = False
    for line in lines:
        if re.match(r"\s*content_version\s*=", line):
            out.append(f'content_version = "{new_version}"')
            wrote = True
        else:
            out.append(line)
    if not wrote:
        # Insert after the [deck] table's first field if present, else append.
        for i, line in enumerate(out):
            if line.strip().startswith("slug") or line.strip().startswith("name"):
                out.insert(i + 1, f'content_version = "{new_version}"')
                wrote = True
                break
        if not wrote:
            out.append(f'content_version = "{new_version}"')
    return "\n".join(out) + "\n"


def install_bundled(slug: str, decks_dir: Path) -> None:
    """Install one bundled deck by slug into `decks_dir/<slug>`.

    Reads the bundled tiku.json from package data, validates it, assigns stable
    question ids, and writes tiku + manifest. Caller (the Bootstrap tab) is
    responsible for having checked the slug is actually available; this function
    will still work if the directory already exists but is normally only called
    for slugs returned by `available_bundled_slugs`.
    """
    spec = _read_bundled_metadata(slug)
    decks_dir = Path(decks_dir)
    decks_dir.mkdir(parents=True, exist_ok=True)

    raw_text = _read_bundled_tiku_text(slug)
    tiku_data = json.loads(raw_text)
    errs = validate_tiku(tiku_data)
    if errs:
        raise ValueError(f"bundled deck {slug} failed validation: {'; '.join(errs[:5])}")

    installed_tiku = deepcopy(tiku_data)
    engine.ensure_question_ids(installed_tiku, prefix=slug)
    answer_alphabet = _detect_alphabet_from_tiku(installed_tiku)

    deck = Deck(
        slug=slug,
        name=spec["name"],
        path=decks_dir / slug,
        source_lang=spec["source_lang"],
        answer_alphabet=answer_alphabet,
        content_version=spec.get("content_version", "0"),
    )
    deck.path.mkdir(parents=True, exist_ok=True)
    store.save_tiku(deck, installed_tiku)
    deck.manifest_path.write_text(
        _build_manifest_text(
            slug=slug,
            display_name=spec["name"],
            source_lang=spec["source_lang"],
            answer_alphabet=answer_alphabet,
            role_text=spec["role"],
            content_version=spec.get("content_version", "0"),
        ),
        encoding="utf-8",
    )


def bundled_deck_summary(slug: str) -> dict:
    """Lightweight metadata for the Bootstrap tab's display rows.

    Returns a dict with the spec fields plus a precomputed question count from
    the bundled tiku.json, so the renderer can show "(120 题, en→zh)" without
    each render having to parse the JSON itself.
    """
    spec = _read_bundled_metadata(slug)
    data = json.loads(_read_bundled_tiku_text(slug))
    count = 0
    for _, _q in engine.iter_question_records(data):
        count += 1
    return {
        "slug": slug,
        "name": spec["name"],
        "source_lang": spec["source_lang"],
        "questions": count,
    }


def _bundled_file_path(slug: str, filename: str) -> Path:
    return Path(resources.files("flip").joinpath("bundled_decks", slug, filename))


def _read_bundled_text(slug: str, filename: str) -> str:
    return _bundled_file_path(slug, filename).read_text(encoding="utf-8")


def _bundled_slugs() -> list[str]:
    root = Path(resources.files("flip").joinpath("bundled_decks"))
    if not root.exists():
        return []
    return sorted(
        path.name for path in root.iterdir()
        if path.is_dir() and (path / "metadata.toml").exists()
    )


def _read_bundled_metadata(slug: str) -> dict:
    data = load_toml(_bundled_file_path(slug, "metadata.toml"))
    deck = data.get("deck", {})
    explain = data.get("explain", {})
    return {
        "slug": str(deck.get("slug", slug)),
        "name": str(deck.get("name", slug)),
        "source_lang": str(deck.get("source_lang", "en")),
        "role": str(explain.get("role", "")),
        "content_version": str(deck.get("content_version", "0")).strip() or "0",
    }


def _read_bundled_tiku_text(slug: str) -> str:
    return _read_bundled_text(slug, "tiku.json")


def read_changelog(slug: str) -> str:
    return _read_bundled_text(slug, "CHANGELOG.md")


def _detect_alphabet_from_tiku(data):
    letters = set("ABCD")
    for _, q in engine.iter_question_records(data):
        for opt in q.get("options", []):
            if isinstance(opt, str) and opt:
                letters.add(opt[0].upper())
    valid = sorted(letter for letter in letters if letter in "ABCDEFGHIJ")
    return "".join(valid) if valid else "ABCD"


def _build_manifest_text(*, slug: str, display_name: str, source_lang: str, answer_alphabet: str, role_text: str, content_version: str = "0") -> str:
    return (
        "[deck]\n"
        f'name = "{display_name}"\n'
        f'slug = "{slug}"\n'
        f'source_lang = "{source_lang}"\n'
        f'answer_alphabet = "{answer_alphabet}"\n'
        "max_display_options = 4\n"
        f'content_version = "{content_version}"\n'
        "\n"
        "[explain]\n"
        f'role = "{role_text}"\n'
        "max_chars = 200\n"
        "# default_model and model_env override the global [explain].model.\n"
    )
