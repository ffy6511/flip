"""Terminal UI primitives for flip.

`keys` reads raw keystrokes; `render` draws screens. Neither contains TUI
interaction loops or business logic — those live in `flip.engine`.
"""

from .keys import RESIZE_KEY, read_key, save_tty, restore_tty, enter_cbreak
from .render import (
    clear_screen,
    enter_alt_screen,
    exit_alt_screen,
    split_option,
    option_label,
    has_translation,
    has_agent_said,
    has_user_note,
    translated_question,
    default_detail_view,
    normalize_detail_view,
    detail_key_hints,
    print_translation_block,
    print_detail_view,
    print_key_hint_footer,
    print_ai_interaction_footer,
    print_warning,
    render_question,
    render_result,
    render_review_question,
    render_ai_waiting,
    render_ai_prompt_input,
    render_note_input,
)

__all__ = [
    "RESIZE_KEY", "read_key", "save_tty", "restore_tty", "enter_cbreak",
    "clear_screen", "enter_alt_screen", "exit_alt_screen",
    "split_option", "option_label",
    "has_translation", "has_agent_said", "has_user_note",
    "translated_question", "default_detail_view", "normalize_detail_view",
    "detail_key_hints",
    "print_translation_block", "print_detail_view",
    "print_key_hint_footer", "print_ai_interaction_footer", "print_warning",
    "render_question", "render_result", "render_review_question",
    "render_ai_waiting", "render_ai_prompt_input", "render_note_input",
]
