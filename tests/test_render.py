import re

from flip.tui import render


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _question(answer="A"):
    return {
        "topic": "Which compiler phases can reject a program?",
        "options": ["A. Lexing", "B. Parsing", "C. Code emission", "D. Linking"],
        "answer": answer,
    }


def _strip_ansi(text):
    return ANSI_RE.sub("", text)


def test_render_question_marks_multi_select_without_mutating_topic(capsys):
    q = _question("AB")

    render.render_question(1, 1, "1", q, q["options"], 0, set())

    out = capsys.readouterr().out
    assert "Which compiler phases can reject a program? [多选]" in out
    assert q["topic"] == "Which compiler phases can reject a program?"


def test_render_question_does_not_mark_single_select(capsys):
    q = _question("A")

    render.render_question(1, 1, "1", q, q["options"], 0, set())

    out = capsys.readouterr().out
    assert "Which compiler phases can reject a program? [多选]" not in out
    assert "Which compiler phases can reject a program?" in out


# ---- multi-select badge dedup ----
#
# If the source topic already ends with a multi-select marker (English
# "[multi-select]" or Chinese "[多选]"), the renderer must NOT append another
# [多选]. Otherwise we get "...? [multi-select] [多选]" / "...？ [多选] [多选]",
# which is the user-reported bug.

def test_badge_not_duplicated_when_topic_has_english_marker():
    # The exact case from the bug report: English source with [multi-select].
    assert render.topic_with_answer_badge(
        "Which of the following are standard loop optimizations? [multi-select]", "ABC"
    ) == "Which of the following are standard loop optimizations? [multi-select]"


def test_badge_not_duplicated_when_topic_has_chinese_marker():
    # Translated topic that already carries [多选].
    assert render.topic_with_answer_badge(
        "以下哪些是标准循环优化？ [多选]", "ABC"
    ) == "以下哪些是标准循环优化？ [多选]"


def test_badge_still_appended_when_topic_has_no_marker():
    # No pre-existing marker → renderer adds exactly one [多选].
    assert render.topic_with_answer_badge(
        "Which compiler phases can reject a program?", "AB"
    ) == "Which compiler phases can reject a program? [多选]"


def test_badge_recognizes_marker_spacing_variants():
    # Tolerate minor whitespace/separator variants so we don't half-fix it.
    assert render.topic_with_answer_badge("... ? [multi select]", "AB") == "... ? [multi select]"
    assert render.topic_with_answer_badge("... ? [Multi-Select]", "AB") == "... ? [Multi-Select]"


def test_render_result_and_review_mark_multi_select(capsys):
    q = _question("AB")

    render.render_result(1, 1, "1", q, q["options"], "A", False)
    result_out = capsys.readouterr().out
    render.render_review_question(0, 1, "1", q)
    review_out = capsys.readouterr().out

    assert "Which compiler phases can reject a program? [多选]" in result_out
    assert "Which compiler phases can reject a program? [多选]" in review_out


def test_wrap_text_respects_cjk_display_width():
    lines = render.wrap_text("软件工程课程设计", 8)

    assert lines == ["软件工程", "课程设计"]


def test_render_question_wraps_long_options_to_terminal_width(capsys, monkeypatch):
    q = {
        "topic": "A narrow terminal should reflow long quiz text",
        "options": [
            "A. This option is intentionally long so it must wrap cleanly",
            "B. short",
        ],
        "answer": "A",
    }
    monkeypatch.setattr(render, "terminal_width", lambda: 32)

    render.render_question(1, 1, "1", q, q["options"], 0, set())

    out = capsys.readouterr().out
    assert "> [ ] A. This option is" in out
    assert "      intentionally long so it" in out


def test_render_question_swaps_translation_into_primary_slot(capsys):
    q = _question("AB")
    translation = {
        "topic": "哪些编译阶段会拒绝程序？",
        "options": ["A. 词法分析", "B. 语法分析", "C. 代码生成", "D. 链接"],
    }

    render.render_question(
        1, 1, "1", q, q["options"], 0, set(),
        show_translation=True, translation=translation,
    )

    out = _strip_ansi(capsys.readouterr().out)
    assert out.index("哪些编译阶段会拒绝程序？ [多选]") < out.index("Which compiler phases can reject a program? [多选]")
    assert "A. 词法分析" in out
    assert "A. Lexing" in out


def test_render_result_keeps_answer_markers_aligned(capsys):
    q = _question("A")

    render.render_result(1, 1, "1", q, q["options"], "B", False)

    lines = _strip_ansi(capsys.readouterr().out).splitlines()
    assert "✓ A. Lexing" in lines
    assert "✗ B. Parsing" in lines
    assert "  C. Code emission" in lines
    assert "  D. Linking" in lines


def test_render_review_question_keeps_answer_markers_aligned(capsys):
    q = _question("A")

    render.render_review_question(0, 1, "1", q)

    lines = _strip_ansi(capsys.readouterr().out).splitlines()
    assert "✓ A. Lexing" in lines
    assert "  B. Parsing" in lines
    assert "  C. Code emission" in lines
    assert "  D. Linking" in lines


def test_render_scored_session_summary_colors_accuracy(capsys):
    summary = {
        "kind": "scored",
        "mode": "train",
        "label": "全部",
        "total": 5,
        "correct": 1,
        "incorrect": 4,
        "wrong_items": [],
    }

    render.render_session_summary(summary)

    out = capsys.readouterr().out
    assert render.AI_COLOR + "20.0%" + render.RESET_COLOR in out
    assert "还有提升空间，建议回看错题" in out


def test_render_browse_session_summary_shows_count_only(capsys):
    summary = {
        "kind": "browse",
        "mode": "review",
        "label": "1",
        "total": 3,
        "browse_items": [],
    }

    render.render_session_summary(summary)

    out = capsys.readouterr().out
    assert "浏览数量: 3" in out
    assert "正确率" not in out


def test_render_session_item_list_groups_by_chapter_and_highlights_selected(capsys):
    items = [
        {
            "chapter": "25",
            "question": {
                "topic": "12. First prompt",
                "options": ["A. Alpha", "B. Beta"],
                "answer": "B",
            },
            "options": ["A. Alpha", "B. Beta"],
            "selected_answer": "A",
        },
        {
            "chapter": "25",
            "question": {
                "topic": "9. Second prompt",
                "options": ["A. One", "B. Two"],
                "answer": "A",
            },
            "options": ["A. One", "B. Two"],
        },
        {
            "chapter": "26",
            "question": {
                "topic": "1. Third prompt",
                "options": ["A. Red", "B. Blue"],
                "answer": "A",
            },
            "options": ["A. Red", "B. Blue"],
        },
    ]

    render.render_session_item_list("本轮错题", items, 0)

    out = capsys.readouterr().out
    plain = _strip_ansi(out)
    assert "\nch25\n" in plain
    assert "\nch26\n" in plain
    assert "12. First prompt" not in plain
    assert "9. Second prompt" not in plain
    assert "1. Third prompt" not in plain
    assert "· First prompt" in plain
    assert "· Second prompt" in plain
    assert "· Third prompt" in plain
    assert "> · " + render.SELECTED_COLOR + "First prompt" + render.RESET_COLOR in out
    assert render.DIM_COLOR in out and "─" in plain


def test_render_session_item_list_emits_one_header_per_chapter_run(capsys):
    # The renderer trusts that the caller pre-sorted items by chapter (sorting
    # lives in engine_loop so cursor order matches display order). When a
    # chapter's items are contiguous, exactly one header is emitted for that
    # run; adjacent same-chapter runs would each get their own header — that
    # is the caller's job to avoid.
    items = [
        {"chapter": "1", "question": {"topic": "1. b", "options": ["A. x"], "answer": "A"},
         "options": ["A. x"]},
        {"chapter": "2", "question": {"topic": "1. d", "options": ["A. x"], "answer": "A"},
         "options": ["A. x"]},
        {"chapter": "3", "question": {"topic": "1. a", "options": ["A. x"], "answer": "A"},
         "options": ["A. x"]},
        {"chapter": "3", "question": {"topic": "2. c", "options": ["A. x"], "answer": "A"},
         "options": ["A. x"]},
        {"chapter": "3", "question": {"topic": "3. e", "options": ["A. x"], "answer": "A"},
         "options": ["A. x"]},
    ]

    render.render_session_item_list("本轮错题", items, 0)

    plain = _strip_ansi(capsys.readouterr().out)
    # Exactly one header per chapter, no duplicates.
    assert plain.count("\nch1\n") == 1
    assert plain.count("\nch2\n") == 1
    assert plain.count("\nch3\n") == 1
    # Chapters appear in the given order, and ch3's three items are contiguous.
    assert plain.index("ch1") < plain.index("ch2") < plain.index("ch3")
    ch3_start = plain.index("ch3")
    ch3_block = plain[ch3_start:]
    assert ch3_block.index("· a") < ch3_block.index("· c") < ch3_block.index("· e")


def test_render_session_item_list_windows_long_list_around_cursor(capsys, monkeypatch):
    # A list taller than the terminal must be clipped, with the selected item
    # kept in view. Previously every item was printed and overflow was lost.
    monkeypatch.setattr(render, "terminal_width", lambda: 40)
    monkeypatch.setattr(render, "terminal_height", lambda: 8)
    items = [
        {"chapter": "1", "question": {"topic": f"q{n}", "options": ["A. x"], "answer": "A"},
         "options": ["A. x"]}
        for n in range(20)
    ]

    # Cursor near the end: the top of the list must be clipped away.
    render.render_session_item_list("本轮错题", items, 19)
    plain = _strip_ansi(capsys.readouterr().out)
    assert "↑ 更多" in plain
    assert "· q19" in plain  # selected still visible
    assert "· q0" not in plain  # off the top of the window

    # Cursor at the start: the bottom is clipped instead.
    render.render_session_item_list("本轮错题", items, 0)
    plain = _strip_ansi(capsys.readouterr().out)
    assert "↓ 更多" in plain
    assert "· q0" in plain
    assert "· q19" not in plain


def test_render_session_item_list_shows_selected_translation_block_before_separator(capsys):
    items = [
        {
            "chapter": "25",
            "question": {
                "topic": "12. First prompt",
                "options": ["A. Alpha", "B. Beta"],
                "answer": "B",
                "zh": {
                    "topic": "12. 第一题",
                    "options": ["A. 甲", "B. 乙"],
                },
            },
            "options": ["A. Alpha", "B. Beta"],
            "selected_answer": "A",
        },
        {
            "chapter": "25",
            "question": {
                "topic": "13. Second prompt",
                "options": ["A. One", "B. Two"],
                "answer": "A",
            },
            "options": ["A. One", "B. Two"],
        },
    ]

    render.render_session_item_list("本轮错题", items, 0, show_translation=True)

    plain = _strip_ansi(capsys.readouterr().out)
    assert "· First prompt" in plain
    assert "第一题" in plain
    # "你的答案" and "正确答案" share one line (left before right).
    assert any(
        "你的答案: A" in line and "正确答案: B" in line
        for line in plain.splitlines()
    )
    assert plain.index("· First prompt") < plain.index("你的答案: A") < plain.index("正确答案: B")
    assert plain.index("B. Beta") < plain.index("第一题") < plain.index("──")
    assert plain.index("第一题") < plain.index("A. 甲") < plain.index("B. 乙") < plain.index("──")


def test_render_review_search_numbers_items_and_keeps_fixed_two_line_rows(capsys, monkeypatch):
    monkeypatch.setattr(render, "terminal_width", lambda: 32)
    monkeypatch.setattr(render, "terminal_height", lambda: 14)
    results = [
        {
            "question": {
                "topic": "1. An extremely long English title that should not wrap into multiple rows",
                "answer": "A",
                "options": ["A. x"],
                "zh": {
                    "topic": "1. 这是一个非常长的中文标题，渲染时也不应该继续向下换行挤掉搜索栏",
                    "options": ["A. 甲"],
                },
            }
        },
        {
            "question": {
                "topic": "2. Another long English title that would normally wrap in a narrow terminal",
                "answer": "A",
                "options": ["A. x"],
                "zh": {
                    "topic": "2. 第二个中文标题同样应该被压成一行显示",
                    "options": ["A. 甲"],
                },
            }
        },
    ]

    render.render_review_search("cache", results, 0)

    plain = _strip_ansi(capsys.readouterr().out)
    lines = plain.splitlines()
    assert "search: cache" in plain
    assert "> 1. " in plain
    assert "  2. " in plain
    assert any(line.startswith("> 1. ") for line in lines)
    assert any(line.startswith("    这是一个非常长的中文标题") for line in lines)
    assert not any(line.startswith("  title that should") for line in lines)
    assert not any(line.startswith("  into multiple rows") for line in lines)


def test_accuracy_style_thresholds():
    assert render._accuracy_style(10, 5)[0] == render.CORRECT_COLOR
    assert render._accuracy_style(10, 2)[0] == render.AI_COLOR
    assert render._accuracy_style(10, 1)[0] == render.WRONG_COLOR
