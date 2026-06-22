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


def test_render_session_item_list_shows_inline_translation(capsys):
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
    ]

    render.render_session_item_list("本轮错题", items, 0, show_translation=True)

    plain = _strip_ansi(capsys.readouterr().out)
    assert "· First prompt" in plain
    assert "第一题" in plain
    assert plain.index("· First prompt") < plain.index("第一题") < plain.index("你的答案: A")
    assert "A. 甲" in plain
    assert "B. 乙" in plain


def test_accuracy_style_thresholds():
    assert render._accuracy_style(10, 5)[0] == render.CORRECT_COLOR
    assert render._accuracy_style(10, 2)[0] == render.AI_COLOR
    assert render._accuracy_style(10, 1)[0] == render.WRONG_COLOR
