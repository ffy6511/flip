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

from . import engine, store
from .deck import Deck
from .importers import validate_tiku


# Single source of truth for bundled deck metadata. The Bootstrap tab iterates
# this dict (insertion order is the display order) and filters by which deck
# directories already exist to compute the "available to install" list.
BUNDLED_DECK_SPECS = {
    "se-template": {
        "name": "软件工程模板",
        "source_lang": "en",
        "role": "软件工程助教",
        # Monotonic content version of the bundled tiku data. Bump on every
        # upstream change so already-installed decks show as updatable.
        "content_version": "1",
    },
}


def available_bundled_slugs(decks_dir: Path) -> list[str]:
    """Slugs that can be offered by the Bootstrap tab right now.

    A bundled slug is available iff no deck directory of that slug exists under
    `decks_dir`. So a freshly removed bundled deck re-appears here, while an
    installed one is hidden until its directory disappears.
    """
    decks_dir = Path(decks_dir)
    return [slug for slug in BUNDLED_DECK_SPECS if not (decks_dir / slug).exists()]


def updatable_bundled_decks(decks_dir: Path) -> list[dict]:
    """Installed bundled decks whose content_version lags the shipped one.

    Returns one dict per updatable deck: {slug, name, current, latest}. A deck
    with content_version "0" (pre-versioning, or never set) always counts as
    outdated, so the first update triggers the legacy-id migration. The deck
    directory must exist (i.e. already installed) to be considered here.
    """
    decks_dir = Path(decks_dir)
    out = []
    for slug, spec in BUNDLED_DECK_SPECS.items():
        deck_dir = decks_dir / slug
        if not deck_dir.is_dir():
            continue
        current = _read_local_version(deck_dir)
        latest = spec.get("content_version", "0")
        if _version_lt(current, latest):
            out.append({
                "slug": slug,
                "name": spec["name"],
                "current": current,
                "latest": latest,
            })
    return out


def update_bundled(slug: str, decks_dir: Path):
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
    spec = BUNDLED_DECK_SPECS[slug]
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
    )


def switch_bundled(slug: str, decks_dir: Path, backup_path) -> object:
    spec = BUNDLED_DECK_SPECS[slug]
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
    )


def _apply_incoming(slug: str, decks_dir: Path, incoming_tiku: dict, incoming_version: str,
                    *, backup_op: str, deck_name: str | None = None, source_lang: str | None = None):
    from .merge import merge_tiku

    spec = BUNDLED_DECK_SPECS[slug]
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

    # Update-policy note preservation: the bundled deck always ships a
    # maintainer user_note (the explanation), so the default upsert rule
    # ("incoming non-empty wins") would clobber the user's own notes on every
    # update. We strip user_note from the incoming payload so merge falls into
    # its "incoming empty -> preserve local" branch. zh (translation) is left
    # intact so maintainer translation fixes still propagate.
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
    from ._toml import load_toml
    manifest = deck_dir / "manifest.toml"
    if not manifest.exists():
        return "0"
    data = load_toml(manifest)
    return str(data.get("deck", {}).get("content_version", "0")).strip() or "0"


def _version_lt(a: str, b: str) -> bool:
    """True if version a is strictly older than b. Numeric compare, else string."""
    try:
        return int(a) < int(b)
    except (TypeError, ValueError):
        return str(a) < str(b)


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
    spec = BUNDLED_DECK_SPECS[slug]
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
    spec = BUNDLED_DECK_SPECS[slug]
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


def _read_bundled_tiku_text(slug: str) -> str:
    return _read_bundled_text(slug, "tiku.json")


def _diff_tiku(before_data, after_data) -> list[dict]:
    before_index = {}
    after_index = {}
    before_order = []
    after_order = []

    for chapter, q in engine.iter_question_records(before_data):
        qid = engine.question_id(q)
        if not qid:
            continue
        before_index[qid] = (str(chapter), q)
        before_order.append(qid)
    for chapter, q in engine.iter_question_records(after_data):
        qid = engine.question_id(q)
        if not qid:
            continue
        after_index[qid] = (str(chapter), q)
        after_order.append(qid)

    changes = []
    for qid in after_order:
        if qid not in before_index:
            continue
        before_chapter, before_q = before_index[qid]
        after_chapter, after_q = after_index[qid]
        if (
            before_q.get("topic") == after_q.get("topic")
            and before_q.get("options") == after_q.get("options")
            and before_q.get("answer") == after_q.get("answer")
            and before_q.get("zh") == after_q.get("zh")
            and before_q.get("user_note") == after_q.get("user_note")
        ):
            continue
        change = {
            "id": qid,
            "kind": "updated",
            "chapter": after_chapter,
            "topic": {"before": before_q.get("topic", ""), "after": after_q.get("topic", "")},
            "options": {"before": before_q.get("options", []), "after": after_q.get("options", [])},
            "answer": {"before": before_q.get("answer", ""), "after": after_q.get("answer", "")},
        }
        if before_q.get("zh") != after_q.get("zh"):
            change["zh_changed"] = True
        if before_q.get("user_note") != after_q.get("user_note"):
            change["user_note_changed"] = True
        changes.append(change)

    for qid in before_order:
        if qid in after_index:
            continue
        chapter, q = before_index[qid]
        changes.append({
            "id": qid,
            "kind": "removed",
            "chapter": chapter,
            "topic": q.get("topic", ""),
        })

    for qid in after_order:
        if qid in before_index:
            continue
        chapter, q = after_index[qid]
        changes.append({
            "id": qid,
            "kind": "added",
            "chapter": chapter,
            "topic": q.get("topic", ""),
        })
    return changes


def _change_summary_line(change: dict) -> str:
    kind = change.get("kind")
    qid = change.get("id", "-")
    chapter = change.get("chapter", "?")
    if kind == "added":
        return f"新增 {qid} (ch{chapter}): {str(change.get('topic', ''))[:80]}"
    if kind == "removed":
        return f"删除 {qid} (ch{chapter}): {str(change.get('topic', ''))[:80]}"
    parts = []
    topic = change.get("topic", {})
    answer = change.get("answer", {})
    options = change.get("options", {})
    if topic.get("before") != topic.get("after"):
        parts.append("题干修订")
    if answer.get("before") != answer.get("after"):
        parts.append(f"答案 {answer.get('before', '')}→{answer.get('after', '')}")
    if options.get("before") != options.get("after"):
        parts.append("选项修订")
    if change.get("zh_changed"):
        parts.append("译文更新")
    if change.get("user_note_changed"):
        parts.append("说明更新")
    summary = "、".join(parts) if parts else "内容修订"
    return f"更新 {qid} (ch{chapter}): {summary}"


def gen_changelog(slug: str) -> str:
    before_data = json.loads(_read_bundled_text(slug, "prev_tiku.json"))
    after_data = json.loads(_read_bundled_tiku_text(slug))
    changes = _diff_tiku(before_data, after_data)
    if not changes:
        raise ValueError("prev_tiku.json 与 tiku.json 相同,无法生成 diff;请确认 prev 是上一版发布时的快照")

    version = str(BUNDLED_DECK_SPECS[slug].get("content_version", "0"))
    today = datetime.date.today().isoformat()
    updated = sum(1 for item in changes if item["kind"] == "updated")
    added = sum(1 for item in changes if item["kind"] == "added")
    removed = sum(1 for item in changes if item["kind"] == "removed")
    lines = [
        f"## [{version}] - {today}",
        "",
        f"更新 {updated} 题、新增 {added} 题、删除 {removed} 题。",
        "",
    ]
    for change in changes:
        lines.append(f"- {_change_summary_line(change)}")
    payload = {
        "version": version,
        "date": today,
        "changes": changes,
    }
    lines.extend([
        "",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "```",
    ])
    entry_text = "\n".join(lines)

    changelog_path = _bundled_file_path(slug, "CHANGELOG.md")
    original = changelog_path.read_text(encoding="utf-8")
    if original.startswith("# "):
        first_line, _, remainder = original.partition("\n")
        remainder = remainder.lstrip("\n")
        new_text = first_line + "\n\n" + entry_text + ("\n\n" + remainder if remainder else "\n")
    else:
        new_text = entry_text + "\n\n" + original.lstrip("\n")
    changelog_path.write_text(new_text.rstrip() + "\n", encoding="utf-8")
    return entry_text


def read_changelog(slug: str, version=None) -> list[dict]:
    text = _read_bundled_text(slug, "CHANGELOG.md")
    pattern = re.compile(
        r"^## \[(?P<version>[^\]]+)\] - (?P<date>\d{4}-\d{2}-\d{2})\n(?P<body>.*?)(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    entries = []
    for match in pattern.finditer(text):
        entry_version = match.group("version").strip()
        if version is not None and str(version) != entry_version:
            continue
        body = match.group("body").strip()
        diff = None
        text_body = body
        json_match = re.search(r"```json\n(.*?)\n```", body, re.DOTALL)
        if json_match:
            diff_payload = json.loads(json_match.group(1))
            diff = diff_payload.get("changes")
            text_body = body[:json_match.start()].rstrip()
        section_text = f"## [{entry_version}] - {match.group('date')}\n\n{text_body}".rstrip()
        entries.append({
            "version": entry_version,
            "date": match.group("date"),
            "text": section_text,
            "diff": diff,
        })
    return entries


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
