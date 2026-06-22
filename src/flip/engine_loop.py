"""TUI interaction loops for flip.

Faithful port of se_regressor.py's epoch/prompt_answer/prompt_result/
review_questions/entry_menu. Behavior is preserved; only the deck/config
plumbing changes (alphabet, translation toggle, manifest-driven prompt).

These loops drive the renderers in flip.tui and read keys via flip.tui.keys.
They are intentionally not unit-tested — coverage is by manual run against
the example deck.
"""

import datetime
import sys

from . import store
from . import engine
from . import explain as explain_mod
from .config import Config
from .deck import DEFAULT_MAX_DISPLAY_OPTIONS, Deck, save_max_display_options
from .tui import (
    read_key, save_tty, restore_tty, enter_cbreak,
    clear_screen, enter_alt_screen, exit_alt_screen,
    has_translation, has_agent_said, has_user_note,
    translated_question, default_detail_view, normalize_detail_view,
    render_question, render_result, render_review_question,
    render_ai_waiting, render_ai_prompt_input, render_note_input,
)


# Sentinel a prompt loop returns to signal "Esc pressed — go back to the
# chapter picker (phase 3), keep the mode/ans/filters but clear the chapters".
# Distinct from 'quit' (aborts the whole epoch) so callers can tell the two
# apart. It is a string (not a tuple) so it threads through the existing
# ('previous'|'quit'|'remove') plumbing without breaking the unpack shape.
BACK_TO_SELECTOR = 'back-to-selector'


def _options(q, deck=None):
    options = list(q.get("options", []))
    if deck is None:
        return options
    return options[:deck.max_display_options]


def _translated_options(q, options):
    translation = translated_question(q)
    if translation is None:
        return None
    visible_labels = [choice[:1].upper() for choice in options]
    by_label = {
        choice[:1].upper(): choice
        for choice in translation.get("options", [])
        if isinstance(choice, str) and choice
    }
    return {
        "topic": translation["topic"],
        "options": [by_label[label] for label in visible_labels if label in by_label],
    }


def _answer_from_selected(options, selected):
    answer = []
    for index in sorted(selected):
        answer.append(options[index][0].upper())
    return "".join(answer)


# ---- small sub-prompts (ai extra / note) ----

def _prompt_ai_extra(deck, chapter, q, render_current):
    buffer = ""
    options = _options(q, deck)
    while True:
        render_current(ai_prompt_buffer=buffer)
        key = read_key()
        if key == '\x03':
            raise KeyboardInterrupt
        if key in {'\r', '\n'}:
            return buffer
        if key in {'\x7f', '\b'}:
            buffer = buffer[:-1]
            continue
        if key == '\x1b':
            return None
        if len(key) == 1 and key.isprintable():
            buffer += key


def _prompt_user_note(deck, chapter, q, render_current):
    buffer = str(q.get("user_note", "") or "")
    while True:
        render_current(note_buffer=buffer)
        key = read_key()
        if key == '\x03':
            raise KeyboardInterrupt
        if key in {'\r', '\n'}:
            return buffer.strip()
        if key in {'\x7f', '\b'}:
            buffer = buffer[:-1]
            continue
        if key == '\x1b':
            return None
        if len(key) == 1 and key.isprintable():
            buffer += key


def _edit_user_note(deck, chapter, q, render_current):
    note = _prompt_user_note(deck, chapter, q, render_current)
    if note is None:
        return False
    q["user_note"] = note
    engine.save_question_field(deck, chapter, q)
    return True


def _request_ai(deck, config, chapter, q, render_current, force=False):
    if q.get("ai_explanation") and not force:
        return True
    extra = _prompt_ai_extra(deck, chapter, q, render_current)
    if extra is None:
        return False
    render_current(ai_waiting=True)
    engine.ensure_ai_explanation(deck, config, chapter, q, extra, force=force)
    return True


def _open_agent_tab(deck, config, chapter, q, render_current):
    if has_agent_said(q):
        return "ai"
    if _request_ai(deck, config, chapter, q, render_current):
        return "ai" if has_agent_said(q) else None
    return default_detail_view(q)


def _open_note_tab(deck, chapter, q, render_current):
    if has_user_note(q):
        return "note"
    if _edit_user_note(deck, chapter, q, render_current):
        return "note" if has_user_note(q) else default_detail_view(q)
    return default_detail_view(q)


def _edit_current_detail(deck, config, chapter, q, detail_view, render_current):
    detail_view = normalize_detail_view(q, detail_view)
    if detail_view == "ai":
        if _request_ai(deck, config, chapter, q, render_current, force=True):
            return ("ai" if has_agent_said(q) else default_detail_view(q)), ""
        return detail_view, ""
    if detail_view == "note":
        if _edit_user_note(deck, chapter, q, render_current):
            return ("note" if has_user_note(q) else default_detail_view(q)), ""
        return detail_view, ""
    return detail_view, "当前没有可编辑的底部内容；按 x 生成 Agent Said 或 n 新建笔记。"


# ---- shared per-question key handling ----

def _handle_detail_keys(deck, config, chapter, q, detail_view, key, render_current):
    """Dispatch the shared detail/mark/explain/note/edit/quit keys.

    Returns a 3-tuple consumed by every prompt loop (prompt_answer,
    prompt_result, review_history, review_questions):
      (detail_view, warning, action)

    - detail_view: possibly-updated "ai"|"note"|None (which bottom block shows)
    - warning:     a user-facing warning string, or "" (e.g. "已从索引中移除")
    - action:      None to keep looping, or a sentinel tuple:
                     ('quit',)     -> caller returns 'quit'
                   Note: 'previous' and 'remove' are handled by each loop
                   locally (they depend on loop-specific state like history
                   pointers), so this helper only emits 'quit' as an action.

    Centralizing x/n/e here avoids ~4 copies of the same key-handling code
    across the four prompt loops.
    """
    if key in {'q', 'Q'}:
        return detail_view, "", ('quit',)
    if key == '\x03':
        raise KeyboardInterrupt
    if key in {'m', 'M'}:
        engine.toggle_marked(deck, chapter, q)
        return detail_view, "", None
    if key in {'x', 'X'}:
        return _open_agent_tab(deck, config, chapter, q, render_current), "", None
    if key in {'n', 'N'}:
        return _open_note_tab(deck, chapter, q, render_current), "", None
    if key in {'e', 'E'}:
        dv, warning = _edit_current_detail(deck, config, chapter, q, detail_view, render_current)
        return normalize_detail_view(q, dv), warning, None
    return detail_view, "", None


# ---- answer input loop (training mode, pre-submit) ----

def prompt_answer(deck, config, count, total, chapter, q, *,
                  show_translation=False, detail_view=None,
                  previous_available=False, removable=False, selected_set=None):
    """Pre-submit answer input loop for one question in training mode.

    Drives a small keyboard state machine over the option list:
      ↑/↓        move cursor
      Space      toggle the cursor option in/out of `selected`
      1..N       quick-toggle option N by digit (N bounded by options length)
      Enter      submit (requires ≥1 selected); returns the letter string
      ←          go back into history (only if previous_available)
      t          toggle translation block (only if translation_enabled)
      m/x/n/e    mark / explain / note / edit-detail (via _handle_detail_keys)
      r          remove from index (only if removable; double-tap to confirm)
      q          quit the epoch

    Returns one of:
      ("<letters>", show_translation, detail_view)  — a submitted answer
      ('previous' | 'quit' | 'remove', show_translation, detail_view)

    The termios tty is put in cbreak for raw key reading and restored in
    `finally` so a Ctrl-C / crash doesn't leave the terminal broken.
    """
    options = _options(q, deck)
    alphabet = deck.answer_alphabet
    translation_enabled = config.translation_enabled
    detail_view = normalize_detail_view(q, detail_view)
    cursor = 0
    selected = set()
    warning = ""
    confirm_remove = False
    translation = _translated_options(q, options) if (translation_enabled and show_translation) else None
    if translation_enabled and show_translation and translation is None:
        show_translation = False
        warning = f"题库缺少 {config.target_lang} 字段，请先运行：flip deck {deck.slug} translate"

    old_settings = save_tty()
    model_name = deck.explain.resolve_model()
    try:
        enter_cbreak()
        while True:
            marked = engine.is_marked(deck, chapter, q)
            render_question(
                count, total, chapter, q, options, cursor, selected,
                warning=warning, show_translation=show_translation, translation=translation,
                detail_view=detail_view, marked=marked, model_name=model_name,
                translation_enabled=translation_enabled,
            )
            key = read_key()
            if key not in {'r', 'R'}:
                confirm_remove = False

            if key in {'\r', '\n'}:
                if not selected:
                    warning = "请先选择至少一个选项。"
                    continue
                return (_answer_from_selected(options, selected), show_translation, detail_view)

            if key == '\x1b[D':  # left
                if previous_available:
                    return ('previous', show_translation, detail_view)
                warning = "当前没有上一题。"
                continue
            if key == ' ':
                warning = ""
                if cursor in selected:
                    selected.remove(cursor)
                else:
                    selected.add(cursor)
                continue
            if key == '\x1b[A':  # up
                warning = ""
                cursor = (cursor - 1) % len(options)
                continue
            if key == '\x1b[B':  # down
                warning = ""
                cursor = (cursor + 1) % len(options)
                continue

            # digit quick-select: 1..N
            if key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(options):
                    warning = ""
                    if idx in selected:
                        selected.remove(idx)
                    else:
                        selected.add(idx)
                continue

            if translation_enabled and key in {'t', 'T'}:
                warning = ""
                if show_translation:
                    show_translation = False
                    continue
                show_translation = True
                if translation is None:
                    translation = _translated_options(q, options)
                    if translation is None:
                        show_translation = False
                        warning = f"题库缺少 {config.target_lang} 字段，请先运行：flip deck {deck.slug} translate"
                continue

            if key in {'r', 'R'}:
                if removable:
                    if confirm_remove:
                        return ('remove', show_translation, detail_view)
                    confirm_remove = True
                    warning = "再次按 r 确认移除，其他按键取消。"
                    continue
                warning = "当前输入不是索引文件，不能移除。"
                continue

            def render_current(ai_prompt_buffer=None, ai_waiting=False, note_buffer=None):
                marked_now = engine.is_marked(deck, chapter, q)
                render_question(
                    count, total, chapter, q, options, cursor, selected,
                    warning=warning, show_translation=show_translation, translation=translation,
                    detail_view=detail_view, marked=marked_now, model_name=model_name,
                    translation_enabled=translation_enabled,
                    ai_prompt_buffer=ai_prompt_buffer, ai_waiting=ai_waiting, note_buffer=note_buffer,
                )

            dv, w, action = _handle_detail_keys(deck, config, chapter, q, detail_view, key, render_current)
            detail_view = dv
            if w:
                warning = w
            if action:
                if action[0] == 'quit':
                    return ('quit', show_translation, detail_view)
            if key == '\x1b':
                return (BACK_TO_SELECTOR, show_translation, detail_view)
    finally:
        restore_tty(old_settings)


# ---- result loop (post-submit) ----

def prompt_result(deck, config, count, total, chapter, q, selected_answer, is_correct, *,
                  show_translation=False, detail_view=None,
                  previous_available=False, removable=False):
    """Post-submit result loop.

    Detail-view policy here differs from prompt_answer: we DEFAULT to showing
    x/n content (the agent explanation or user note) when present, rather than
    waiting for the user to press x/n. Rationale: the result screen is the
    "moment of feedback" — surfacing the explanation/note automatically saves
    a keypress at exactly the time the learner wants to see why.

    The in-loop x/n toggle still works, and because detail_view is re-derived
    from the question (not inherited) on every entry, the next question starts
    fresh — no stale open state carries over.
    """
    options = _options(q, deck)
    translation_enabled = config.translation_enabled
    # default_detail_view (not normalize) so x/n auto-shows when content exists.
    detail_view = default_detail_view(q)
    translation = _translated_options(q, options) if (translation_enabled and show_translation) else None
    if translation_enabled and show_translation and translation is None:
        show_translation = False
        warning = f"题库缺少 {config.target_lang} 字段，请先运行：flip deck {deck.slug} translate"
    else:
        warning = ""
    confirm_remove = False
    model_name = deck.explain.resolve_model()
    old_settings = save_tty()
    try:
        enter_cbreak()
        while True:
            marked = engine.is_marked(deck, chapter, q)
            render_result(
                count, total, chapter, q, options, selected_answer, is_correct,
                warning=warning, show_translation=show_translation, translation=translation,
                detail_view=detail_view, marked=marked, model_name=model_name,
                translation_enabled=translation_enabled,
            )
            key = read_key()
            if key not in {'r', 'R'}:
                confirm_remove = False
            if key in {'\r', '\n'}:
                return ('next', show_translation, detail_view)
            if key == '\x1b[D':
                if previous_available:
                    return ('previous', show_translation, detail_view)
                warning = "当前没有上一题。"
                continue
            if key in {'m', 'M'}:
                engine.toggle_marked(deck, chapter, q)
                warning = ""
                continue
            if translation_enabled and key in {'t', 'T'}:
                warning = ""
                if show_translation:
                    show_translation = False
                    continue
                show_translation = True
                if translation is None:
                    translation = _translated_options(q, options)
                    if translation is None:
                        show_translation = False
                        warning = f"题库缺少 {config.target_lang} 字段，请先运行：flip deck {deck.slug} translate"
                continue
            if key in {'r', 'R'}:
                if removable:
                    if confirm_remove:
                        return ('remove', show_translation, detail_view)
                    confirm_remove = True
                    warning = "再次按 r 确认移除，其他按键取消。"
                    continue
                warning = "当前输入不是索引文件，不能移除。"
                continue

            def render_current(ai_prompt_buffer=None, ai_waiting=False, note_buffer=None):
                marked_now = engine.is_marked(deck, chapter, q)
                render_result(
                    count, total, chapter, q, options, selected_answer, is_correct,
                    warning=warning, show_translation=show_translation, translation=translation,
                    detail_view=detail_view, marked=marked_now, model_name=model_name,
                    translation_enabled=translation_enabled,
                    ai_prompt_buffer=ai_prompt_buffer, ai_waiting=ai_waiting, note_buffer=note_buffer,
                )

            dv, w, action = _handle_detail_keys(deck, config, chapter, q, detail_view, key, render_current)
            detail_view = dv
            if w:
                warning = w
            if action and action[0] == 'quit':
                return ('quit', show_translation, detail_view)
            if key == '\x1b':
                return (BACK_TO_SELECTOR, show_translation, detail_view)
    finally:
        restore_tty(old_settings)


# ---- history review (within an epoch) ----

def review_history(deck, config, history, start_index, total, *,
                   show_translation=False, detail_view=None, removable=False, selected_set=None):
    """Browse the answered-so-far history within an epoch (←/→ to navigate).

    `start_index` is the entry to land on when entering history; callers pick
    it based on context:
      - from prompt_answer (before submitting): len(history)-1, the newest
        answered question.
      - from prompt_result (after submitting):  len(history)-2, because the
        result screen's question was just pushed, so "previous" is the one
        before it.

    Returns a sentinel the epoch loop acts on: 'continue' (resume the current
    question), 'quit' (abort the whole epoch), or BACK_TO_SELECTOR (Esc —
    leave the epoch and go back to the chapter picker, clearing chapters).
    ←/→ at the ends either clamp with a warning or, at the right edge, also
    return 'continue' to resume.
    """
    if not history:
        return 'continue', show_translation, detail_view
    index = max(0, min(start_index, len(history) - 1))
    detail_view = default_detail_view(history[index]["question"])
    warning = ""
    confirm_remove = False
    translation_enabled = config.translation_enabled
    model_name = deck.explain.resolve_model()
    old_settings = save_tty()
    try:
        enter_cbreak()
        while True:
            item = history[index]
            chapter = item["chapter"]
            q = item["question"]
            options = _options(q, deck)
            detail_view = normalize_detail_view(q, detail_view)
            translation = _translated_options(q, options) if (translation_enabled and show_translation) else None
            marked = engine.is_marked(deck, chapter, q)
            footer = "←/→ 后退/前进, Enter 返回当前" + \
                (", t 中文" if translation_enabled else "") + \
                ", " + _detail_hint(q) + ", m 标记, r 移除, q quit"
            render_result(
                item["count"], total, chapter, q, options,
                item["selected_answer"], item["is_correct"],
                warning=warning, show_translation=show_translation, translation=translation,
                detail_view=detail_view, marked=marked, model_name=model_name,
                translation_enabled=translation_enabled, footer=footer,
            )
            key = read_key()
            if key not in {'r', 'R'}:
                confirm_remove = False
            if key in {'q', 'Q'}:
                return 'quit', show_translation, detail_view
            if key == '\x03':
                raise KeyboardInterrupt
            if key == '\x1b':
                return BACK_TO_SELECTOR, show_translation, detail_view
            if key in {'\r', '\n'}:
                return 'continue', show_translation, detail_view
            if key == '\x1b[D':
                warning = ""
                if index > 0:
                    index -= 1
                    detail_view = default_detail_view(history[index]["question"])
                else:
                    warning = "已经是第一条历史记录。"
                continue
            if key == '\x1b[C':
                warning = ""
                if index < len(history) - 1:
                    index += 1
                    detail_view = default_detail_view(history[index]["question"])
                else:
                    return 'continue', show_translation, detail_view
                continue
            if key in {'m', 'M'}:
                engine.toggle_marked(deck, chapter, q)
                warning = ""
                continue
            if translation_enabled and key in {'t', 'T'}:
                warning = ""
                if show_translation:
                    show_translation = False
                    continue
                show_translation = True
                if translation is None:
                    translation = _translated_options(q, options)
                    if translation is None:
                        show_translation = False
                        warning = f"题库缺少 {config.target_lang} 字段，请先运行：flip deck {deck.slug} translate"
                continue
            if key in {'r', 'R'}:
                if removable and selected_set is not None:
                    if not confirm_remove:
                        confirm_remove = True
                        warning = "再次按 r 确认移除，其他按键取消。"
                        continue
                    if engine.remove_from_active_index(selected_set, chapter, q):
                        history.pop(index)
                        if not history:
                            return 'continue', show_translation, detail_view
                        index = min(index, len(history) - 1)
                        detail_view = default_detail_view(history[index]["question"])
                        warning = "已从索引中移除。"
                        confirm_remove = False
                        continue
                    warning = "未找到可移除的索引记录。"
                    continue
                warning = "当前输入不是索引文件，不能移除。"
                continue

            def render_current(ai_prompt_buffer=None, ai_waiting=False, note_buffer=None):
                marked_now = engine.is_marked(deck, chapter, q)
                render_result(
                    item["count"], total, chapter, q, options,
                    item["selected_answer"], item["is_correct"],
                    warning=warning, show_translation=show_translation, translation=translation,
                    detail_view=detail_view, marked=marked_now, model_name=model_name,
                    translation_enabled=translation_enabled, footer=footer,
                    ai_prompt_buffer=ai_prompt_buffer, ai_waiting=ai_waiting, note_buffer=note_buffer,
                )

            dv, w, action = _handle_detail_keys(deck, config, chapter, q, detail_view, key, render_current)
            detail_view = dv
            if w:
                warning = w
    finally:
        restore_tty(old_settings)


def _detail_hint(q):
    agent_state = "有" if has_agent_said(q) else "无"
    note_state = "有" if has_user_note(q) else "无"
    return "x Agent[" + agent_state + "], n 笔记[" + note_state + "], e 编辑当前"


# ---- top-level epoch (training) ----

def epoch(deck, config, selected_set):
    """Run one training pass over selected_set.questions.

    For each question the flow is:
      1. prompt_answer — collect the learner's selection (or 'previous' to
         jump back into history, or 'remove' to drop an index entry).
      2. Grade it; wrong answers are appended to `incorrect` and to `history`.
      3. prompt_result — show correctness, let the learner browse back via
         'previous' or move on with Enter.

    History navigation uses a subtle double-pointer convention (see
    review_history's start_index arg): from the *answer* screen, ← opens
    history at len-1 (the just-answered question is newest); from the
    *result* screen, ← opens at len-2 (because the result-screen question
    has already been pushed to history, so the "previous" one is len-2).

    Writes a wrong-index file at the end ONLY when training on tiku
    (input_is_index=False). Reviewing the wrong-index itself never spawns
    a new wrong file.

    Returns (count, incorrect, status) where status is one of:
      'done'              — every question was attempted normally
      'quit'              — q / Esc-abort: leave flip entirely
      BACK_TO_SELECTOR    — Esc pressed mid-question: go back to the chapter
                            picker, keep mode/ans/filters but clear chapters
    """
    questions = selected_set.questions
    alphabet = deck.answer_alphabet
    incorrect = []
    count = 1
    show_translation = False
    detail_view = None
    history = []
    try:
        enter_alt_screen()
        for question in questions:
            chapter, q = question
            # Pre-answer screen must NOT auto-show x/n content (that would
            # leak the explanation before the learner commits an answer).
            # Force None here; prompt_answer keeps it None via normalize, and
            # prompt_result re-derives via default_detail_view to auto-show.
            detail_view = None
            while True:
                inpu, show_translation, detail_view = prompt_answer(
                    deck, config, count, len(questions), chapter, q,
                    show_translation=show_translation, detail_view=detail_view,
                    previous_available=bool(history),
                    removable=selected_set.input_is_index,
                    selected_set=selected_set,
                )
                if inpu == 'quit':
                    return count, incorrect, 'quit'
                if inpu == BACK_TO_SELECTOR:
                    return count, incorrect, BACK_TO_SELECTOR
                if inpu == 'remove':
                    engine.remove_from_active_index(selected_set, chapter, q)
                    break
                if inpu == 'previous':
                    haction, show_translation, detail_view = review_history(
                        deck, config, history, len(history) - 1, len(questions),
                        show_translation=show_translation, detail_view=detail_view,
                        removable=selected_set.input_is_index, selected_set=selected_set,
                    )
                    if haction == 'quit':
                        return count, incorrect, 'quit'
                    if haction == BACK_TO_SELECTOR:
                        return count, incorrect, BACK_TO_SELECTOR
                    continue
                break
            if inpu == 'remove':
                clear_screen()
                count += 1
                continue

            parsed = engine.parse_answer(str.upper(inpu), alphabet)
            is_correct = parsed == q.get("answer", "")
            if not is_correct:
                incorrect.append(engine.incorrect_record(chapter, q, inpu, alphabet))
            history.append({
                "count": count,
                "chapter": chapter,
                "question": q,
                "selected_answer": parsed,
                "is_correct": is_correct,
            })

            while True:
                raction, show_translation, detail_view = prompt_result(
                    deck, config, count, len(questions), chapter, q, parsed, is_correct,
                    show_translation=show_translation, detail_view=detail_view,
                    previous_available=len(history) > 1,
                    removable=selected_set.input_is_index,
                )
                if raction == 'quit':
                    return count, incorrect, 'quit'
                if raction == BACK_TO_SELECTOR:
                    return count, incorrect, BACK_TO_SELECTOR
                if raction == 'remove':
                    engine.remove_from_active_index(selected_set, chapter, q)
                    break
                if raction == 'previous':
                    haction, show_translation, detail_view = review_history(
                        deck, config, history, len(history) - 2, len(questions),
                        show_translation=show_translation, detail_view=detail_view,
                        removable=selected_set.input_is_index, selected_set=selected_set,
                    )
                    if haction == 'quit':
                        return count, incorrect, 'quit'
                    if haction == BACK_TO_SELECTOR:
                        return count, incorrect, BACK_TO_SELECTOR
                    continue
                break

            clear_screen()
            count += 1
        return count, incorrect, 'done'
    finally:
        exit_alt_screen()


# ---- standalone review (browse a question set) ----

def review_questions(deck, config, selected_set):
    questions = selected_set.questions
    if not questions:
        print("No questions to review.")
        return
    index = 0
    show_translation = False
    translation_enabled = config.translation_enabled
    detail_view = default_detail_view(questions[0][1])
    warning = ""
    confirm_remove = False
    model_name = deck.explain.resolve_model()
    old_settings = save_tty()
    try:
        enter_alt_screen()
        enter_cbreak()
        while True:
            chapter, q = questions[index]
            options = _options(q, deck)
            detail_view = normalize_detail_view(q, detail_view)
            translation = _translated_options(q, options) if (translation_enabled and show_translation) else None
            marked = engine.is_marked(deck, chapter, q)
            render_review_question(
                index, len(questions), chapter, q,
                options=options, show_translation=show_translation, translation=translation,
                detail_view=detail_view, marked=marked, warning=warning,
                model_name=model_name, translation_enabled=translation_enabled,
            )
            key = read_key()
            if key not in {'r', 'R'}:
                confirm_remove = False
            if key in {'q', 'Q'}:
                return
            if key == '\x03':
                raise KeyboardInterrupt
            if key == '\x1b':
                return BACK_TO_SELECTOR
            if key == '\x1b[C':
                warning = ""
                if index < len(questions) - 1:
                    index += 1
                    detail_view = default_detail_view(questions[index][1])
                else:
                    warning = "已经是最后一题。"
                continue
            if key == '\x1b[D':
                warning = ""
                if index > 0:
                    index -= 1
                    detail_view = default_detail_view(questions[index][1])
                else:
                    warning = "已经是第一题。"
                continue
            if translation_enabled and key in {'t', 'T'}:
                warning = ""
                if show_translation:
                    show_translation = False
                    continue
                translation = _translated_options(q, options)
                if translation is None:
                    warning = f"题库缺少 {config.target_lang} 字段，请先运行：flip deck {deck.slug} translate"
                    continue
                show_translation = True
                continue
            if key in {'m', 'M'}:
                engine.toggle_marked(deck, chapter, q)
                warning = ""
                continue
            if key in {'r', 'R'}:
                if selected_set.input_is_index:
                    if not confirm_remove:
                        confirm_remove = True
                        warning = "再次按 r 确认移除，其他按键取消。"
                        continue
                    if engine.remove_from_active_index(selected_set, chapter, q):
                        questions.pop(index)
                        if not questions:
                            return
                        index = min(index, len(questions) - 1)
                        detail_view = default_detail_view(questions[index][1])
                        warning = "已从索引中移除。"
                        confirm_remove = False
                        continue
                    warning = "未找到可移除的索引记录。"
                    continue
                warning = "当前输入不是索引文件，不能移除。"
                continue

            def render_current(ai_prompt_buffer=None, ai_waiting=False, note_buffer=None):
                marked_now = engine.is_marked(deck, chapter, q)
                render_review_question(
                    index, len(questions), chapter, q,
                    options=options, show_translation=show_translation, translation=translation,
                    detail_view=detail_view, marked=marked_now, warning=warning,
                    model_name=model_name, translation_enabled=translation_enabled,
                    ai_prompt_buffer=ai_prompt_buffer, ai_waiting=ai_waiting, note_buffer=note_buffer,
                )

            dv, w, action = _handle_detail_keys(deck, config, chapter, q, detail_view, key, render_current)
            detail_view = normalize_detail_view(q, dv)
            if w:
                warning = w
            if action and action[0] == 'quit':
                return
    finally:
        restore_tty(old_settings)
        exit_alt_screen()


# ---- stats ----

def render_stats(deck, config):
    stats = engine.stats_snapshot(deck)
    from .tui.render import (
        AI_COLOR, DIM_COLOR, DRILL_COLOR, RESET_COLOR, STAT_TOTAL_COLOR,
    )
    clear_screen()
    print("@ 全局统计 —", deck.name)
    print()
    print("  题目总数:", stats["total"])
    print("  章节数:", stats["chapters"])
    print("  已标记:", stats["marked"])
    print("  有笔记:", stats["note"])
    print("  有 Agent Said:", stats["ai"])
    print("  wrong 去重题数:", stats["wrong"])
    print("  wrong 文件数:", stats["wrong_files"])
    print()
    print("  题量 / 错题分布:")
    print("  整条柱=全部题量  " + AI_COLOR + "黄色" + RESET_COLOR + "=wrong 错题  "
          + STAT_TOTAL_COLOR + "白色" + RESET_COLOR + "=其余题量  "
          + DIM_COLOR + "灰色" + RESET_COLOR + "=相对最大章节余量")
    max_total = max(stats["per_chapter"].values(), default=0)
    for chapter in sorted(stats["per_chapter"], key=store._chapter_sort_key):
        total = stats["per_chapter"][chapter]
        wrong = stats["wrong_per_chapter"].get(chapter, 0)
        drills = stats.get("drills_per_chapter", {}).get(chapter, 0)
        ratio = wrong / total if total else 0
        bar = _stats_bar(total, wrong, max_total)
        # Drill badge: green when nonzero (drilled), dim when zero (never
        # drilled) so "未刷过" reads as visually secondary, not as loud as
        # the default text color used for the rest of the line.
        if drills > 0:
            drill_badge = f"{DRILL_COLOR}[×{drills}]{RESET_COLOR}"
        else:
            drill_badge = f"{DIM_COLOR}[×{drills}]{RESET_COLOR}"
        print("  ch{:<3} {:>3}题 / {:>2}错 {:>5.1%}  {}  {}".format(
            str(chapter), total, wrong, ratio, bar, drill_badge))
    print()
    print("  Enter/Esc 返回菜单，q 退出")


def _stats_bar(total, wrong, max_total, width=32):
    from .tui.render import AI_COLOR, DIM_COLOR, RESET_COLOR, STAT_TOTAL_COLOR
    if max_total <= 0 or total <= 0:
        return DIM_COLOR + "·" * width + RESET_COLOR
    total_len = max(1, round(total / max_total * width))
    wrong_len = round(wrong / max_total * width)
    if wrong > 0:
        wrong_len = max(1, wrong_len)
    wrong_len = min(wrong_len, total_len)
    rest_len = max(0, total_len - wrong_len)
    padding_len = max(0, width - total_len)
    parts = []
    if wrong_len:
        parts.append(AI_COLOR + "█" * wrong_len + RESET_COLOR)
    if rest_len:
        parts.append(STAT_TOTAL_COLOR + "█" * rest_len + RESET_COLOR)
    if padding_len:
        parts.append(DIM_COLOR + "·" * padding_len + RESET_COLOR)
    return "".join(parts)


# ---- entry menu ----

def deck_picker(config):
    """Phase 1 of the interactive entry: pick a deck.

    Full-screen table styled like `flip list`, with live search (typing
    printable chars filters by slug+name substring) and ↑/↓ navigation.
    The cursor starts on config.default_deck when it still exists. Enter
    confirms and persists the choice as the new default; Esc/q quits.

    Returns a Deck or None.
    """
    if not sys.stdin.isatty():
        print("flip: 交互菜单需要 tty。使用 `flip deck <slug> train` 等子命令。")
        return None
    from .deck import list_decks, load_deck
    all_slugs = list_decks(config.decks_dir)
    if not all_slugs:
        print("还没有任何 deck。先用 `flip import <slug> <tiku.json>` 注册一个。")
        return None

    # Precompute the full table once; search filters these rows in place.
    all_rows = store.deck_rows(config)
    # slug -> display name (lowercased) for search.
    name_index = {row[0]: (row[1] or "").lower() for row in all_rows}

    query = ""
    index = 0
    if config.default_deck:
        slugs_only = [r[0] for r in all_rows]
        if config.default_deck in slugs_only:
            index = slugs_only.index(config.default_deck)

    old_settings = save_tty()
    try:
        enter_alt_screen()
        enter_cbreak()
        while True:
            rows = _filter_rows(all_rows, name_index, query)
            _render_deck_picker(rows, index, query, config.default_deck)
            key = read_key()
            if key == '\x03':
                raise KeyboardInterrupt
            if key == '\x1b':
                if query:
                    query = ""
                    index = min(index, max(len(all_rows) - 1, 0))
                else:
                    return None
                continue
            if key in {'q', 'Q'} and not query:
                return None
            if key == '\x1b[A':
                if rows:
                    index = (index - 1) % len(rows)
                continue
            if key == '\x1b[B':
                if rows:
                    index = (index + 1) % len(rows)
                continue
            if key in {'\x7f', '\b'}:
                query = query[:-1]
                index = 0
                continue
            if len(key) == 1 and 0x20 <= ord(key) < 0x7f:
                query += key
                index = 0
                continue
            if key in {'\r', '\n'} and rows:
                slug = rows[index][0]
                try:
                    deck = load_deck(config.decks_dir / slug)
                except Exception:
                    continue
                from .config import save_default_deck
                try:
                    save_default_deck(config, slug)
                except Exception:
                    pass
                return deck
    finally:
        restore_tty(old_settings)
        exit_alt_screen()


def _filter_rows(all_rows, name_index, query):
    """Filter table rows by slug/name substring (case-insensitive)."""
    if not query:
        return list(all_rows)
    q = query.lower()
    return [r for r in all_rows if q in r[0].lower() or q in name_index.get(r[0], "")]


def _render_deck_picker(rows, index, query, default_deck):
    from .tui.render import DIM_COLOR, RESET_COLOR, SELECTED_COLOR
    clear_screen()
    print("@ flip — 选择 deck")
    # Search bar at the very top: shows the live query, or a dim placeholder
    # hint when empty (so the affordance for typing is always visible).
    if query:
        search_line = "  " + DIM_COLOR + "search:" + RESET_COLOR + " " + query
    else:
        search_line = "  " + DIM_COLOR + "search: 输入字符过滤(按 slug 或 deck 名)" + RESET_COLOR
    print(search_line)
    print()
    if rows:
        widths = store.table_widths(rows)
        # Header (same left-aligned, CJK-aware style as `flip list`).
        header_cells = [store._pad(h, widths[i]) for i, h in enumerate(store.DECK_TABLE_HEADERS)]
        print("  " + "  ".join(header_cells))
        for i, row in enumerate(rows):
            cells = [store._pad(c, widths[j]) for j, c in enumerate(row)]
            mark = " *" if row[0] == default_deck else "  "
            line = mark + "  ".join(cells)
            print(SELECTED_COLOR + line + RESET_COLOR if i == index
                  else DIM_COLOR + line + RESET_COLOR)
    else:
        print("  " + DIM_COLOR + "(无匹配 deck)" + RESET_COLOR)
    print()
    print("  " + DIM_COLOR + "↑/↓ 选择,Enter 进入,Esc 清空搜索/q 退出  (* = 上次使用)" + RESET_COLOR)


def _table_widths(rows):
    # Kept as a thin shim for backwards compat; new code should use store.table_widths.
    return store.table_widths(rows)


def entry_menu(config, deck, *, resume=None):
    """Phase 2 of the interactive entry: pick a mode + filters for a deck.

    Three modes selectable via ↑/↓ + Enter:
      Train   — drill the full tiku bank (or browse it when Ans mode is on)
      Review  — drill the wrong index (or browse it when Ans mode is on)
      List    — global learning stats for this deck
    Keys 1-4 toggle the question filters (mark/note/ai) and Ans mode.

    Returns (mode, selector, ans_mode, filters) where mode is "train" or
    "review", or None on cancel (Esc/q returns to the deck picker).

    `resume`, when given, is a (mode_index, ans_mode, filters) tuple that
    seeds the mode screen and immediately drops into the chapter picker with
    chapters cleared — used when an Esc mid-question bounces back here. The
    mode/ans/filters are preserved; only the chapter selection is reset.
    """
    if not sys.stdin.isatty():
        print("flip: 交互菜单需要 tty。使用 `flip deck <slug> train` 等子命令。")
        return None

    modes = [
        ("Train", "章节题库训练"),
        ("Review", "错题索引复习"),
        ("List", "全局学习统计"),
    ]
    if resume is not None:
        mode_index, ans_mode, filters = resume
    else:
        mode_index = 0
        ans_mode = False
        filters = []
    selector = None
    old_settings = save_tty()
    try:
        enter_alt_screen()
        enter_cbreak()
        # On resume, skip the mode screen and jump straight into the chapter
        # picker (chapters cleared). The mode/ans/filters above are preserved.
        confirmed = None
        next_selector = None
        if resume is not None:
            name = modes[mode_index][0]
            if name != "List":
                confirmed, next_selector = _edit_selector(
                    None,
                    name + " (" + ("浏览" if ans_mode else "计分") + ")",
                    deck=deck, config=config,
                )
                if confirmed:
                    selector = next_selector
        while True:
            _render_entry_menu(deck, modes, mode_index, selector, ans_mode, filters)
            key = read_key()
            if key in {'q', 'Q', '\x1b'}:
                return None
            if key == '\x03':
                raise KeyboardInterrupt
            if key == '\x1b[A':
                mode_index = (mode_index - 1) % len(modes)
                continue
            if key == '\x1b[B':
                mode_index = (mode_index + 1) % len(modes)
                continue
            if key == '1':
                filters = _toggle_filter(filters, "mark")
                continue
            if key == '2':
                filters = _toggle_filter(filters, "note")
                continue
            if key == '3':
                filters = _toggle_filter(filters, "ai")
                continue
            if key == '4':
                ans_mode = not ans_mode
                continue
            if key == '5':
                _cycle_max_display_options(deck)
                continue
            if key in {'\r', '\n'}:
                name = modes[mode_index][0]
                if name == "List":
                    _run_stats_loop(deck, config)
                    continue
                confirmed, next_selector = _edit_selector(
                    selector,
                    name + " (" + ("浏览" if ans_mode else "计分") + ")",
                    deck=deck, config=config,
                )
                if not confirmed:
                    continue
                selector = next_selector
                return ("train" if name == "Train" else "review"), selector, ans_mode, filters
    finally:
        restore_tty(old_settings)
        exit_alt_screen()


def _render_entry_menu(deck, modes, mode_index, selector, ans_mode, filters):
    from .tui.render import (
        DIM_COLOR, RESET_COLOR, SELECTED_COLOR,
    )
    from . import store
    filter_set = set(filters)
    # Pad the mode name to the widest one so the descriptions line up.
    name_w = max(store.display_width(n) for n, _ in modes)
    clear_screen()
    print("@ flip —", deck.name, f"({deck.slug})")
    print()
    for i, (name, desc) in enumerate(modes):
        prefix = ">" if i == mode_index else " "
        name_field = store._pad(name, name_w)
        line = prefix + " " + name_field + "   " + desc
        print(SELECTED_COLOR + line + RESET_COLOR if i == mode_index
              else DIM_COLOR + line + RESET_COLOR)
    print()
    print("  " + DIM_COLOR + "↑/↓ 选择模式,Enter 进入,1-5 切换配置,Esc/q 返回选 deck" + RESET_COLOR)
    print("  1", _opt_state("mark" in filter_set), "包含已标记")
    print("  2", _opt_state("note" in filter_set), "包含笔记")
    print("  3", _opt_state("ai" in filter_set), "包含 Agent Said")
    print("  4", _opt_state(ans_mode), "Ans 模式(直接显示答案,不计分)")
    print("  5", _opt_state(True), f"最多显示选项: {deck.max_display_options}")


def _cycle_max_display_options(deck):
    max_options = max(DEFAULT_MAX_DISPLAY_OPTIONS, len(deck.answer_alphabet))
    current = deck.max_display_options
    next_value = current + 1 if current < max_options else DEFAULT_MAX_DISPLAY_OPTIONS
    save_max_display_options(deck, next_value)


def _opt_state(on):
    from .tui.render import ACTIVE_COLOR, DIM_COLOR, RESET_COLOR
    if on:
        return ACTIVE_COLOR + "[ON ]" + RESET_COLOR
    return DIM_COLOR + "[OFF]" + RESET_COLOR


def _toggle_filter(filters, name):
    if name in filters:
        return [f for f in filters if f != name]
    return filters + [name]


def _edit_selector(selector, mode_name, deck=None, config=None):
    """Chapter picker with a live preview and dual input modes.

    Renders one row per chapter showing its title (if `_chapter_titles`
    declares one), question/wrong counts, and a white+yellow bar. The chapter
    set can be built two ways that stay in sync:

      - typing: digits / `-` / `,` edit a text buffer; on Enter it's parsed by
        `chapter_selector` (so `5,3-4` -> {3,4,5}). Backspace edits the buffer.
      - arrows + space: ↑/↓ moves the cursor; space toggles a chapter in/out
        of the selection. The text buffer is regenerated from the set so the
        two modes never disagree.

    Returns (confirmed, selector) where selector is the canonical text form
    (e.g. "3-5") or None for "all chapters".
    """
    from .tui.render import (
        DIM_COLOR, RESET_COLOR, SELECTED_COLOR, STAT_TOTAL_COLOR, AI_COLOR,
    )

    # Gather chapter metadata once: ordered list of chapter strings, per-chapter
    # question/wrong counts, max for bar scaling, and optional titles.
    chapters = []
    titles = {}
    per_chapter = {}
    wrong_per_chapter = {}
    drills_per_chapter = {}
    max_total = 0
    if deck is not None:
        data = store.load_tiku(deck) or {}
        if isinstance(data, dict):
            raw_titles = data.get("_chapter_titles", {})
            if isinstance(raw_titles, dict):
                titles = {str(k): str(v) for k, v in raw_titles.items()}
        stats = engine.stats_snapshot(deck) if config is not None else None
        if stats:
            per_chapter = stats["per_chapter"]
            wrong_per_chapter = stats["wrong_per_chapter"]
            max_total = max(per_chapter.values(), default=0)
        # Drills filtered by THIS entry mode (mode_name is "Train"/"Review").
        # Stats keeps a merged count; the picker shows the context-specific
        # count so the user sees "how many times I drilled this chapter in the
        # mode I'm about to enter" rather than the merged total.
        drills_per_chapter = _drills_per_chapter_for_mode(deck, mode_name)
        chapters = sorted(per_chapter.keys(), key=store._chapter_sort_key) if per_chapter else []

    cursor = 0
    # `selected` is the source of truth; `buffer` is its text projection.
    # Seeding both from the incoming selector keeps a re-entered picker stable.
    numeric_chapters = [int(c) for c in chapters if str(c).isdigit()]
    max_n = max(numeric_chapters) if numeric_chapters else 0
    selected = set()
    if selector:
        try:
            resolved = engine.chapter_selector(selector, max_n)
            selected = {c for c in chapters if str(c).isdigit() and int(c) in resolved}
        except ValueError:
            pass
    buffer = _selector_text_from_set(selected)

    while True:
        _render_chapter_picker(mode_name, chapters, titles, per_chapter,
                               wrong_per_chapter, drills_per_chapter,
                               max_total, cursor, selected, buffer)
        key = read_key()
        if key == '\x03':
            raise KeyboardInterrupt
        if key == '\x1b':
            return False, selector
        if key in {'\r', '\n'}:
            return True, (buffer.strip() or None)
        if key in {'\x7f', '\b'}:
            buffer = buffer[:-1]
            # Re-parse the truncated buffer back into the set.
            selected = _selector_set_from_text(buffer, chapters, max_n)
            continue
        if key == ' ' and chapters:
            # Toggle the chapter under the cursor; regenerate the buffer.
            ch = chapters[cursor]
            if ch in selected:
                selected.discard(ch)
            else:
                selected.add(ch)
            buffer = _selector_text_from_set(selected)
            continue
        if key == '\x1b[A' and chapters:
            cursor = (cursor - 1) % len(chapters)
            continue
        if key == '\x1b[B' and chapters:
            cursor = (cursor + 1) % len(chapters)
            continue
        if len(key) == 1 and (key.isdigit() or key in {'-', ','}):
            buffer += key
            selected = _selector_set_from_text(buffer, chapters, max_n)
            continue


def _selector_text_from_set(selected):
    """Canonical text form of a chapter set: sorted, ranges collapsed.

    e.g. {3,4,5,9} -> "3-5,9". Empty set -> "" (means all chapters on Enter).
    Accepts int or str chapter ids (treats them as integers for ordering).
    """
    nums = sorted(int(n) for n in selected if str(n).isdigit())
    if not nums:
        return ""
    parts = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


def _selector_set_from_text(buffer, chapters, max_n):
    """Parse a buffer like '5,3-4' into a set of chapter strings present in `chapters`."""
    text = buffer.strip()
    if not text:
        return set()
    try:
        nums = engine.chapter_selector(text, max_n) if max_n else set()
    except ValueError:
        return set()
    return {c for c in chapters if str(c).isdigit() and int(c) in nums}


def _render_chapter_picker(mode_name, chapters, titles, per_chapter,
                           wrong_per_chapter, drills_per_chapter, max_total,
                           cursor, selected, buffer):
    from .tui.render import (
        DIM_COLOR, DRILL_COLOR, RESET_COLOR, SELECTED_COLOR, STAT_TOTAL_COLOR, AI_COLOR,
    )
    clear_screen()
    print("@", mode_name, "— 章节选择")
    print()
    print("  " + DIM_COLOR + "输入章节或范围(如 5、3-5、5,3-4);清空=全部" + RESET_COLOR)
    # Search/input line: show the live buffer; dim hint when empty.
    if buffer:
        print("  > " + buffer)
    else:
        print("  > " + DIM_COLOR + "(空=全部章节)" + RESET_COLOR)
    print()
    if not chapters:
        print("  " + DIM_COLOR + "(无章节数据)" + RESET_COLOR)
    else:
        # The per-line wrapper color (dim vs highlighted) is applied to the
        # whole line. The drill badge embeds its own green-when-nonzero escape;
        # to keep the wrapper alive after RESET inside the badge, we re-apply
        # the wrapper color right after the badge.
        for i, ch in enumerate(chapters):
            total = per_chapter.get(ch, 0)
            wrong = wrong_per_chapter.get(ch, 0)
            drills = drills_per_chapter.get(ch, 0)
            bar = _stats_bar(total, wrong, max_total, width=20)
            mark = "[x]" if ch in selected else "[ ]"
            title = titles.get(ch, "")
            title_field = ("  " + title) if title else ""
            line_color = SELECTED_COLOR if i == cursor else DIM_COLOR
            if drills > 0:
                badge = f"{DRILL_COLOR}[×{drills}]{RESET_COLOR}{line_color}"
            else:
                badge = f"{DIM_COLOR}[×{drills}]{RESET_COLOR}{line_color}"
            line = f"  {mark} ch{str(ch):<3} {bar}  {total:>3}题/{wrong:>2}错{title_field}  {badge}"
            print(line_color + line + RESET_COLOR)
    print()
    print("  " + DIM_COLOR +
          "↑/↓ 移动,空格 切换选中,数字/范围 直接输入,Enter 开始,Esc 返回" +
          RESET_COLOR)


def _drills_per_chapter_for_mode(deck, mode_name):
    """Aggregate drill counts per chapter, filtered to the given entry mode.

    `mode_name` is the picker's display label ("Train" / "Review"). We map it
    to the history record's `mode` field ("train" / "review") and count only
    matching records. This is what makes the picker show context-specific
    counts: entering via Train shows train drills, via Review shows review
    drills, instead of the merged total that stats_snapshot reports.
    """
    if deck is None:
        return {}
    label = (mode_name or "").lower()
    if "review" in label:
        want_mode = "review"
    elif "train" in label:
        want_mode = "train"
    else:
        # Unknown mode label: show merged (defensive — shouldn't normally happen).
        want_mode = None
    counts = {}
    for record in store.load_history(deck):
        if want_mode is not None and record.get("mode") != want_mode:
            continue
        for ch in record.get("chapters", []):
            ch = str(ch)
            counts[ch] = counts.get(ch, 0) + 1
    return counts


def _run_stats_loop(deck, config):
    # Non-tty: render once and return (don't spin on a dead stdin).
    if not sys.stdin.isatty():
        render_stats(deck, config)
        return None
    while True:
        render_stats(deck, config)
        key = read_key()
        if key in {'q', 'Q'}:
            return None
        if key in {'\r', '\n', '\x1b'}:
            break
    return None


def run_train(deck, config, selector, source="tiku", ans_mode=False, filters=None):
    """Top-level runner used by the CLI after a deck/mode is chosen.

    `source` selects the question pool — "tiku" (full bank) or "wrong"
    (the error index). `ans_mode` selects the loop — True runs
    `review_questions` (browse, answers shown, no scoring); False runs
    `epoch` (answer, grade, append wrongs). The two are independent, so a
    wrong-source training pass (ans_mode=False) drills exactly the questions
    you previously got wrong.

    Returns BACK_TO_SELECTOR if the learner pressed Esc mid-question (the
    caller should bounce them back to the chapter picker, clearing chapters
    but keeping mode/ans/filters); otherwise 0 after printing the report.
    """
    filters = filters or []
    selected = engine.pick_questions(deck, config, selector=selector, shuffle=True,
                                     filters=filters, source=source)
    # `mode` follows the ENTRY mode (i.e. the question source), NOT the ans
    # toggle. ans only changes whether we score (epoch) or browse
    # (review_questions); it does not relabel a Train session as review. So
    # Train+ans and Review+ans record under their respective entry modes.
    mode_label = "train" if source == "tiku" else "review"
    if ans_mode:
        outcome = review_questions(deck, config, selected)
        if outcome == BACK_TO_SELECTOR:
            return BACK_TO_SELECTOR
        # Browse mode doesn't score, so incorrect=0; still counts as a drill
        # so the user sees the chapter was visited.
        _record_drill(deck, selected, total=len(selected.questions),
                      incorrect=0, mode=mode_label)
        return 0

    count, incorrects, status = epoch(deck, config, selected)
    if status == BACK_TO_SELECTOR:
        return BACK_TO_SELECTOR

    alphabet = deck.answer_alphabet
    print("============== Report ==============")
    label = selector if selector is not None else "全部"
    print(f"- Deck: {deck.name}, 范围 {label}, 源 {source}")
    print(f"- Epoch Finished, \033[1;31m{len(incorrects)} / {count - 1}\033[0m incorrects.")
    if selected.input_is_index:
        print("- Source index unchanged (review-on-wrong writes no new file).")
    else:
        out = store.build_result_filename(selected.questions, deck)
        disp = store.relative_to_cwd(out)
        print(f"- Next epoch: \033[1;33m{disp}\033[0m")
        store.write_json(out, incorrects)
    print("====================================")
    # A completed training run (not BACK_TO_SELECTOR) counts as a drill.
    _record_drill(deck, selected, total=count - 1, incorrect=len(incorrects), mode=mode_label)
    return 0


def _record_drill(deck, selected, *, total, incorrect, mode):
    """Append one drill record to the deck's history.

    Centralizes record construction so train and review share the same shape.
    `chapters` is the deduped sorted set of chapters this run covered, so
    stats_snapshot can +1 each of them. Called only on completed runs —
    BACK_TO_SELECTOR exits before reaching here, so abandoned drills aren't
    counted (a half-finished session isn't a real drill).
    """
    import datetime
    chapters = sorted({str(ch) for ch, _ in selected.questions})
    store.append_history(deck, {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "chapters": chapters,
        "total": total,
        "incorrect": incorrect,
        "mode": mode,
    })


def run_translate(deck, config, selector=None, force=False):
    if not config.translation_enabled:
        print(f"翻译未启用：source_lang == target_lang ({config.source_lang} == {config.target_lang})。")
        print("编辑 ~/.local/share/flip/config.toml 设置不同的语言对后重试。")
        return 1
    data = store.load_tiku(deck)
    if data is None:
        print(f"tiku.json 不存在：{deck.tiku_path}")
        return 1
    records = engine.records_from_data(data, selector)
    print(f"翻译 {len(records)} 题 ({config.source_lang} → {config.target_lang})…")

    def progress(done, total):
        print(f"\r{done} / {total}", end="", flush=True)

    updated, failures = translate.translate_question_records(
        records, config.source_lang, config.target_lang,
        force=force, progress_callback=progress,
    )
    store.save_tiku(deck, data)
    print()
    print(f"已更新 {updated} 题。")
    if failures:
        print(f"{len(failures)} 题失败，重跑同命令可补译缺失字段。")
        for ch, topic, err in failures[:10]:
            print(f"  - ch{ch}: {topic} ({err})")
    return 1 if failures else 0
