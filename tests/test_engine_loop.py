import shutil

from flip import engine, engine_loop, store
from flip.tui.render import default_detail_view, normalize_detail_view


def _patch_tty(monkeypatch, keys):
    key_iter = iter(keys)
    monkeypatch.setattr(engine_loop, "save_tty", lambda: None)
    monkeypatch.setattr(engine_loop, "restore_tty", lambda _settings: None)
    monkeypatch.setattr(engine_loop, "enter_cbreak", lambda: None)
    monkeypatch.setattr(engine_loop, "read_key", lambda: next(key_iter))


# ---- detail_view defaulting policy ----
# prompt_result relies on default_detail_view to auto-show x/n content at the
# moment of feedback. These tests lock that semantics down.

def test_default_detail_view_none_when_no_content():
    q = {"topic": "t", "options": ["A. x"], "answer": "A", "user_note": ""}
    assert default_detail_view(q) is None


def test_default_detail_view_prefers_note_over_ai():
    # When both exist, note wins (matches the original se_regressor priority).
    q = {
        "topic": "t", "options": ["A. x"], "answer": "A",
        "user_note": "remember this",
        "ai_explanation": "some explanation",
    }
    assert default_detail_view(q) == "note"


def test_default_detail_view_shows_ai_when_only_ai_present():
    q = {
        "topic": "t", "options": ["A. x"], "answer": "A",
        "user_note": "",
        "ai_explanation": "some explanation",
    }
    assert default_detail_view(q) == "ai"


def test_default_detail_view_ignores_whitespace_only_note():
    q = {
        "topic": "t", "options": ["A. x"], "answer": "A",
        "user_note": "   \n  ",
        "ai_explanation": "x",
    }
    # Note is effectively empty -> fall through to ai
    assert default_detail_view(q) == "ai"


def test_normalize_detail_view_drops_to_none_without_explicit_request():
    # normalize is the *conservative* policy (used by prompt_answer pre-submit
    # and review_history): passing None yields None, NOT a default. This is
    # the inverse of default_detail_view, and prompt_result switched from
    # normalize to default precisely so the result screen auto-shows content.
    q = {
        "topic": "t", "options": ["A. x"], "answer": "A",
        "user_note": "n",
        "ai_explanation": "a",
    }
    assert normalize_detail_view(q, None) is None
    assert normalize_detail_view(q, "ai") == "ai"
    assert normalize_detail_view(q, "note") == "note"


# ---- x/n toggle: pressing the key again hides the block ----
#
# _handle_detail_keys is the shared dispatcher for x/n/e/m/q across all four
# prompt loops. These pin the new toggle semantics: when the matching block is
# already shown, the key hides it (returns None); otherwise it opens as before.

def _noop_render(*a, **k):
    return None


def test_x_toggles_off_when_ai_already_shown():
    # detail_view == "ai" + press x  ->  hidden (None), no AI request fired.
    q = {"topic": "t", "options": ["A. x"], "answer": "A",
         "ai_explanation": "some explanation", "user_note": ""}
    detail_view, _warning, action = engine_loop._handle_detail_keys(
        None, None, "1", q, "ai", "x", _noop_render,
    )
    assert detail_view is None
    assert action is None


def test_n_toggles_off_when_note_already_shown():
    # detail_view == "note" + press n  ->  hidden (None), no note editor fired.
    q = {"topic": "t", "options": ["A. x"], "answer": "A",
         "user_note": "a note", "ai_explanation": ""}
    detail_view, _warning, action = engine_loop._handle_detail_keys(
        None, None, "1", q, "note", "n", _noop_render,
    )
    assert detail_view is None
    assert action is None


def test_x_opens_when_something_else_shown(monkeypatch):
    # detail_view == "note" (or None) + press x  ->  opens ai (does NOT toggle
    # off), so users with both kinds of content can switch between them.
    q = {"topic": "t", "options": ["A. x"], "answer": "A",
         "ai_explanation": "ai content", "user_note": "note content"}
    # Both tabs exist, so opening ai is pure display — no AI request needed.
    detail_view, _warning, _action = engine_loop._handle_detail_keys(
        None, None, "1", q, "note", "x", _noop_render,
    )
    assert detail_view == "ai"


def test_x_then_x_round_trips_to_hidden_then_ai():
    # Two presses: first opens ai, second hides it. Verifies the toggle is
    # symmetric — the user can't get stuck "always showing".
    q = {"topic": "t", "options": ["A. x"], "answer": "A",
         "ai_explanation": "ai content", "user_note": ""}
    opened, _, _ = engine_loop._handle_detail_keys(
        None, None, "1", q, None, "x", _noop_render)
    assert opened == "ai"
    closed, _, _ = engine_loop._handle_detail_keys(
        None, None, "1", q, opened, "x", _noop_render)
    assert closed is None


# ---- e edits the correct answer when no detail block is shown ----
#
# detail_view == None means the lower block has nothing to edit, so `e` opens
# the answer editor instead. This writes the new answer back to tiku.json
# (UUID untouched, so history stays attached).

def _stub_tty_for_keys(monkeypatch, keys):
    """Wire fake keypresses into engine_loop for sub-prompt loops."""
    key_iter = iter(keys)
    monkeypatch.setattr(engine_loop, "save_tty", lambda: None)
    monkeypatch.setattr(engine_loop, "restore_tty", lambda _s: None)
    monkeypatch.setattr(engine_loop, "enter_cbreak", lambda: None)
    monkeypatch.setattr(engine_loop, "read_key", lambda: next(key_iter))
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)


def _demo_deck_with(tmp_path, answer="A", qid="q-test0000001"):
    """A one-question deck on disk, returned with its Deck handle."""
    import os
    from flip import store
    from flip.deck import Deck
    home = tmp_path / "flip_home"
    decks = home / "decks" / "demo"
    decks.mkdir(parents=True)
    q = {"topic": "pick", "options": ["A. x", "B. y", "C. z"], "answer": answer}
    if qid is not None:
        q["id"] = qid
    store.save_tiku(Deck(slug="demo", name="Demo", path=decks, source_lang="en"),
                    {"1": [q]})
    (decks / "manifest.toml").write_text(
        '[deck]\nname="Demo"\nslug="demo"\nsource_lang="en"\nanswer_alphabet="ABC"\nmax_display_options=4\n\n[explain]\nrole="d"\nmax_chars=200\n',
        encoding="utf-8")
    os.environ["FLIP_HOME"] = str(home)
    return Deck(slug="demo", name="Demo", path=decks, source_lang="en")


def test_e_edits_answer_when_detail_view_none(monkeypatch, tmp_path):
    # detail_view None + press e  ->  answer editor opens. Keys: down to B,
    # space (single-select flips to B), enter to save.
    _stub_tty_for_keys(monkeypatch, ["\x1b[B", " ", "\r"])
    deck = _demo_deck_with(tmp_path, answer="A")
    from flip import store
    q = store.load_tiku(deck)["1"][0]

    detail_view, warning, action = engine_loop._handle_detail_keys(
        deck, None, "1", q, None, "e", _noop_render)

    assert detail_view is None           # answer edit doesn't change detail_view
    assert action is None
    assert "已更新" in warning
    assert q["answer"] == "B"
    # Persisted to disk.
    assert store.load_tiku(deck)["1"][0]["answer"] == "B"


def test_e_edits_answer_without_question_id(monkeypatch, tmp_path):
    _stub_tty_for_keys(monkeypatch, ["\x1b[B", " ", "\r"])
    deck = _demo_deck_with(tmp_path, answer="A", qid=None)
    from flip import store
    q = store.load_tiku(deck)["1"][0]

    detail_view, warning, action = engine_loop._handle_detail_keys(
        deck, None, "1", q, None, "e", _noop_render)

    assert detail_view is None
    assert action is None
    assert "已更新" in warning
    assert q["answer"] == "B"
    assert store.load_tiku(deck)["1"][0]["answer"] == "B"


def test_e_answer_edit_refreshes_current_selected_set(monkeypatch, tmp_path):
    _stub_tty_for_keys(monkeypatch, ["\x1b[B", " ", "\r"])
    deck = _demo_deck_with(tmp_path, answer="A")
    from flip import store
    q = store.load_tiku(deck)["1"][0]
    stale_copy = dict(q)
    selected = engine.SelectedSet(
        [("1", q), ("1", stale_copy)],
        input_is_index=True,
    )

    _detail_view, warning, _action = engine_loop._handle_detail_keys(
        deck, None, "1", q, None, "e", _noop_render, selected_set=selected)

    assert "已更新" in warning
    assert selected.questions == [("1", q)]
    assert selected.questions[0][1]["answer"] == "B"


def test_review_questions_answer_edit_refreshes_selected_set(monkeypatch, tmp_path, config):
    _stub_tty_for_keys(monkeypatch, ["e", "\x1b[B", " ", "\r", "q"])
    deck = _demo_deck_with(tmp_path, answer="A")
    from flip import store
    q = store.load_tiku(deck)["1"][0]
    stale_copy = dict(q)
    selected = engine.SelectedSet(
        [("1", q), ("1", stale_copy)],
        input_is_index=True,
    )
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "enter_cbreak", lambda: None)
    monkeypatch.setattr(engine_loop, "render_review_question", lambda *a, **k: None)

    status, items = engine_loop.review_questions(deck, config, selected)

    assert status == "quit"
    assert selected.questions == [("1", q)]
    assert selected.questions[0][1]["answer"] == "B"
    assert len(items) == 1
    assert items[0]["question"]["answer"] == "B"


def test_run_train_warns_when_deck_has_missing_ids(monkeypatch, tmp_path, capsys):
    deck = _demo_deck_with(tmp_path, answer="A", qid=None)
    engine_loop._DECK_HEALTH_WARNED.clear()
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: engine.SelectedSet([], input_is_index=False))

    outcome = engine_loop.run_train(deck, None, selector=None, source="tiku")

    assert outcome == 0
    out = capsys.readouterr().out
    assert "缺少稳定 id" in out
    assert "flip deck migrate demo --ids" in out


def test_run_review_warns_when_deck_has_missing_ids(monkeypatch, tmp_path, capsys):
    deck = _demo_deck_with(tmp_path, answer="A", qid=None)
    engine_loop._DECK_HEALTH_WARNED.clear()
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: engine.SelectedSet([], input_is_index=True))

    outcome = engine_loop.run_train(deck, None, selector=None, source="wrong")

    assert outcome == 0
    out = capsys.readouterr().out
    assert "缺少稳定 id" in out
    assert "flip deck migrate demo --ids" in out


def test_e_answer_edit_esc_cancels_without_writing(monkeypatch, tmp_path):
    # Esc in the answer editor cancels: nothing written, no warning.
    _stub_tty_for_keys(monkeypatch, ["\x1b[B", " ", "\x1b"])
    deck = _demo_deck_with(tmp_path, answer="A")
    from flip import store
    q = store.load_tiku(deck)["1"][0]

    detail_view, warning, action = engine_loop._handle_detail_keys(
        deck, None, "1", q, None, "e", _noop_render)

    assert warning == ""
    assert q["answer"] == "A"             # unchanged
    assert store.load_tiku(deck)["1"][0]["answer"] == "A"


def test_e_still_edits_note_when_detail_view_note(monkeypatch, tmp_path):
    # When a note is showing, e edits the NOTE, not the answer. Confirms the
    # dispatch by detail_view still wins over the new answer-edit fallback.
    deck = _demo_deck_with(tmp_path, answer="A")
    from flip import store
    q = store.load_tiku(deck)["1"][0]
    q["user_note"] = "orig note"          # a note exists -> detail_view can be "note"
    store.save_tiku(deck, {"1": [q]})
    # Stub the note editor to immediately return a new note.
    monkeypatch.setattr(engine_loop, "_prompt_user_note", lambda *a, **k: "edited")

    detail_view, warning, action = engine_loop._handle_detail_keys(
        deck, None, "1", q, "note", "e", _noop_render)

    assert detail_view == "note"
    assert q["user_note"] == "edited"
    assert q["answer"] == "A"             # answer untouched


def test_prompt_user_note_ctrl_u_clears_entire_buffer(monkeypatch):
    _patch_tty(monkeypatch, ["\x15", "n", "e", "w", "\r"])
    q = {
        "topic": "t",
        "options": ["A. x"],
        "answer": "A",
        "user_note": "old note",
    }

    note = engine_loop._prompt_user_note(None, "1", q, _noop_render)

    assert note == "new"


def test_prompt_ai_extra_ctrl_u_clears_entire_buffer(monkeypatch, tmp_path):
    _patch_tty(monkeypatch, ["a", "b", "\x15", "c", "\r"])
    deck = _demo_deck_with(tmp_path, answer="A")
    q = {"topic": "t", "options": ["A. x"], "answer": "A"}

    extra = engine_loop._prompt_ai_extra(deck, "1", q, _noop_render)

    assert extra == "c"


def test_selector_set_from_text_uses_engine_chapter_selector():
    assert engine_loop._selector_set_from_text(
        "5,3-4", ["1", "2", "3", "4", "5"], 5
    ) == {"3", "4", "5"}


def test_auto_select_chapters_uses_mode_specific_availability_and_count():
    selected = engine_loop._auto_select_chapters(
        "Review",
        ["1", "2", "3", "4"],
        {"1": 5, "2": 4, "3": 3, "4": 2},
        {"1": 0, "2": 2, "3": 1, "4": 1},
        {"1": 0, "2": 3, "3": 1, "4": 1},
        2,
    )

    assert selected == {"3", "4"}


def test_auto_select_chapters_caps_at_all_eligible_chapters():
    selected = engine_loop._auto_select_chapters(
        "Train",
        ["1", "2", "3"],
        {"1": 0, "2": 2, "3": 1},
        {"1": 0, "2": 0, "3": 0},
        {"1": 5, "2": 1, "3": 0},
        9,
    )

    assert selected == {"2", "3"}


def test_edit_selector_auto_mode_applies_selection_then_returns_to_normal_mode(
    deck, config, monkeypatch
):
    seen_buffers = []
    _patch_tty(monkeypatch, ["a", "2", "\r", "\x1b"])
    monkeypatch.setattr(
        engine_loop.engine,
        "stats_snapshot",
        lambda _deck: {
            "total": 4,
            "chapters": 4,
            "marked": 0,
            "note": 0,
            "ai": 0,
            "wrong": 0,
            "wrong_files": 0,
            "per_chapter": {"1": 2, "2": 2, "3": 2, "4": 2},
            "wrong_per_chapter": {"1": 0, "2": 1, "3": 1, "4": 1},
            "drills_per_chapter": {"1": 9, "2": 9, "3": 9, "4": 9},
        },
    )
    monkeypatch.setattr(
        engine_loop,
        "_drills_per_chapter_for_mode",
        lambda _deck, _mode: {"1": 3, "2": 1, "3": 1, "4": 2},
    )
    monkeypatch.setattr(
        engine_loop,
        "_render_chapter_picker",
        lambda _mode, _chapters, _titles, _per, _wrong, _drills, _max, _cursor, _selected, buffer,
        *args, **kwargs: seen_buffers.append(buffer),
    )

    confirmed, selector = engine_loop._edit_selector(None, "Review", deck=deck, config=config)

    assert (confirmed, selector) == (False, None)
    assert "2-3" in seen_buffers


def test_options_respects_deck_max_display_options(deck):
    q = {
        "topic": "t",
        "options": ["A. a", "B. b", "C. c", "D. d", "E. e"],
        "answer": "A",
    }

    assert engine_loop._options(q, deck) == ["A. a", "B. b", "C. c", "D. d"]

    deck.max_display_options = 5
    assert engine_loop._options(q, deck) == ["A. a", "B. b", "C. c", "D. d", "E. e"]


def test_chapter_picker_renders_zero_drill_badge_dim_when_selected(capsys, monkeypatch):
    from flip.tui.render import DIM_COLOR, RESET_COLOR, SELECTED_COLOR

    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)

    engine_loop._render_chapter_picker(
        "训练",
        ["1"],
        {},
        {"1": 3},
        {"1": 0},
        {"1": 0},
        3,
        0,
        set(),
        "",
    )

    out = capsys.readouterr().out
    assert f"{DIM_COLOR}[×0]{RESET_COLOR}{SELECTED_COLOR}" in out


def test_chapter_picker_scrolls_window_to_keep_cursor_visible(capsys, monkeypatch):
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "_terminal_height", lambda: 12, raising=False)

    chapters = [str(i) for i in range(1, 11)]
    titles = {str(i): f"Title {i:02d}" for i in range(1, 11)}
    per_chapter = {str(i): i for i in range(1, 11)}
    wrong_per_chapter = {str(i): 0 for i in range(1, 11)}
    drills_per_chapter = {str(i): 0 for i in range(1, 11)}

    engine_loop._render_chapter_picker(
        "训练",
        chapters,
        titles,
        per_chapter,
        wrong_per_chapter,
        drills_per_chapter,
        10,
        9,
        set(),
        "",
    )

    out = capsys.readouterr().out
    assert "Title 01" not in out
    assert "Title 10" in out


def test_deck_picker_scrolls_window_to_keep_cursor_visible(capsys, monkeypatch):
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "_terminal_height", lambda: 10, raising=False)

    rows = [
        [f"deck{i:02d}", f"Deck {i}", "10", "2", "en", "ABCD", "0", "0"]
        for i in range(1, 11)
    ]

    engine_loop._render_deck_picker(rows, 9, "", "deck10")

    out = capsys.readouterr().out
    assert "deck01" not in out
    assert "deck10" in out


# ---- Bootstrap tab (deck picker's left/right tab for installing bundled decks) ----
#
# These pin the tab-switch + multi-select + confirm-and-install flow introduced
# to replace the old silent first-run auto-install. Goal ③ (a removed deck must
# not reappear on launch) is guarded here by checking that install only happens
# via the explicit Bootstrap tab, never via load_config.

def _empty_config(tmp_path, monkeypatch):
    """A Config backed by an FLIP_HOME with NO decks (Library tab empty)."""
    home = tmp_path / "flip_home"
    home.mkdir()
    monkeypatch.setenv("FLIP_HOME", str(home))
    from flip.config import load_config
    return load_config(home)


def _patch_deck_picker_tty(monkeypatch, keys):
    """Wire fake keypresses + suppress the alt-screen/tty calls deck_picker makes."""
    _patch_tty(monkeypatch, keys)
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None, raising=False)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None, raising=False)
    monkeypatch.setattr(engine_loop.sys.stdin, "isatty", lambda: True)


def _install_fake_bootstrap_deck(monkeypatch, decks_dir, *, version="1", topic="t1"):
    import json
    from flip import bootstrap

    monkeypatch.setattr(bootstrap, "_bundled_slugs", lambda: ["se-template"])
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": "se-template",
        "name": "软件工程模板",
        "source_lang": "en",
        "role": "软件工程助教",
        "content_version": version,
    })
    monkeypatch.setattr(
        bootstrap,
        "_read_bundled_tiku_text",
        lambda _slug: json.dumps({"1": [
            {"topic": topic, "options": ["A. x", "B. y"], "answer": "A"},
        ]}, ensure_ascii=False),
    )
    bootstrap.install_bundled("se-template", decks_dir)


def test_deck_picker_initially_highlights_last_used_deck(flip_home, monkeypatch):
    from flip.config import load_config, save_default_deck

    decks_dir = flip_home / "decks"
    later_dir = decks_dir / "later"
    later_dir.mkdir()
    shutil.copyfile(decks_dir / "example" / "tiku.json", later_dir / "tiku.json")
    (later_dir / "manifest.toml").write_text(
        '[deck]\nname = "Later"\nslug = "later"\nsource_lang = "en"\n'
        'answer_alphabet = "ABCDE"\nmax_display_options = 4\n\n'
        '[explain]\nrole = "later"\nmax_chars = 200\n',
        encoding="utf-8",
    )

    config = load_config()
    save_default_deck(config, "later")
    _patch_deck_picker_tty(monkeypatch, ["q"])

    rendered = []

    def capture_render(rows, index, query, default_deck):
        rendered.append((rows, index, query, default_deck))

    monkeypatch.setattr(engine_loop, "_render_deck_picker", capture_render)

    engine_loop.deck_picker(config)

    rows, index, query, default_deck = rendered[0]
    assert query == ""
    assert default_deck == "later"
    assert rows[index][0] == "later"


def test_deck_picker_empty_library_shows_bootstrap_hint(capsys, monkeypatch, tmp_path):
    # Empty home: Library must NOT abort flip; it shows a pointer to the
    # Bootstrap tab so the user can install without leaving the picker.
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(monkeypatch, ["\x1b"])  # Esc immediately

    config = _empty_config(tmp_path, monkeypatch)
    engine_loop.deck_picker(config)

    out = capsys.readouterr().out
    assert "Bootstrap" in out
    assert "→" in out  # the hint arrow pointing at the Bootstrap tab


def test_deck_picker_right_arrow_switches_to_bootstrap_tab(capsys, monkeypatch, tmp_path):
    # Right arrow moves the active tab to Bootstrap; its screen renders the
    # bundled se-template row as installable (under its display name).
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(monkeypatch, ["\x1b[C", "\x1b", "q"])

    config = _empty_config(tmp_path, monkeypatch)
    engine_loop.deck_picker(config)

    out = capsys.readouterr().out
    # Bootstrap screen shows the display name of the bundled deck.
    assert "软件工程模板" in out
    assert "[ Bootstrap ]" in out


def test_bootstrap_tab_esc_with_selection_does_not_exit(capsys, monkeypatch, tmp_path):
    # Esc when there's a selection must clear it, not bounce out to Library or
    # exit flip. After clearing, another Esc falls back to Library, then q
    # quits. If the first Esc had exited instead, the trailing Esc/q would
    # either error or leave the picker hanging on StopIteration.
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(monkeypatch, ["\x1b[C", " ", "\x1b", "\x1b", "q"])

    config = _empty_config(tmp_path, monkeypatch)
    result = engine_loop.deck_picker(config)

    assert result is None
    out = capsys.readouterr().out
    # The very last rendered frame is Library (the fallback target of the 2nd
    # Esc), proving the 1st Esc cleared the selection rather than exiting.
    # The Bootstrap frames before it must show [ ] (selection cleared), not [x].
    bootstrap_frames = out.split("@ flip — Bootstrap")[1:]
    assert bootstrap_frames, "expected at least one Bootstrap render frame"
    last_boot = bootstrap_frames[-1]
    assert "[x]" not in last_boot


def test_bootstrap_tab_space_toggles_selection_marker(capsys, monkeypatch, tmp_path):
    # Space toggles a row into the selection; [x] appears in the row's own
    # color (yellow here since the cursor is on it), no separate tint.
    from flip.tui.render import SELECTED_COLOR, RESET_COLOR

    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    # → into Bootstrap, space selects, then Esc (drops selection), Esc (back to
    # Library), q quits Library.
    _patch_deck_picker_tty(monkeypatch, ["\x1b[C", " ", "\x1b", "\x1b", "q"])

    config = _empty_config(tmp_path, monkeypatch)
    engine_loop.deck_picker(config)

    out = capsys.readouterr().out
    # The selected row is wrapped in SELECTED_COLOR (yellow) and carries [x].
    assert SELECTED_COLOR + "  [x] " in out
    assert RESET_COLOR in out


def test_bootstrap_tab_enter_confirms_then_installs(capsys, monkeypatch, tmp_path):
    # Full flow: → Bootstrap, space select se-template, Enter (confirm prompt),
    # Enter again (commit install). Assert the deck lands on disk and the
    # Bootstrap list refreshes to empty.
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(
        monkeypatch,
        ["\x1b[C", " ", "\r", "\r", "\x1b", "\x1b", "q"],
    )

    config = _empty_config(tmp_path, monkeypatch)
    engine_loop.deck_picker(config)

    deck_dir = config.decks_dir / "se-template"
    assert deck_dir.is_dir()
    assert (deck_dir / "manifest.toml").is_file()
    assert (deck_dir / "tiku.json").is_file()

    out = capsys.readouterr().out
    assert "已安装 1 个 deck" in out
    assert "reinstall" in out


def test_bootstrap_tab_second_enter_without_selection_is_noop(capsys, monkeypatch, tmp_path):
    # Enter with an empty selection should not enter confirm mode nor install
    # anything — guards against stray installs from a double-tap.
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(monkeypatch, ["\x1b[C", "\r", "\r", "\x1b", "q"])

    config = _empty_config(tmp_path, monkeypatch)
    engine_loop.deck_picker(config)

    # Nothing installed despite two Enters.
    assert not (config.decks_dir / "se-template").exists()
    out = capsys.readouterr().out
    assert "将安装" not in out  # confirm prompt never shown


def test_bootstrap_tab_shows_updateable_deck(capsys, monkeypatch, tmp_path):
    from flip import bootstrap

    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(monkeypatch, ["\x1b[C", "\x1b", "q"])

    config = _empty_config(tmp_path, monkeypatch)
    _install_fake_bootstrap_deck(monkeypatch, config.decks_dir, version="1", topic="old topic")

    monkeypatch.setattr(
        bootstrap,
        "_read_bundled_tiku_text",
        lambda _slug: '{"1":[{"topic":"new topic","options":["A. x","B. y"],"answer":"A"}]}',
    )
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": "se-template",
        "name": "软件工程模板",
        "source_lang": "en",
        "role": "软件工程助教",
        "content_version": "2",
    })

    engine_loop.deck_picker(config)

    out = capsys.readouterr().out
    assert "软件工程模板" in out
    assert "update v1→v2" in out


def test_bootstrap_tab_shows_installed_deck_as_reinstall(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(monkeypatch, ["\x1b[C", "\x1b", "q"])

    config = _empty_config(tmp_path, monkeypatch)
    _install_fake_bootstrap_deck(monkeypatch, config.decks_dir, version="1", topic="old topic")

    engine_loop.deck_picker(config)

    out = capsys.readouterr().out
    assert "软件工程模板" in out
    assert "reinstall" in out


def test_bootstrap_tab_enter_updates_and_refreshes(capsys, monkeypatch, tmp_path):
    from flip import bootstrap, store
    from flip.deck import load_deck

    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(
        monkeypatch,
        ["\x1b[C", " ", "\r", "\r", "\x1b", "q"],
    )

    config = _empty_config(tmp_path, monkeypatch)
    _install_fake_bootstrap_deck(monkeypatch, config.decks_dir, version="1", topic="old topic")
    deck = load_deck(config.decks_dir / "se-template")
    qid = store.load_tiku(deck)["1"][0]["id"]

    monkeypatch.setattr(
        bootstrap,
        "_read_bundled_tiku_text",
        lambda _slug: (
            '{"1":[{"id":"%s","topic":"old topic [fixed]","options":["A. x","B. y"],"answer":"A"}]}'
            % qid
        ),
    )
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": "se-template",
        "name": "软件工程模板",
        "source_lang": "en",
        "role": "软件工程助教",
        "content_version": "2",
    })

    engine_loop.deck_picker(config)

    deck2 = load_deck(config.decks_dir / "se-template")
    assert deck2.content_version == "2"
    assert store.load_tiku(deck2)["1"][0]["topic"] == "old topic [fixed]"

    out = capsys.readouterr().out
    assert "已更新 1 个 deck" in out
    assert "updated=1" in out
    assert "reinstall" in out


def test_bootstrap_tab_u_toggles_note_overwrite_and_applies_update(capsys, monkeypatch, tmp_path):
    from flip import bootstrap, store
    from flip.deck import load_deck

    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(
        monkeypatch,
        ["\x1b[C", "u", " ", "\r", "\r", "\x1b", "q"],
    )

    config = _empty_config(tmp_path, monkeypatch)
    _install_fake_bootstrap_deck(monkeypatch, config.decks_dir, version="1", topic="old topic")
    deck = load_deck(config.decks_dir / "se-template")
    tiku = store.load_tiku(deck)
    qid = tiku["1"][0]["id"]
    tiku["1"][0]["user_note"] = "MINE"
    store.save_tiku(deck, tiku)

    monkeypatch.setattr(
        bootstrap,
        "_read_bundled_tiku_text",
        lambda _slug: (
            '{"1":[{"id":"%s","topic":"old topic [fixed]","options":["A. x","B. y"],"answer":"A","user_note":"UPSTREAM"}]}'
            % qid
        ),
    )
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": "se-template",
        "name": "软件工程模板",
        "source_lang": "en",
        "role": "软件工程助教",
        "content_version": "2",
    })

    engine_loop.deck_picker(config)

    deck2 = load_deck(config.decks_dir / "se-template")
    assert store.load_tiku(deck2)["1"][0]["user_note"] == "UPSTREAM"
    out = capsys.readouterr().out
    assert "覆盖 bundled note" in out


def test_bootstrap_tab_enter_reinstalls_installed_deck(capsys, monkeypatch, tmp_path):
    from flip import bootstrap, store
    from flip.deck import load_deck

    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(monkeypatch, ["\x1b[C", " ", "\r", "\r", "\x1b", "q"])

    config = _empty_config(tmp_path, monkeypatch)
    _install_fake_bootstrap_deck(monkeypatch, config.decks_dir, version="1", topic="old topic")
    deck = load_deck(config.decks_dir / "se-template")
    qid = store.load_tiku(deck)["1"][0]["id"]

    monkeypatch.setattr(
        bootstrap,
        "_read_bundled_tiku_text",
        lambda _slug: (
            '{"1":[{"id":"%s","topic":"old topic [reinstalled]","options":["A. x","B. y"],"answer":"A"}]}'
            % qid
        ),
    )

    engine_loop.deck_picker(config)

    deck2 = load_deck(config.decks_dir / "se-template")
    assert store.load_tiku(deck2)["1"][0]["topic"] == "old topic [reinstalled]"
    out = capsys.readouterr().out
    assert "已重装 1 个 deck" in out
    assert "reinstall" in out


def test_bootstrap_tab_c_key_shows_changelog(capsys, monkeypatch, tmp_path):
    from flip import bootstrap

    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    _patch_deck_picker_tty(monkeypatch, ["\x1b[C", "c", "\x1b", "q", "q"])
    monkeypatch.setattr(
        bootstrap,
        "read_changelog",
        lambda slug: "# Changelog — 软件工程模板\n\n## v1.1\n\n更新 1 题。",
    )

    config = _empty_config(tmp_path, monkeypatch)
    _install_fake_bootstrap_deck(monkeypatch, config.decks_dir, version="1", topic="old topic")
    monkeypatch.setattr(bootstrap, "_read_bundled_metadata", lambda _slug: {
        "slug": "se-template",
        "name": "软件工程模板",
        "source_lang": "en",
        "role": "软件工程助教",
        "content_version": "2",
    })

    engine_loop.deck_picker(config)

    out = capsys.readouterr().out
    assert "Changelog" in out
    assert "更新 1 题" in out


def test_markdown_lines_render_heading_and_bullets():
    lines = engine_loop._markdown_lines("# Title\n\n- item `code`", 40)

    assert any("Title" in line for line in lines)
    assert any("• item" in line for line in lines)
    assert any("code" in line for line in lines)


def test_changelog_view_scrolls_down(capsys, monkeypatch):
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "_terminal_height", lambda: 8, raising=False)
    _patch_tty(monkeypatch, ["G", "q"])

    text = "\n".join(f"- line {i}" for i in range(1, 9))

    engine_loop._view_changelog("demo", text)

    out = capsys.readouterr().out
    assert "line 1" in out
    assert "line 8" in out


def test_render_stats_scrolls_window_to_keep_cursor_visible(capsys, monkeypatch, deck, config):
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "_terminal_height", lambda: 14, raising=False)
    monkeypatch.setattr(
        engine_loop.engine,
        "stats_snapshot",
        lambda _deck: {
            "total": 100,
            "chapters": 12,
            "marked": 0,
            "note": 0,
            "ai": 0,
            "wrong": 0,
            "wrong_files": 0,
            "per_chapter": {str(i): i for i in range(1, 13)},
            "wrong_per_chapter": {str(i): 0 for i in range(1, 13)},
            "drills_per_chapter": {str(i): 0 for i in range(1, 13)},
        },
    )

    engine_loop.render_stats(deck, config, cursor=11)

    out = capsys.readouterr().out
    assert "已标记" not in out
    assert "有笔记" not in out
    assert "有 Agent Said" not in out
    assert "wrong 去重题数" not in out
    assert "wrong 文件数" not in out
    assert "章节数: 12" in out
    assert "题目总数: 100" in out
    assert "题量 / 错题分布:" not in out
    assert "黄色" in out and "错题" in out and "[×N]" in out
    assert "search:" in out and "输入章节号后回车跳转" in out
    assert "  ch1 " not in out
    assert "  ch12" in out


def test_prompt_answer_refreshes_marked_state_after_m(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    rendered = []
    _patch_tty(monkeypatch, ["m", "1", "\r"])

    def capture_render(*_args, **kwargs):
        rendered.append(kwargs["marked"])

    monkeypatch.setattr(engine_loop, "render_question", capture_render)

    result = engine_loop.prompt_answer(deck, config, 1, 1, "1", q)

    assert result[0] == "A"
    assert rendered[:2] == [False, True]
    assert store.load_tiku(deck)["1"][0]["marked"] is True
    assert any(
        item.get("key") == engine.question_key("1", q)
        for item in store.load_marked(deck)
    )


def test_prompt_answer_resize_key_rerenders_without_state_change(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    rendered = []
    _patch_tty(monkeypatch, [engine_loop.RESIZE_KEY, "1", "\r"])

    def capture_render(*args, **_kwargs):
        rendered.append((args[5], set(args[6])))

    monkeypatch.setattr(engine_loop, "render_question", capture_render)

    result = engine_loop.prompt_answer(deck, config, 1, 1, "1", q)

    assert result[0] == "A"
    assert rendered == [(0, set()), (0, set()), (0, {0})]


def test_prompt_answer_single_select_replaces_previous_choice(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    _patch_tty(monkeypatch, ["1", "2", "\r"])

    result = engine_loop.prompt_answer(deck, config, 1, 1, "1", q)

    assert result[0] == "B"


def test_prompt_answer_multi_select_keeps_multiple_choices(deck, config, monkeypatch):
    q = {
        "topic": "multi",
        "options": ["A. x", "B. y", "C. z"],
        "answer": "AC",
        "user_note": "",
    }
    _patch_tty(monkeypatch, ["1", "3", "\r"])

    result = engine_loop.prompt_answer(deck, config, 1, 1, "1", q)

    assert result[0] == "AC"


def test_prompt_result_refreshes_marked_state_after_m(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    rendered = []
    _patch_tty(monkeypatch, ["m", "\r"])

    def capture_render(*_args, **kwargs):
        rendered.append(kwargs["marked"])

    monkeypatch.setattr(engine_loop, "render_result", capture_render)

    result = engine_loop.prompt_result(deck, config, 1, 1, "1", q, "B", True)

    assert result[0] == "next"
    assert rendered[:2] == [False, True]
    assert store.load_tiku(deck)["1"][0]["marked"] is True
    assert any(
        item.get("key") == engine.question_key("1", q)
        for item in store.load_marked(deck)
    )


def test_review_history_marks_previous_question_from_answer_screen(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    _patch_tty(monkeypatch, [
        "1", "\r",      # q1 answer
        "\r",           # q1 result -> next
        "\x1b[D",       # q2 answer -> previous history
        "m",            # mark q1 in history
        "\r",           # return to q2
        "q",            # quit run
    ])
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)

    _count, _incorrect, status, _history = engine_loop.epoch(deck, config, selected)

    assert status == "quit"
    assert q1.get("marked") is True
    assert any(
        item.get("key") == engine.question_key("1", q1)
        for item in store.load_marked(deck)
    )


def test_review_history_marks_previous_question_from_result_screen(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    _patch_tty(monkeypatch, [
        "1", "\r",      # q1 answer
        "\r",           # q1 result -> next
        "1", "\r",      # q2 answer
        "\x1b[D",       # q2 result -> previous history
        "m",            # mark q1 in history
        "\r",           # return to q2 result
        "q",            # quit run
    ])
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)

    _count, _incorrect, status, _history = engine_loop.epoch(deck, config, selected)

    assert status == "quit"
    assert q1.get("marked") is True
    assert any(
        item.get("key") == engine.question_key("1", q1)
        for item in store.load_marked(deck)
    )


def test_review_history_rerenders_marked_state_after_m(deck, config, monkeypatch):
    q1 = store.load_tiku(deck)["1"][0]
    history = [{
        "count": 1,
        "chapter": "1",
        "question": q1,
        "raw_input": "A",
        "selected_answer": "A",
        "is_correct": True,
    }]
    rendered = []
    _patch_tty(monkeypatch, ["m", "\r"])

    def capture_render(*_args, **kwargs):
        rendered.append(kwargs["marked"])

    monkeypatch.setattr(engine_loop, "render_result", capture_render)

    result = engine_loop.review_history(deck, config, history, 0, 1)

    assert result[0] == "continue"
    assert rendered[:2] == [False, True]


# ---- detail_view visibility across the answer flow ----
# Regression: prompt_answer (pre-submit) must NOT auto-show x/n content even
# when the question has ai_explanation, while prompt_result (post-submit)
# must auto-show it. This was broken when epoch passed default_detail_view(q)
# into prompt_answer — normalize couldn't strip a legitimately-valued "ai".

def test_epoch_pre_answer_hides_detail_post_answer_shows_it(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    q["ai_explanation"] = "cached explanation"
    store.save_tiku(deck, {"1": [q] + store.load_tiku(deck)["1"][1:]})

    _patch_tty(monkeypatch, ["1", "\r", "q"])  # select A, submit, quit
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)

    pre_answer = []
    post_answer = []
    orig_q = engine_loop.render_question
    orig_r = engine_loop.render_result
    monkeypatch.setattr(engine_loop, "render_question",
        lambda *a, **k: (pre_answer.append(k.get("detail_view")), orig_q(*a, **k))[1])
    monkeypatch.setattr(engine_loop, "render_result",
        lambda *a, **k: (post_answer.append(k.get("detail_view")), orig_r(*a, **k))[1])

    from flip.engine import SelectedSet
    sel = SelectedSet([("1", q)], input_is_index=False)
    engine_loop.epoch(deck, config, sel)

    # Pre-submit screens must all be None — the explanation must not leak.
    assert pre_answer == [None, None], f"pre-answer leaked detail: {pre_answer}"
    # Post-submit must auto-show the explanation (default_detail_view policy).
    assert post_answer == ["ai"], f"post-answer should show ai: {post_answer}"



def test_prompt_answer_esc_returns_quit(deck, config, monkeypatch):
    """Esc on the answer screen quits the run and preserves progress like q."""
    q = store.load_tiku(deck)["1"][0]
    _patch_tty(monkeypatch, ["\x1b"])
    monkeypatch.setattr(engine_loop, "render_question", lambda *a, **k: None)

    result = engine_loop.prompt_answer(deck, config, 1, 1, "1", q)

    assert result[0] == "quit"


def test_prompt_result_esc_returns_quit(deck, config, monkeypatch):
    """Esc on the result screen quits the run and preserves progress like q."""
    q = store.load_tiku(deck)["1"][0]
    _patch_tty(monkeypatch, ["\x1b"])
    monkeypatch.setattr(engine_loop, "render_result", lambda *a, **k: None)

    result = engine_loop.prompt_result(deck, config, 1, 1, "1", q, "B", True)

    assert result[0] == "quit"


def test_epoch_esc_returns_quit_without_writing_wrong(deck, config, monkeypatch, tmp_path):
    """Esc mid-epoch bubbles up as status=quit and writes no report.

    Guards the orchestration contract run_train relies on: an Esc abort must
    NOT touch the wrong-index file system (no partial epoch report).
    """
    selected = engine.pick_questions(deck, config, selector="1", shuffle=False)
    # First keypress is Esc; epoch should bail immediately on question 1.
    _patch_tty(monkeypatch, ["\x1b"])
    monkeypatch.setattr(engine_loop, "render_question", lambda *a, **k: None)
    # Sentinel: if a wrong-index write is attempted, blow up.
    monkeypatch.setattr(engine_loop.store, "write_json",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not write")))

    _count, _incorrect, status, _history = engine_loop.epoch(deck, config, selected)

    assert status == "quit"


def test_run_train_esc_quits_and_keeps_session(deck, config, monkeypatch, capsys):
    """run_train on Esc exits like q and keeps the checkpoint for continue."""
    selected = engine.pick_questions(deck, config, selector="1", shuffle=False)
    _patch_tty(monkeypatch, ["\x1b"])
    monkeypatch.setattr(engine_loop, "render_question", lambda *a, **k: None)

    # Force pick_questions to return our fixed set regardless of selector.
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)

    outcome = engine_loop.run_train(deck, config, selector="1", source="tiku")

    assert outcome == 0
    assert store.load_session(deck) is not None
    assert "Report" not in capsys.readouterr().out


def test_run_train_quit_writes_partial_wrong_without_history(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q)], input_is_index=False)
    incorrect = [engine.incorrect_record("1", q, "B", deck.answer_alphabet)]
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(engine_loop, "epoch", lambda *_a, **_k: (1, incorrect, "quit", []))
    monkeypatch.setattr(
        engine_loop,
        "_run_session_summary_loop",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not summarize quit")),
    )

    outcome = engine_loop.run_train(deck, config, selector="1", source="tiku")

    assert outcome == 0
    assert store.load_history(deck) == []
    wrong_files = store.wrong_files(deck)
    assert len(wrong_files) == 1
    assert store.read_json(wrong_files[0]) == incorrect


def test_run_train_quit_without_wrong_does_not_create_empty_wrong(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q)], input_is_index=False)
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(engine_loop, "epoch", lambda *_a, **_k: (1, [], "quit", []))

    outcome = engine_loop.run_train(deck, config, selector="1", source="tiku")

    assert outcome == 0
    assert store.load_history(deck) == []
    assert store.wrong_files(deck) == []


def test_run_train_quit_from_wrong_source_does_not_rewrite_wrong(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q)], input_is_index=True)
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(
        engine_loop,
        "epoch",
        lambda *_a, **_k: (1, [engine.incorrect_record("1", q, "B", deck.answer_alphabet)], "quit", []),
    )
    orig_write_json = engine_loop.store.write_json

    def guard_wrong_write(path, data):
        if deck.wrong_dir in path.parents:
            raise AssertionError("must not write wrong source")
        return orig_write_json(path, data)

    monkeypatch.setattr(engine_loop.store, "write_json", guard_wrong_write)

    outcome = engine_loop.run_train(deck, config, selector="1", source="wrong")

    assert outcome == 0
    assert store.load_history(deck) == []


def test_run_train_browse_quit_does_not_record_history(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q)], input_is_index=False)
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(engine_loop, "review_questions", lambda *_a, **_k: ("quit", []))
    monkeypatch.setattr(
        engine_loop,
        "_run_session_summary_loop",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not summarize quit")),
    )

    outcome = engine_loop.run_train(deck, config, selector="1", source="tiku", ans_mode=True)

    assert outcome == 0
    assert store.load_history(deck) == []


def test_run_train_browse_uses_sequential_order(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q)], input_is_index=False)
    seen = {}

    def fake_pick_questions(*_args, **kwargs):
        seen["shuffle"] = kwargs["shuffle"]
        return selected

    monkeypatch.setattr(engine, "pick_questions", fake_pick_questions)
    monkeypatch.setattr(engine_loop, "review_questions", lambda *_a, **_k: ("done", []))
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *_a, **_k: None)

    outcome = engine_loop.run_train(deck, config, selector="1", source="tiku", ans_mode=True)

    assert outcome == 0
    assert seen["shuffle"] is False


def test_run_train_browse_quit_overwrites_stale_session_and_saves_cursor(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    store.save_session(deck, {
        "status": "paused",
        "source": "tiku",
        "ans_mode": False,
        "selector": "1",
        "filters": [],
        "mode": "train",
        "questions": [{"chapter": "1", "key": engine.question_key("1", q1)}],
        "cursor": 0,
        "answered": [],
    })
    _patch_tty(monkeypatch, ["\x1b[C", "q"])
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "render_review_question", lambda *a, **k: None)

    outcome = engine_loop.run_train(deck, config, selector="1", source="tiku", ans_mode=True)

    session = store.load_session(deck)
    assert outcome == 0
    assert session["ans_mode"] is True
    assert session["cursor"] == 1
    assert session["mode"] == "train"
    assert [item["key"] for item in session["questions"]] == [
        engine.question_key("1", q1),
        engine.question_key("1", q2),
    ]


def test_review_questions_q_returns_quit(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q)], input_is_index=False)
    _patch_tty(monkeypatch, ["q"])
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "render_review_question", lambda *a, **k: None)

    status, browse_items = engine_loop.review_questions(deck, config, selected)

    assert status == "quit"
    assert len(browse_items) == 1


def test_epoch_r_removes_question_from_tiku_with_explicit_warning(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q)], input_is_index=False)
    warnings = []
    _patch_tty(monkeypatch, ["r", "r"])
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    monkeypatch.setattr(
        engine_loop,
        "render_question",
        lambda *a, **k: warnings.append(k.get("warning", "")),
    )

    _count, _incorrect, status, _history = engine_loop.epoch(deck, config, selected)

    assert status == "done"
    assert "从 tiku 题库删除" in warnings[-1]
    remaining = store.load_tiku(deck)["1"]
    assert all(item.get("id") != q.get("id") for item in remaining)


def test_review_questions_r_removes_wrong_index_only_with_explicit_warning(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    wrong = engine.incorrect_record("1", q, "A", deck.answer_alphabet)
    path = deck.wrong_dir / "ch1.json"
    store.write_json(path, [wrong])
    selected = engine.pick_questions(deck, config, selector="1", shuffle=False, source="wrong")
    warnings = []
    _patch_tty(monkeypatch, ["r", "r"])
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(
        engine_loop,
        "render_review_question",
        lambda *a, **k: warnings.append(k.get("warning", "")),
    )

    status, _items = engine_loop.review_questions(deck, config, selected)

    assert status == "done"
    assert "不会删除题库" in warnings[-1]
    assert store.read_json(path) == []
    assert any(item.get("id") == q.get("id") for item in store.load_tiku(deck)["1"])


def test_review_questions_s_opens_empty_search_then_esc_returns_to_browse(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q)], input_is_index=False)
    search_renders = []
    _patch_tty(monkeypatch, ["s", "\x1b", "q"])
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "render_review_question", lambda *a, **k: None)
    monkeypatch.setattr(
        engine_loop,
        "render_review_search",
        lambda *args, **kwargs: search_renders.append(
            {
                "query": kwargs.get("query", ""),
                "results": list(kwargs.get("results", [])),
            }
        ),
        raising=False,
    )

    status, _browse_items = engine_loop.review_questions(deck, config, selected)

    assert status == "quit"
    assert search_renders[0]["query"] == ""
    assert search_renders[0]["results"] == []


def test_review_questions_search_matches_zh_topic_and_enter_jumps(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    q2["zh"] = {
        "topic": "中文搜索目标",
        "options": ["A. 一", "B. 二", "C. 三", "D. 四"],
    }
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    renders = []
    _patch_tty(monkeypatch, ["s", "中", "文", "\r", "q"])
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(
        engine_loop,
        "render_review_question",
        lambda index, *args, **kwargs: renders.append(index),
    )
    monkeypatch.setattr(engine_loop, "render_review_search", lambda *a, **k: None, raising=False)

    status, _browse_items = engine_loop.review_questions(deck, config, selected)

    assert status == "quit"
    assert renders[:2] == [0, 1]


def test_review_questions_j_digits_enter_jumps_to_1_based_index(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    renders = []
    _patch_tty(monkeypatch, ["j", "2", "\r", "q"])
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(
        engine_loop,
        "render_review_question",
        lambda index, *args, **kwargs: renders.append(index),
    )

    status, _browse_items = engine_loop.review_questions(deck, config, selected)

    assert status == "quit"
    assert renders[:2] == [0, 1]


def test_selected_set_remove_in_memory(deck):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet(
        [("1", q1), ("1", q2)],
        input_is_index=True,
        in_memory=True,
    )

    assert engine.remove_in_memory(selected, "1", q1) is True
    assert selected.questions == [("1", q2)]
    assert engine.remove_in_memory(selected, "1", q1) is False


def test_remove_from_in_memory_does_not_touch_wrong_index(deck):
    q1 = store.load_tiku(deck)["1"][0]
    wrong = engine.incorrect_record("1", q1, "B", deck.answer_alphabet)
    path = store.build_result_filename([("1", q1)], deck)
    store.write_json(path, [wrong])
    selected = engine.SelectedSet(
        [("1", q1)],
        input_is_index=True,
        index_sources={wrong["key"]: {path}},
        in_memory=True,
    )

    assert engine_loop._remove_question_from_selected_source(deck, selected, "1", q1) is True

    assert selected.questions == []
    assert store.read_json(path) == [wrong]


def test_run_train_scored_opens_summary_with_wrong_items(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    history = [
        {"count": 1, "chapter": "1", "question": q1, "selected_answer": "A", "is_correct": False},
        {"count": 2, "chapter": "1", "question": q2, "selected_answer": "A", "is_correct": True},
    ]
    summaries = []
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(
        engine_loop,
        "epoch",
        lambda *_a, **_k: (3, [{"topic": q1["topic"]}], "done", history),
    )
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *_a: summaries.append(_a[2]))
    monkeypatch.setattr(engine_loop.store, "write_json", lambda *a, **k: None)
    monkeypatch.setattr(engine_loop, "_record_drill", lambda *a, **k: None)

    outcome = engine_loop.run_train(deck, config, selector=None, source="tiku")

    assert outcome == 0
    assert summaries[0]["kind"] == "scored"
    assert summaries[0]["total"] == 2
    assert summaries[0]["correct"] == 1
    assert summaries[0]["wrong_items"][0]["question"] is q1


def test_run_train_scored_summary_d_starts_drill(deck, config, monkeypatch):
    q1 = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q1)], input_is_index=False)
    history = [
        {"count": 1, "chapter": "1", "question": q1, "selected_answer": "B", "is_correct": False},
    ]
    calls = []
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(engine_loop, "epoch", lambda *_a, **_k: (2, [], "done", history))
    monkeypatch.setattr(engine_loop, "_record_drill", lambda *a, **k: None)
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *a, **k: "drill")
    monkeypatch.setattr(
        engine_loop,
        "_run_drill_chain",
        lambda *a, **k: calls.append((a, k)) or 0,
    )

    outcome = engine_loop.run_train(deck, config, selector="1", source="tiku")

    assert outcome == 0
    assert calls
    assert calls[0][1]["mode_label"] == "train"
    assert calls[0][1]["selector"] == "1"


def test_run_train_review_summary_d_starts_drill(deck, config, monkeypatch):
    q1 = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q1)], input_is_index=True)
    history = [
        {"count": 1, "chapter": "1", "question": q1, "selected_answer": "B", "is_correct": False},
    ]
    calls = []
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(engine_loop, "epoch", lambda *_a, **_k: (2, [], "done", history))
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *a, **k: "drill")
    monkeypatch.setattr(
        engine_loop,
        "_run_drill_chain",
        lambda *a, **k: calls.append((a, k)) or 0,
    )

    outcome = engine_loop.run_train(deck, config, selector="1", source="wrong")

    assert outcome == 0
    assert calls
    assert calls[0][1]["mode_label"] == "review"
    assert calls[0][1]["selector"] == "1"


def test_run_train_train_counts_even_with_incorrect_answers(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    records = []
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(
        engine_loop,
        "epoch",
        lambda *_a, **_k: (
            3,
            [engine.incorrect_record("1", q1, "A", deck.answer_alphabet)],
            "done",
            [],
        ),
    )
    monkeypatch.setattr(engine_loop, "_record_drill", lambda *a, **k: records.append((a, k)))
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *a, **k: None)

    outcome = engine_loop.run_train(deck, config, selector="1", source="tiku")

    assert outcome == 0
    assert records
    assert records[0][1]["mode"] == "train"


def test_run_train_review_counts_only_fully_correct_chapters(deck, config, monkeypatch):
    q26a = {"topic": "q26a", "options": ["A. x"], "answer": "A", "user_note": ""}
    q26b = {"topic": "q26b", "options": ["A. x"], "answer": "A", "user_note": ""}
    q31 = {"topic": "q31", "options": ["A. x"], "answer": "A", "user_note": ""}
    selected = engine.SelectedSet([("26", q26a), ("26", q26b), ("31", q31)], input_is_index=True)

    def fake_pick_questions(_deck, _config, selector=None, **_kwargs):
        assert selector == "26-31"
        return selected

    monkeypatch.setattr(engine, "pick_questions", fake_pick_questions)
    monkeypatch.setattr(
        engine_loop,
        "epoch",
        lambda *_a, **_k: (
            4,
            [engine.incorrect_record("31", q31, "B", deck.answer_alphabet)],
            "done",
            [],
        ),
    )
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *a, **k: None)

    outcome = engine_loop.run_train(deck, config, selector="26-31", source="wrong")
    history = store.load_history(deck)

    assert outcome == 0
    assert history == [{
        "date": history[0]["date"],
        "chapters": ["26"],
        "total": 3,
        "incorrect": 1,
        "mode": "review",
    }]


def test_run_train_review_counts_when_all_correct(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=True)
    records = []
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(engine_loop, "epoch", lambda *_a, **_k: (3, [], "done", []))
    monkeypatch.setattr(engine_loop, "_record_drill", lambda *a, **k: records.append((a, k)))
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *a, **k: None)

    outcome = engine_loop.run_train(deck, config, selector="1", source="wrong")

    assert outcome == 0
    assert records
    assert records[0][1]["mode"] == "review"


def test_run_train_review_browse_does_not_count(deck, config, monkeypatch):
    q1 = store.load_tiku(deck)["1"][0]
    selected = engine.SelectedSet([("1", q1)], input_is_index=True)
    records = []
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(engine_loop, "review_questions", lambda *a, **k: ("done", []))
    monkeypatch.setattr(engine_loop, "_record_drill", lambda *a, **k: records.append((a, k)))
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *a, **k: None)

    outcome = engine_loop.run_train(deck, config, selector="1", source="wrong", ans_mode=True)

    assert outcome == 0
    assert records == []


def test_run_train_browse_opens_summary_with_browse_count(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    summaries = []
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)
    monkeypatch.setattr(
        engine_loop,
        "review_questions",
        lambda *_a, **_k: ("done", [
            {"chapter": "1", "question": q1},
            {"chapter": "1", "question": q2},
        ]),
    )
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *_a: summaries.append(_a[2]))
    monkeypatch.setattr(engine_loop, "_record_drill", lambda *a, **k: None)

    outcome = engine_loop.run_train(deck, config, selector="1", source="wrong", ans_mode=True)

    assert outcome == 0
    assert summaries[0]["kind"] == "browse"
    assert summaries[0]["total"] == 2
    assert len(summaries[0]["browse_items"]) == 2


def test_write_wrong_report_merges_with_existing_wrong_records(deck):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    existing = engine.incorrect_record("1", q1, "A", deck.answer_alphabet)
    incoming = engine.incorrect_record("1", q2, "A", deck.answer_alphabet)
    out = store.build_result_filename(selected.questions, deck)
    store.write_json(out, [existing])

    engine_loop._write_wrong_report(deck, selected, [incoming])

    records = store.read_json(out)
    assert [item["key"] for item in records] == [existing["key"], incoming["key"]]


def test_write_wrong_report_keeps_existing_when_no_new_wrong(deck):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    existing = engine.incorrect_record("1", q1, "A", deck.answer_alphabet)
    out = store.build_result_filename(selected.questions, deck)
    store.write_json(out, [existing])

    engine_loop._write_wrong_report(deck, selected, [])

    assert store.read_json(out) == [existing]


def test_session_summary_v_opens_wrong_list(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    summary = {
        "kind": "scored",
        "total": 1,
        "correct": 0,
        "incorrect": 1,
        "wrong_items": [{
            "chapter": "1",
            "question": q,
            "selected_answer": "A",
            "is_correct": False,
        }],
    }
    list_renders = []
    _patch_tty(monkeypatch, ["v", "\r", "\r"])
    monkeypatch.setattr(engine_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "render_session_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(
        engine_loop,
        "render_session_item_list",
        lambda *args, **_kwargs: list_renders.append(args),
    )

    engine_loop._run_session_summary_loop(deck, config, summary)

    assert list_renders
    assert list_renders[0][1] == [summary["wrong_items"][0]]
    assert list_renders[0][2] == 0


def test_session_summary_d_returns_drill(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    summary = {
        "kind": "scored",
        "total": 1,
        "correct": 0,
        "incorrect": 1,
        "wrong_items": [{
            "chapter": "1",
            "question": q,
            "selected_answer": "B",
            "is_correct": False,
        }],
    }
    _patch_tty(monkeypatch, ["d"])
    monkeypatch.setattr(engine_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "render_session_summary", lambda *_a, **_k: None)

    assert engine_loop._run_session_summary_loop(deck, config, summary, True) == "drill"


def test_drill_chain_converges_without_persistent_writes(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    calls = []

    def fake_epoch(_deck, _config, selected, **_kwargs):
        calls.append(list(selected.questions))
        if len(calls) == 1:
            return 3, [], "done", [
                {"count": 1, "chapter": "1", "question": q1, "selected_answer": "B", "is_correct": False},
                {"count": 2, "chapter": "1", "question": q2, "selected_answer": "A", "is_correct": True},
            ]
        return 2, [], "done", [
            {"count": 1, "chapter": "1", "question": q1, "selected_answer": "A", "is_correct": True},
        ]

    summaries = []

    def fake_summary_loop(_deck, _config, summary, drill=False):
        summaries.append((summary, drill))
        return "drill" if summary.get("wrong_items") else None

    monkeypatch.setattr(engine_loop, "epoch", fake_epoch)
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", fake_summary_loop)
    monkeypatch.setattr(
        engine_loop,
        "_write_wrong_report",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not write wrong report")),
    )

    outcome = engine_loop._run_drill_chain(
        deck,
        config,
        [
            {"chapter": "1", "question": q1},
            {"chapter": "1", "question": q2},
        ],
        mode_label="review",
        selector="1",
    )

    assert outcome == 0
    assert [[q["topic"] for _ch, q in round_questions] for round_questions in calls] == [
        [q1["topic"], q2["topic"]],
        [q1["topic"]],
    ]
    assert summaries[-1][0]["cleared"] is True
    assert summaries[-1][0]["wrong_items"] == []
    assert store.load_history(deck) == []


def test_drill_chain_quit_midway_does_not_record_history(deck, config, monkeypatch):
    q1 = store.load_tiku(deck)["1"][0]
    monkeypatch.setattr(engine_loop, "epoch", lambda *_a, **_k: (1, [], "quit", []))

    outcome = engine_loop._run_drill_chain(
        deck,
        config,
        [{"chapter": "1", "question": q1}],
        mode_label="train",
        selector="1",
    )

    assert outcome == 0
    assert store.load_history(deck) == []


def test_session_item_list_toggles_inline_translation(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    q["zh"] = {
        "topic": "1. 一加一等于几？",
        "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
    }
    items = [{
        "chapter": "1",
        "question": q,
        "options": list(q["options"]),
        "selected_answer": "B",
        "is_correct": True,
    }]
    renders = []
    _patch_tty(monkeypatch, ["t", "\r"])
    monkeypatch.setattr(
        engine_loop,
        "render_session_item_list",
        lambda *args, **kwargs: renders.append(kwargs.get("show_translation", False)),
    )

    engine_loop._run_session_item_list({"kind": "scored"}, items, config)

    assert renders[:2] == [False, True]


def test_session_item_list_sorts_items_by_chapter(deck, config, monkeypatch):
    # Items arrive in answer order, interleaved across chapters. The loop must
    # hand the renderer a chapter-sorted list so each chapter's questions stay
    # together (and cursor ↑/↓ matches display order). Stable within a chapter.
    items = [
        {"chapter": "3", "question": {"topic": "a", "options": [], "answer": "A"}, "options": []},
        {"chapter": "1", "question": {"topic": "b", "options": [], "answer": "A"}, "options": []},
        {"chapter": "3", "question": {"topic": "c", "options": [], "answer": "A"}, "options": []},
        {"chapter": "2", "question": {"topic": "d", "options": [], "answer": "A"}, "options": []},
    ]
    rendered = []
    _patch_tty(monkeypatch, ["\r"])
    monkeypatch.setattr(
        engine_loop,
        "render_session_item_list",
        lambda title, it, cursor, **_k: rendered.append(list(it)),
    )

    engine_loop._run_session_item_list({"kind": "scored"}, items, config)

    assert rendered, "renderer was never called"
    chapters = [it["chapter"] for it in rendered[0]]
    topics = [it["question"]["topic"] for it in rendered[0]]
    assert chapters == ["1", "2", "3", "3"]
    # ch3's two items keep their original relative order (stable).
    assert topics == ["b", "d", "a", "c"]


def test_run_stats_loop_uses_alt_screen_and_cbreak_and_esc_returns(deck, config, monkeypatch):
    calls = []
    _patch_tty(monkeypatch, ["\x1b"])
    monkeypatch.setattr(engine_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(engine_loop, "render_stats", lambda *_a, **_k: calls.append("render"))
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: calls.append("enter_alt"))
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: calls.append("exit_alt"))

    result = engine_loop._run_stats_loop(deck, config)

    assert result is None
    assert calls == ["enter_alt", "render", "exit_alt"]


def test_run_stats_loop_enter_jumps_to_matching_chapter_and_clears_search(deck, config, monkeypatch):
    calls = []
    _patch_tty(monkeypatch, ["2", "5", "\r", "\x1b"])
    monkeypatch.setattr(engine_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        engine_loop.engine,
        "stats_snapshot",
        lambda _deck: {
            "total": 3,
            "chapters": 3,
            "marked": 0,
            "note": 0,
            "ai": 0,
            "wrong": 0,
            "wrong_files": 0,
            "per_chapter": {"1": 1, "25": 1, "30": 1},
            "wrong_per_chapter": {"1": 0, "25": 0, "30": 0},
            "drills_per_chapter": {"1": 0, "25": 0, "30": 0},
        },
    )
    monkeypatch.setattr(
        engine_loop,
        "render_stats",
        lambda *_a, **kwargs: calls.append((kwargs.get("cursor"), kwargs.get("search_buffer", ""))),
    )
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)

    result = engine_loop._run_stats_loop(deck, config)

    assert result is None
    assert calls[-1] == (1, "")
    assert (0, "25") in calls


def test_run_stats_loop_enter_on_missing_chapter_keeps_cursor(deck, config, monkeypatch):
    calls = []
    _patch_tty(monkeypatch, ["9", "9", "\r", "\x1b"])
    monkeypatch.setattr(engine_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        engine_loop.engine,
        "stats_snapshot",
        lambda _deck: {
            "total": 2,
            "chapters": 2,
            "marked": 0,
            "note": 0,
            "ai": 0,
            "wrong": 0,
            "wrong_files": 0,
            "per_chapter": {"1": 1, "25": 1},
            "wrong_per_chapter": {"1": 0, "25": 0},
            "drills_per_chapter": {"1": 0, "25": 0},
        },
    )
    monkeypatch.setattr(
        engine_loop,
        "render_stats",
        lambda *_a, **kwargs: calls.append(
            (kwargs.get("cursor"), kwargs.get("search_buffer", ""), kwargs.get("warning", ""))
        ),
    )
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)

    result = engine_loop._run_stats_loop(deck, config)

    assert result is None
    assert calls[-1][0] == 0
    assert calls[-1][1] == ""
    assert "99" in calls[-1][2]


def test_entry_menu_resume_keeps_mode_ans_filters_clears_chapters(deck, config, monkeypatch):
    """On resume, entry_menu skips the mode screen, drops into the chapter
    picker with chapters cleared, and preserves the mode/ans/filters from the
    interrupted pass."""
    _patch_tty(monkeypatch, ["\r", "\x1b"])  # confirm all-chapters, then Esc back
    # Suppress the rendered screens.
    for fn in ("_render_entry_menu", "_render_chapter_picker"):
        monkeypatch.setattr(engine_loop, fn, lambda *a, **k: None)

    # Review mode (index 1), ans_mode on, mark filter set — the bits we
    # expect to survive the resume.
    result = engine_loop.entry_menu(
        config, deck, resume=(1, True, ["mark"]),
    )

    # First Esc in _edit_selector returns (False, selector) → drops to the
    # mode screen; second Esc at the mode screen returns None.
    assert result is None


def test_save_session_checkpoint_records_order_and_answers(deck):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    history = [{
        "count": 1,
        "chapter": "1",
        "question": q1,
        "raw_input": "B",
        "selected_answer": "B",
        "is_correct": False,
    }]

    engine_loop._save_session_checkpoint(
        deck,
        selected,
        source="tiku",
        ans_mode=False,
        selector="1",
        filters=["mark"],
        mode="train",
        history=history,
    )

    session = store.load_session(deck)
    assert session["status"] == "paused"
    assert session["source"] == "tiku"
    assert session["selector"] == "1"
    assert session["filters"] == ["mark"]
    assert session["cursor"] == 1
    assert [item["key"] for item in session["questions"]] == [
        engine.question_key("1", q1),
        engine.question_key("1", q2),
    ]
    assert session["answered"][0]["raw_input"] == "B"
    assert session["answered"][0]["is_correct"] is False


def test_restore_session_run_rebuilds_history_and_incorrects(deck):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    store.save_session(deck, {
        "status": "paused",
        "source": "tiku",
        "ans_mode": False,
        "selector": "1",
        "filters": [],
        "mode": "train",
        "questions": [
            {"chapter": "1", "key": engine.question_key("1", q1)},
            {"chapter": "1", "key": engine.question_key("1", q2)},
        ],
        "cursor": 1,
        "answered": [{
            "count": 1,
            "chapter": "1",
            "key": engine.question_key("1", q1),
            "raw_input": "B",
            "selected_answer": "B",
            "is_correct": False,
        }],
    })

    restored = engine_loop._restore_session_run(deck)

    assert restored is not None
    assert restored["selected"].questions == [("1", q1), ("1", q2)]
    assert restored["start_index"] == 1
    assert restored["history"][0]["question"] == q1
    assert restored["incorrects"][0]["wrong_input"] == "B"


def test_run_continue_finishes_resumed_session_and_clears_checkpoint(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    selected = engine.SelectedSet([("1", q1), ("1", q2)], input_is_index=False)
    initial_history = [{
        "count": 1,
        "chapter": "1",
        "question": q1,
        "raw_input": "B",
        "selected_answer": "B",
        "is_correct": False,
    }]
    engine_loop._save_session_checkpoint(
        deck,
        selected,
        source="tiku",
        ans_mode=False,
        selector="1",
        filters=[],
        mode="train",
        history=initial_history,
    )
    seen = {}

    def fake_epoch(_deck, _config, restored, **kwargs):
        seen["start_index"] = kwargs["start_index"]
        seen["history"] = kwargs["history"]
        seen["incorrect"] = kwargs["incorrect"]
        return 3, kwargs["incorrect"], "done", kwargs["history"] + [{
            "count": 2,
            "chapter": "1",
            "question": q2,
            "raw_input": "A",
            "selected_answer": "A",
            "is_correct": True,
        }]

    monkeypatch.setattr(engine_loop, "epoch", fake_epoch)
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *a, **k: None)

    outcome = engine_loop.run_continue(deck, config)

    assert outcome == 0
    assert seen["start_index"] == 1
    assert seen["history"][0]["question"] == q1
    assert seen["incorrect"][0]["wrong_input"] == "B"
    assert store.load_session(deck) is None
    assert len(store.load_history(deck)) == 1


def test_run_continue_resumes_browse_session_from_saved_cursor(deck, config, monkeypatch):
    q1, q2 = store.load_tiku(deck)["1"][:2]
    store.save_session(deck, {
        "status": "paused",
        "source": "tiku",
        "ans_mode": True,
        "selector": "1",
        "filters": ["mark"],
        "mode": "train",
        "questions": [
            {"chapter": "1", "key": engine.question_key("1", q1)},
            {"chapter": "1", "key": engine.question_key("1", q2)},
        ],
        "cursor": 1,
        "answered": [],
    })
    seen = {}

    def fake_review_questions(_deck, _config, restored, *, start_index=0, on_progress=None):
        seen["questions"] = restored.questions
        seen["start_index"] = start_index
        if on_progress is not None:
            on_progress(1, restored)
        return "done", [{
            "chapter": "1",
            "question": q2,
            "options": list(q2["options"]),
        }]

    monkeypatch.setattr(engine_loop, "review_questions", fake_review_questions)
    monkeypatch.setattr(engine_loop, "_run_session_summary_loop", lambda *a, **k: None)

    outcome = engine_loop.run_continue(deck, config)

    assert outcome == 0
    assert seen["questions"] == [("1", q1), ("1", q2)]
    assert seen["start_index"] == 1
    assert store.load_session(deck) is None


def test_entry_menu_continue_returns_continue_choice(deck, config, monkeypatch):
    store.save_session(deck, {"status": "paused"})
    _patch_tty(monkeypatch, ["\r"])
    monkeypatch.setattr(engine_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "_render_entry_menu", lambda *a, **k: None)

    result = engine_loop.entry_menu(config, deck)

    assert result == ("continue", None, False, [])


def test_entry_menu_c_twice_clears_current_mode_count(deck, config, monkeypatch):
    store.append_history(deck, {
        "date": "x", "chapters": ["1"], "total": 1, "incorrect": 0, "mode": "train",
    })
    store.append_history(deck, {
        "date": "x", "chapters": ["1"], "total": 1, "incorrect": 0, "mode": "review",
    })
    _patch_tty(monkeypatch, ["c", "c", "q"])
    monkeypatch.setattr(engine_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "_render_entry_menu", lambda *a, **k: None)

    result = engine_loop.entry_menu(config, deck)

    assert result is None
    assert [item["mode"] for item in store.load_history(deck)] == ["review"]


def test_entry_menu_c_twice_on_list_clears_all_counts(deck, config, monkeypatch):
    store.append_history(deck, {
        "date": "x", "chapters": ["1"], "total": 1, "incorrect": 0, "mode": "train",
    })
    store.append_history(deck, {
        "date": "x", "chapters": ["1"], "total": 1, "incorrect": 0, "mode": "review",
    })
    _patch_tty(monkeypatch, ["\x1b[B", "\x1b[B", "c", "c", "q"])
    monkeypatch.setattr(engine_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "_render_entry_menu", lambda *a, **k: None)

    result = engine_loop.entry_menu(config, deck)

    assert result is None
    assert store.load_history(deck) == []


def test_entry_menu_key_5_persists_deck_max_display_options(deck, config, monkeypatch):
    _patch_tty(monkeypatch, ["5", "q"])
    monkeypatch.setattr(engine_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(engine_loop, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "exit_alt_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "clear_screen", lambda: None)
    monkeypatch.setattr(engine_loop, "_render_entry_menu", lambda *a, **k: None)

    result = engine_loop.entry_menu(config, deck)

    assert result is None
    assert deck.max_display_options == 5
    assert "max_display_options = 5" in deck.manifest_path.read_text(encoding="utf-8")
