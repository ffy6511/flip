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
from .deck import Deck
from .tui import (
    read_key, save_tty, restore_tty, enter_cbreak,
    clear_screen, enter_alt_screen, exit_alt_screen,
    has_translation, has_agent_said, has_user_note,
    translated_question, default_detail_view, normalize_detail_view,
    render_question, render_result, render_review_question,
    render_ai_waiting, render_ai_prompt_input, render_note_input,
)


def _options(q):
    """All options visible — no hidden-E suppression in flip."""
    return list(q.get("options", []))


def _answer_from_selected(options, selected):
    answer = []
    for index in sorted(selected):
        answer.append(options[index][0].upper())
    return "".join(answer)


# ---- small sub-prompts (ai extra / note) ----

def _prompt_ai_extra(deck, chapter, q, render_current):
    buffer = ""
    options = _options(q)
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
    options = _options(q)
    alphabet = deck.answer_alphabet
    translation_enabled = config.translation_enabled
    detail_view = normalize_detail_view(q, detail_view)
    cursor = 0
    selected = set()
    warning = ""
    confirm_remove = False
    translation = translated_question(q) if (translation_enabled and show_translation) else None
    if translation_enabled and show_translation and translation is None:
        show_translation = False
        warning = f"题库缺少 {config.target_lang} 字段，请先运行：flip deck {deck.slug} translate"

    marked = engine.is_marked(deck, chapter, q)
    old_settings = save_tty()
    model_name = deck.explain.resolve_model()
    try:
        enter_cbreak()
        while True:
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
                    translation = translated_question(q)
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
                render_question(
                    count, total, chapter, q, options, cursor, selected,
                    warning=warning, show_translation=show_translation, translation=translation,
                    detail_view=detail_view, marked=marked, model_name=model_name,
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
    finally:
        restore_tty(old_settings)


# ---- result loop (post-submit) ----

def prompt_result(deck, config, count, total, chapter, q, selected_answer, is_correct, *,
                  show_translation=False, detail_view=None,
                  previous_available=False, removable=False):
    options = _options(q)
    translation_enabled = config.translation_enabled
    detail_view = normalize_detail_view(q, detail_view)
    translation = translated_question(q) if (translation_enabled and show_translation) else None
    if translation_enabled and show_translation and translation is None:
        show_translation = False
        warning = f"题库缺少 {config.target_lang} 字段，请先运行：flip deck {deck.slug} translate"
    else:
        warning = ""
    marked = engine.is_marked(deck, chapter, q)
    confirm_remove = False
    model_name = deck.explain.resolve_model()
    old_settings = save_tty()
    try:
        enter_cbreak()
        while True:
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
                    translation = translated_question(q)
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
                render_result(
                    count, total, chapter, q, options, selected_answer, is_correct,
                    warning=warning, show_translation=show_translation, translation=translation,
                    detail_view=detail_view, marked=marked, model_name=model_name,
                    translation_enabled=translation_enabled,
                    ai_prompt_buffer=ai_prompt_buffer, ai_waiting=ai_waiting, note_buffer=note_buffer,
                )

            dv, w, action = _handle_detail_keys(deck, config, chapter, q, detail_view, key, render_current)
            detail_view = dv
            if w:
                warning = w
            if action and action[0] == 'quit':
                return ('quit', show_translation, detail_view)
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
    question) or 'quit' (abort the whole epoch). ←/→ at the ends either clamp
    with a warning or, at the right edge, also return 'continue' to resume.
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
            options = _options(q)
            detail_view = normalize_detail_view(q, detail_view)
            translation = translated_question(q) if (translation_enabled and show_translation) else None
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
                    translation = translated_question(q)
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
            detail_view = default_detail_view(q)
            while True:
                inpu, show_translation, detail_view = prompt_answer(
                    deck, config, count, len(questions), chapter, q,
                    show_translation=show_translation, detail_view=detail_view,
                    previous_available=bool(history),
                    removable=selected_set.input_is_index,
                    selected_set=selected_set,
                )
                if inpu == 'quit':
                    return count, incorrect
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
                        return count, incorrect
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
                    return count, incorrect
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
                        return count, incorrect
                    continue
                break

            clear_screen()
            count += 1
        return count, incorrect
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
            options = _options(q)
            detail_view = normalize_detail_view(q, detail_view)
            translation = translated_question(q) if (translation_enabled and show_translation) else None
            marked = engine.is_marked(deck, chapter, q)
            render_review_question(
                index, len(questions), chapter, q,
                show_translation=show_translation, translation=translation,
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
                translation = translated_question(q)
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
                    show_translation=show_translation, translation=translation,
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
        AI_COLOR, DIM_COLOR, RESET_COLOR, STAT_TOTAL_COLOR,
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
        ratio = wrong / total if total else 0
        bar = _stats_bar(total, wrong, max_total)
        print("  ch{:<3} {:>3}题 / {:>2}错 {:>5.1%}  {}".format(str(chapter), total, wrong, ratio, bar))
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
    if query:
        print("  " + DIM_COLOR + "搜索: " + RESET_COLOR + query + "  " +
              DIM_COLOR + "↑/↓ 选择,Enter 进入,Esc 清空搜索,q 退出" + RESET_COLOR)
    else:
        print("  " + DIM_COLOR + "输入字符搜索,↑/↓ 选择,Enter 进入,Esc/q 退出  (* = 上次使用)" + RESET_COLOR)


def _table_widths(rows):
    # Kept as a thin shim for backwards compat; new code should use store.table_widths.
    return store.table_widths(rows)


def entry_menu(config, deck):
    """Phase 2 of the interactive entry: pick mode + filters for a chosen deck.

    The deck is already chosen (by deck_picker); this screen configures the
    1-4 question filters, the Review toggle, and the chapter selector, then
    returns (deck, review_mode, selector, filters) or None on cancel.
    """
    if not sys.stdin.isatty():
        print("flip: 交互菜单需要 tty。使用 `flip deck <slug> train` 等子命令。")
        return None

    selector = None
    review_mode = False
    filters = []
    old_settings = save_tty()
    try:
        enter_alt_screen()
        enter_cbreak()
        while True:
            _render_entry_menu(deck, selector, review_mode, filters)
            key = read_key()
            if key in {'q', 'Q', '\x1b'}:
                return None
            if key == '\x03':
                raise KeyboardInterrupt
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
                review_mode = not review_mode
                continue
            if key in {'\r', '\n'}:
                confirmed, next_selector = _edit_selector(
                    selector, "训练" if not review_mode else "复习"
                )
                if not confirmed:
                    continue
                selector = next_selector
                return deck, review_mode, selector, filters
    finally:
        restore_tty(old_settings)
        exit_alt_screen()


def _render_entry_menu(deck, selector, review_mode, filters):
    from .tui.render import (
        DIM_COLOR, RESET_COLOR, SELECTED_COLOR,
    )
    filter_set = set(filters)
    clear_screen()
    print("@ flip —", deck.name, f"({deck.slug})")
    print()
    print("  " + SELECTED_COLOR + "> 训练" + RESET_COLOR +
          ("  / 复习(Review 已开)" if review_mode else ""))
    print()
    print("  " + DIM_COLOR + "Enter 开始,1-4 切换配置,Esc/q 返回选 deck" + RESET_COLOR)
    print("  1", _opt_state("mark" in filter_set), "仅已标记")
    print("  2", _opt_state("note" in filter_set), "仅有笔记")
    print("  3", _opt_state("ai" in filter_set), "仅有 Agent Said")
    print("  4", _opt_state(review_mode), "Review 模式(从错题索引练习)")


def _opt_state(on):
    from .tui.render import ACTIVE_COLOR, DIM_COLOR, RESET_COLOR
    if on:
        return ACTIVE_COLOR + "[ON ]" + RESET_COLOR
    return DIM_COLOR + "[OFF]" + RESET_COLOR


def _toggle_filter(filters, name):
    if name in filters:
        return [f for f in filters if f != name]
    return filters + [name]


def _edit_selector(selector, mode_name):
    from .tui.render import DIM_COLOR
    buffer = selector or ""
    while True:
        clear_screen()
        print("@", mode_name, "— 章节选择")
        print()
        print("  输入章节或范围，例如 5、5-10、-10。清空表示全部。")
        print("  Enter 开始，Esc 返回。")
        print()
        print("  > " + buffer)
        key = read_key()
        if key == '\x03':
            raise KeyboardInterrupt
        if key == '\x1b':
            return False, selector
        if key in {'\r', '\n'}:
            return True, (buffer.strip() or None)
        if key in {'\x7f', '\b'}:
            buffer = buffer[:-1]
            continue
        if len(key) == 1 and (key.isdigit() or key in {'-', ','}):
            buffer += key


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


def run_train(deck, config, selector, review_mode, filters):
    """Top-level runner used by the CLI after a deck/mode is chosen."""
    source = "wrong" if review_mode else "tiku"
    selected = engine.pick_questions(deck, config, selector=selector, shuffle=True,
                                     filters=filters, source=source)
    if review_mode:
        review_questions(deck, config, selected)
        return 0

    count, incorrects = epoch(deck, config, selected)
    alphabet = deck.answer_alphabet

    print("============== Report ==============")
    label = selector if selector is not None else "全部"
    print(f"- Deck: {deck.name}, 范围 {label}")
    print(f"- Epoch Finished, \033[1;31m{len(incorrects)} / {count - 1}\033[0m incorrects.")
    if selected.input_is_index:
        print("- Source index unchanged (review-on-wrong writes no new file).")
    else:
        out = store.build_result_filename(selected.questions, deck)
        disp = store.relative_to_cwd(out)
        print(f"- Next epoch: \033[1;33m{disp}\033[0m")
        store.write_json(out, incorrects)
    print("====================================")
    return 0


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
