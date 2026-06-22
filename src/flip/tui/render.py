"""Terminal rendering primitives.

Extracted from se_regressor.py with behavior preserved. This module only draws
to stdout — it does not read keys or mutate question state. All data it needs
(question, options, translation, flags) is passed in as arguments.

The one behavioral knob added vs. the original: a `translation_enabled` flag on
the key-hint footer, so decks with translation off (source_lang == target_lang)
do not advertise the `t` key.
"""

import sys
import shutil
import unicodedata

TRANSLATION_COLOR = "\033[36m"
MARK_COLOR = "\033[35m"
CORRECT_COLOR = "\033[1;32m"
AI_COLOR = "\033[33m"
DIM_COLOR = "\033[2m"
RESET_COLOR = "\033[0m"
ACTIVE_COLOR = "\033[1;32m"
SELECTED_COLOR = "\033[1;33m"
STAT_TOTAL_COLOR = "\033[37m"
WRONG_COLOR = "\033[1;31m"
# Non-bold green for the drill-count badge (distinct from CORRECT_COLOR's
# bold green used for correct-answer checkmarks, so they don't visually clash).
DRILL_COLOR = "\033[32m"
LOWER_BLOCK_INDENT = " "
MIN_WRAP_WIDTH = 20


def terminal_width():
    return max(MIN_WRAP_WIDTH, shutil.get_terminal_size((80, 24)).columns)


def display_width(text):
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        for ch in str(text)
    )


def wrap_text(text, width):
    width = max(1, int(width))
    words = str(text).split()
    if len(words) > 1:
        lines = []
        line = ""
        for word in words:
            candidate = word if not line else line + " " + word
            if display_width(candidate) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            if display_width(word) <= width:
                line = word
            else:
                lines.extend(_wrap_unspaced(word, width)[:-1])
                line = _wrap_unspaced(word, width)[-1]
        if line:
            lines.append(line)
        return lines or [""]
    return _wrap_unspaced(str(text), width)


def _wrap_unspaced(text, width):
    lines = []
    line = ""
    used = 0
    for ch in str(text):
        ch_width = display_width(ch)
        if line and used + ch_width > width:
            lines.append(line)
            line = ch
            used = ch_width
        else:
            line += ch
            used += ch_width
    return lines + ([line] if line else [""])


def print_wrapped(prefix, text, *, continuation_prefix=None, color=""):
    continuation_prefix = prefix if continuation_prefix is None else continuation_prefix
    width = terminal_width() - display_width(prefix)
    lines = wrap_text(text, width)
    for i, line in enumerate(lines):
        line_prefix = prefix if i == 0 else continuation_prefix
        if color:
            print(line_prefix + color + line + RESET_COLOR)
        else:
            print(line_prefix + line)


# ---- screen control ----

def clear_screen():
    print("\033[H\033[J", end="", flush=True)


def enter_alt_screen():
    print("\033[?1049h\033[?25l", end="", flush=True)
    clear_screen()


def exit_alt_screen():
    print("\033[?25h\033[?1049l", end="", flush=True)


# ---- option helpers (pure) ----

def split_option(choice):
    """'A. foo' -> ('A. ', 'foo'); 'foo' -> ('', 'foo')."""
    if len(choice) >= 3 and choice[1] == '.' and choice[2] == ' ':
        return choice[:3], choice[3:]
    return "", choice


def option_label(choice):
    label, _ = split_option(choice)
    return label[:1].upper()


def question_topic(q):
    """Return the display topic, adding a badge for multi-select questions."""
    topic = str(q.get("topic", ""))
    answer = str(q.get("answer", "")).strip()
    if len(answer) > 1:
        return topic + " [多选]"
    return topic


# ---- translation / explanation / note presence (pure) ----

def has_translation(q):
    zh = q.get("zh")
    if not isinstance(zh, dict):
        return False
    if not zh.get("topic"):
        return False
    options = zh.get("options")
    return isinstance(options, list) and len(options) == len(q.get("options", [])) and all(options)


def has_agent_said(q):
    return bool(str(q.get("ai_explanation", "")).strip())


def has_user_note(q):
    return bool(str(q.get("user_note", "")).strip())


def translated_question(q):
    """Return {'topic','options'} aligned to q['options'] by label, or None."""
    if not has_translation(q):
        return None
    zh = q["zh"]
    option_by_label = {}
    for choice in zh["options"]:
        label, _ = split_option(choice)
        option_by_label[label.strip()] = choice
    option_translations = []
    for choice in q.get("options", []):
        label, _ = split_option(choice)
        option_translations.append(option_by_label.get(label.strip(), ""))
    return {"topic": zh["topic"], "options": option_translations}


# ---- detail view (ai / note) helpers ----

def default_detail_view(q):
    if has_user_note(q):
        return "note"
    if has_agent_said(q):
        return "ai"
    return None


def normalize_detail_view(q, detail_view):
    if detail_view == "note" and has_user_note(q):
        return "note"
    if detail_view == "ai" and has_agent_said(q):
        return "ai"
    return None


def detail_key_hints(q):
    agent_state = "有" if has_agent_said(q) else "无"
    note_state = "有" if has_user_note(q) else "无"
    return "x Agent[" + agent_state + "], n 笔记[" + note_state + "], e 编辑当前"


# ---- block printers ----

def print_translation_block(translation):
    if not translation:
        return
    print()
    print(LOWER_BLOCK_INDENT + TRANSLATION_COLOR + "─" * (terminal_width() - 2) + RESET_COLOR)
    print_wrapped(LOWER_BLOCK_INDENT, translation["topic"], color=TRANSLATION_COLOR)
    print()
    for choice in translation["options"]:
        print_wrapped(LOWER_BLOCK_INDENT, choice, color=TRANSLATION_COLOR)


def print_ai_explanation_block(q):
    explanation = q.get("ai_explanation")
    if not explanation:
        return
    print()
    print(LOWER_BLOCK_INDENT + AI_COLOR + "─" * (terminal_width() - 2) + RESET_COLOR)
    user_prompt = q.get("ai_explanation_user_prompt")
    if user_prompt:
        print_wrapped(LOWER_BLOCK_INDENT + "Q: ", " ".join(str(user_prompt).split()))
    print(LOWER_BLOCK_INDENT + AI_COLOR + "Agent Said" + RESET_COLOR)
    print_wrapped(LOWER_BLOCK_INDENT, explanation)


def print_user_note_block(q):
    note = str(q.get("user_note", "")).strip()
    if not note:
        return
    print()
    print(LOWER_BLOCK_INDENT + AI_COLOR + "─" * (terminal_width() - 2) + RESET_COLOR)
    print(LOWER_BLOCK_INDENT + AI_COLOR + "User Note" + RESET_COLOR)
    print_wrapped(LOWER_BLOCK_INDENT, note)


def print_detail_view(q, detail_view):
    if detail_view == "ai":
        print_ai_explanation_block(q)
    elif detail_view == "note":
        print_user_note_block(q)


def print_key_hint_footer(text):
    print()
    print_wrapped(LOWER_BLOCK_INDENT, text, color=DIM_COLOR)


def print_ai_interaction_footer(model_name, ai_prompt_buffer=None, ai_waiting=False, note_buffer=None):
    if not ai_waiting and ai_prompt_buffer is None and note_buffer is None:
        return
    print()
    print(LOWER_BLOCK_INDENT + AI_COLOR + "─" * (terminal_width() - 2) + RESET_COLOR)
    if ai_waiting:
        print(LOWER_BLOCK_INDENT + AI_COLOR + "Agent 正在生成内容，请稍等..." + RESET_COLOR)
        print_wrapped(LOWER_BLOCK_INDENT, "model: " + model_name, color=DIM_COLOR)
        return
    if note_buffer is not None:
        print(LOWER_BLOCK_INDENT + AI_COLOR + "User Note" + RESET_COLOR)
        print_wrapped(LOWER_BLOCK_INDENT, "Enter 保存；清空后 Enter 会删除笔记；Esc 取消。")
        print_wrapped(LOWER_BLOCK_INDENT + "> ", note_buffer)
        return
    print(LOWER_BLOCK_INDENT + AI_COLOR + "Agent 追加提示词" + RESET_COLOR)
    print_wrapped(LOWER_BLOCK_INDENT, "Enter 直接生成；输入文字后 Enter 会追加到默认提示词；Esc 取消。")
    print_wrapped(LOWER_BLOCK_INDENT + "> ", ai_prompt_buffer)


def print_warning(warning):
    if warning:
        print_wrapped(LOWER_BLOCK_INDENT, warning, color=WRONG_COLOR)


# ---- full-screen renderers ----

def _answer_footer(q, translation_enabled):
    """Build the key-hint footer for the answer-input screen."""
    parts = ["↑/↓ move", "← 后退", "Space select", "Enter submit", "1-4 quick answer"]
    if translation_enabled:
        parts.append("t 中文")
    parts.append(detail_key_hints(q))
    parts.append("m 标记")
    parts.append("r 移除")
    parts.append("Esc 返回选题, q quit")
    return ", ".join(parts)


def _result_footer(q, translation_enabled, override=None):
    if override is not None:
        return override
    parts = ["Enter 下一题", "← 后退"]
    if translation_enabled:
        parts.append("t 中文")
    parts.append(detail_key_hints(q))
    parts.append("m 标记")
    parts.append("r 移除")
    parts.append("Esc 返回选题, q quit")
    return ", ".join(parts)


def render_question(count, total, chapter, q, options, cursor, selected, *,
                    warning="", show_translation=False, translation=None,
                    detail_view=None, marked=False, model_name="",
                    translation_enabled=True,
                    ai_prompt_buffer=None, ai_waiting=False, note_buffer=None):
    clear_screen()
    mark_text = " " + MARK_COLOR + "[MARKED]" + RESET_COLOR if marked else ""
    print("@ Chapter", chapter, f"({count} / {total})", mark_text)
    print_wrapped("> ", question_topic(q), color=SELECTED_COLOR)
    print()

    for index, choice in enumerate(options):
        cursor_marker = ">" if index == cursor else " "
        selected_marker = "[x]" if index in selected else "[ ]"
        prefix = f"{cursor_marker} {selected_marker} "
        print_wrapped(prefix, choice, continuation_prefix=" " * display_width(prefix))

    if show_translation:
        print_translation_block(translation)
    print_detail_view(q, detail_view)

    print_key_hint_footer(_answer_footer(q, translation_enabled))
    print_warning(warning)
    print_ai_interaction_footer(model_name, ai_prompt_buffer, ai_waiting, note_buffer)


def render_result(count, total, chapter, q, options, selected_answer, is_correct, *,
                   warning="", show_translation=False, translation=None,
                   detail_view=None, marked=False, model_name="",
                   translation_enabled=True,
                   ai_prompt_buffer=None, ai_waiting=False, footer=None, note_buffer=None):
    clear_screen()
    mark_text = " " + MARK_COLOR + "[MARKED]" + RESET_COLOR if marked else ""
    print("@ Chapter", chapter, f"({count} / {total})", mark_text)
    print_wrapped("> ", question_topic(q), color=SELECTED_COLOR)
    print()

    correct_answers = set(q['answer'])
    selected_answers = set(selected_answer)
    for choice in options:
        label = option_label(choice)
        is_correct_option = label in correct_answers
        is_selected = label in selected_answers
        marker = "✓" if is_correct_option else ("✗" if is_selected else " ")
        line = marker + " " + choice
        color = CORRECT_COLOR if is_correct_option else ("\033[1;31m" if is_selected else "")
        print_wrapped("", line, continuation_prefix="  ", color=color)

    if show_translation:
        print_translation_block(translation)
    print_detail_view(q, detail_view)

    print_key_hint_footer(_result_footer(q, translation_enabled, footer))
    print_warning(warning)
    print_ai_interaction_footer(model_name, ai_prompt_buffer, ai_waiting, note_buffer)


def render_review_question(index, total, chapter, q, *, options=None, show_translation=False,
                           translation=None, detail_view=None, marked=False,
                           warning="", model_name="", translation_enabled=True,
                           ai_prompt_buffer=None, ai_waiting=False, note_buffer=None):
    options = list(q.get("options", [])) if options is None else options
    correct_answers = set(q.get('answer', ''))
    clear_screen()
    mark_text = " " + MARK_COLOR + "[MARKED]" + RESET_COLOR if marked else ""
    print("@ Chapter", chapter, f"({index + 1} / {total})", mark_text)
    print_wrapped("> ", question_topic(q), color=SELECTED_COLOR)
    print()

    for choice in options:
        label = option_label(choice)
        is_correct = label in correct_answers
        prefix = "✓" if is_correct else " "
        line = prefix + " " + choice
        print_wrapped("", line, continuation_prefix="  ", color=CORRECT_COLOR if is_correct else "")

    if show_translation:
        print_translation_block(translation)
    print_detail_view(q, detail_view)

    parts = ["←/→ 后退/前进"]
    if translation_enabled:
        parts.append("t 中文")
    parts.append(detail_key_hints(q))
    parts.append("m 标记")
    parts.append("r 移除")
    parts.append("Esc 返回选题, q quit")
    print_key_hint_footer(", ".join(parts))
    print_warning(warning)
    print_ai_interaction_footer(model_name, ai_prompt_buffer, ai_waiting, note_buffer)


def render_ai_waiting(chapter, q, options, model_name):
    clear_screen()
    print("@ Chapter", chapter)
    print_wrapped("> ", question_topic(q), color=SELECTED_COLOR)
    print()
    for choice in options:
        print_wrapped("  [ ] ", choice)
    print()
    print(LOWER_BLOCK_INDENT + AI_COLOR + "Agent 正在生成内容，请稍等..." + RESET_COLOR)
    print(LOWER_BLOCK_INDENT + DIM_COLOR + "model: " + model_name + RESET_COLOR)


def render_ai_prompt_input(chapter, q, options, buffer):
    clear_screen()
    print("@ Chapter", chapter)
    print_wrapped("> ", question_topic(q), color=SELECTED_COLOR)
    print()
    for choice in options:
        print_wrapped("  [ ] ", choice)
    print()
    print(LOWER_BLOCK_INDENT + "x: Agent Said")
    print_wrapped(LOWER_BLOCK_INDENT, "Enter 直接生成；输入文字后 Enter 会追加到默认提示词；Esc 取消。")
    print()
    print_wrapped(LOWER_BLOCK_INDENT + "> ", buffer)


def render_note_input(chapter, q, buffer):
    clear_screen()
    print("@ Chapter", chapter)
    print_wrapped("> ", question_topic(q), color=SELECTED_COLOR)
    print()
    print(LOWER_BLOCK_INDENT + "n: User Note")
    print_wrapped(LOWER_BLOCK_INDENT, "Enter 保存；清空后 Enter 会删除笔记；Esc 取消。")
    print()
    print_wrapped(LOWER_BLOCK_INDENT + "> ", buffer)
