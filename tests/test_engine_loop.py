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


def test_selector_set_from_text_uses_engine_chapter_selector():
    assert engine_loop._selector_set_from_text(
        "5,3-4", ["1", "2", "3", "4", "5"], 5
    ) == {"3", "4", "5"}


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



def test_prompt_answer_esc_returns_back_to_selector(deck, config, monkeypatch):
    """Esc on the answer screen signals "go back to chapter picker"."""
    q = store.load_tiku(deck)["1"][0]
    _patch_tty(monkeypatch, ["\x1b"])
    monkeypatch.setattr(engine_loop, "render_question", lambda *a, **k: None)

    result = engine_loop.prompt_answer(deck, config, 1, 1, "1", q)

    assert result[0] == engine_loop.BACK_TO_SELECTOR


def test_prompt_result_esc_returns_back_to_selector(deck, config, monkeypatch):
    """Esc on the result screen signals "go back to chapter picker"."""
    q = store.load_tiku(deck)["1"][0]
    _patch_tty(monkeypatch, ["\x1b"])
    monkeypatch.setattr(engine_loop, "render_result", lambda *a, **k: None)

    result = engine_loop.prompt_result(deck, config, 1, 1, "1", q, "B", True)

    assert result[0] == engine_loop.BACK_TO_SELECTOR


def test_epoch_esc_returns_back_to_selector_without_writing_wrong(deck, config, monkeypatch, tmp_path):
    """Esc mid-epoch bubbles up as status=BACK_TO_SELECTOR and writes no report.

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

    _count, _incorrect, status = engine_loop.epoch(deck, config, selected)

    assert status == engine_loop.BACK_TO_SELECTOR


def test_run_train_esc_returns_back_to_selector_and_no_report(deck, config, monkeypatch, capsys):
    """run_train on Esc returns the sentinel verbatim and prints no report."""
    selected = engine.pick_questions(deck, config, selector="1", shuffle=False)
    _patch_tty(monkeypatch, ["\x1b"])
    monkeypatch.setattr(engine_loop, "render_question", lambda *a, **k: None)

    # Force pick_questions to return our fixed set regardless of selector.
    monkeypatch.setattr(engine, "pick_questions", lambda *a, **k: selected)

    outcome = engine_loop.run_train(deck, config, selector="1", source="tiku")

    assert outcome == engine_loop.BACK_TO_SELECTOR
    assert "Report" not in capsys.readouterr().out


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
