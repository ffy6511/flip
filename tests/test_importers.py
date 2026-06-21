import pytest

from flip.importers import import_csv, validate_tiku, _resolve_delimiter


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


class TestImportCsvBasic:
    def test_simple_csv(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,B,C,D,answer,chapter\n"
                   '"What is 2+2?","3","4","5","6","B","1"\n'
                   '"Capital?","Berlin","Madrid","Paris","Rome","C","2"\n')
        result = import_csv(p)
        assert result.ok
        assert result.question_count == 2
        assert set(result.chapters.keys()) == {"1", "2"}
        assert result.answer_alphabet == "ABCD"
        q1 = result.chapters["1"][0]
        assert q1["topic"] == "What is 2+2?"
        assert q1["options"] == ["A. 3", "B. 4", "C. 5", "D. 6"]
        assert q1["answer"] == "B"

    def test_multi_select_answer(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,B,C,D,answer\n"
                   '"Primes?","2","4","7","9","AC"\n')
        result = import_csv(p)
        assert result.ok
        assert result.chapters["1"][0]["answer"] == "AC"

    def test_default_chapter_is_1(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,B,answer\n"
                   '"q","x","y","A"\n')
        result = import_csv(p)
        assert "1" in result.chapters


class TestImportCsvAlphabet:
    def test_three_options(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,B,C,answer\n"
                   '"q","x","y","z","C"\n')
        result = import_csv(p)
        assert result.answer_alphabet == "ABC"

    def test_five_options(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,B,C,D,E,answer\n"
                   '"q","1","2","3","4","5","E"\n')
        result = import_csv(p)
        assert result.answer_alphabet == "ABCDE"

    def test_widest_question_drives_alphabet(self, tmp_path):
        # One 4-option question, one 5-option question -> ABCDE
        p = _write(tmp_path, "q.csv",
                   "topic,A,B,C,D,E,answer\n"
                   '"q1","1","2","3","4","","A"\n'
                   '"q2","1","2","3","4","5","E"\n')
        result = import_csv(p)
        assert result.answer_alphabet == "ABCDE"


class TestImportCsvDelimiters:
    def test_tab_delimited(self, tmp_path):
        p = _write(tmp_path, "q.tsv",
                   "topic\tA\tB\tanswer\n"
                   "q\tx\ty\tA\n")
        result = import_csv(p)
        assert result.ok
        assert result.chapters["1"][0]["options"] == ["A. x", "B. y"]

    def test_explicit_delimiter(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic;A;B;answer\n"
                   "q;x;y;A\n")
        result = import_csv(p, delimiter="semicolon")
        assert result.ok

    def test_unknown_delimiter_raises(self, tmp_path):
        p = _write(tmp_path, "q.csv", "topic,A,B,answer\nq,x,y,A\n")
        with pytest.raises(ValueError, match="unknown delimiter"):
            import_csv(p, delimiter="weird")


class TestImportCsvErrors:
    def test_missing_required_column(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,B\n"  # no answer column
                   '"q","x","y"\n')
        with pytest.raises(ValueError, match="answer"):
            import_csv(p)

    def test_too_few_option_columns(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,answer\n"
                   '"q","x","A"\n')
        with pytest.raises(ValueError, match="at least 2 option"):
            import_csv(p)

    def test_empty_topic_row_skipped(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,B,answer\n"
                   '"","x","y","A"\n'
                   '"real","x","y","B"\n')
        result = import_csv(p)
        assert len(result.errors) == 1
        assert result.question_count == 1

    def test_answer_letter_not_in_options(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,B,answer\n"
                   '"q","x","y","D"\n')
        result = import_csv(p)
        assert len(result.errors) == 1
        assert "not in available options" in result.errors[0][1]

    def test_empty_file_raises(self, tmp_path):
        p = _write(tmp_path, "q.csv", "")
        with pytest.raises(ValueError, match="empty"):
            import_csv(p)


class TestImportCsvNoHeader:
    def test_no_header_positional(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   '"q","x","y","z","w","A","5"\n')
        result = import_csv(p, has_header=False)
        assert result.ok
        q = result.chapters["5"][0]
        assert q["options"] == ["A. x", "B. y", "C. z", "D. w"]
        assert q["answer"] == "A"


class TestImportCsvTranslation:
    def test_zh_columns_when_enabled(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,B,answer,zh_topic,zh_A,zh_B\n"
                   '"hi","x","y","A","嗨","甲","乙"\n')
        result = import_csv(p, translation_enabled=True)
        q = result.chapters["1"][0]
        assert q["zh"]["topic"] == "嗨"
        assert q["zh"]["options"] == ["A. 甲", "B. 乙"]

    def test_zh_ignored_when_disabled(self, tmp_path):
        p = _write(tmp_path, "q.csv",
                   "topic,A,B,answer,zh_topic,zh_A,zh_B\n"
                   '"hi","x","y","A","嗨","甲","乙"\n')
        result = import_csv(p, translation_enabled=False)
        q = result.chapters["1"][0]
        assert "zh" not in q


class TestResolveDelimiter:
    def test_named(self):
        assert _resolve_delimiter("comma", None) == ","
        assert _resolve_delimiter("tab", None) == "\t"

    def test_literal_char(self):
        assert _resolve_delimiter(":", None) == ":"

    def test_auto_comma(self):
        assert _resolve_delimiter("auto", "a,b,c") == ","

    def test_auto_tab(self):
        assert _resolve_delimiter("auto", "a\tb\tc") == "\t"


# ---- validate_tiku ----

class TestValidateTiku:
    def test_valid_example(self, deck):
        from flip.store import load_tiku
        data = load_tiku(deck)
        errs = validate_tiku(data)
        assert errs == [], f"unexpected errors: {errs}"

    def test_non_dict_top_level(self):
        assert validate_tiku([1, 2, 3])

    def test_empty_dict(self):
        assert validate_tiku({}) == ["tiku is empty (no chapters)"]

    def test_missing_required_field(self):
        data = {"1": [{"topic": "t", "options": ["A. x"]}]}  # no answer
        errs = validate_tiku(data)
        assert any("answer" in e for e in errs)

    def test_answer_not_in_options(self):
        data = {"1": [{"topic": "t", "options": ["A. x", "B. y"], "answer": "Z"}]}
        errs = validate_tiku(data)
        assert any("not in options" in e for e in errs)

    def test_empty_options_list(self):
        data = {"1": [{"topic": "t", "options": [], "answer": "A"}]}
        errs = validate_tiku(data)
        assert any("empty" in e for e in errs)

    def test_chapter_not_list(self):
        data = {"1": "not a list"}
        errs = validate_tiku(data)
        assert any("must be a list" in e for e in errs)
