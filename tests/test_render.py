from flip.tui import render


def _question(answer="A"):
    return {
        "topic": "Which compiler phases can reject a program?",
        "options": ["A. Lexing", "B. Parsing", "C. Code emission", "D. Linking"],
        "answer": answer,
    }


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
