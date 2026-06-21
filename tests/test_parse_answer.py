from flip.engine import parse_answer


class TestParseAnswerDigits:
    def test_single_digit(self):
        assert parse_answer("1", "ABCD") == "A"

    def test_multi_digit_unsorted(self):
        assert parse_answer("21", "ABCD") == "AB"

    def test_digit_beyond_alphabet_ignored(self):
        # alphabet ABCD, digit 5 maps to nothing
        assert parse_answer("5", "ABCD") == ""


class TestParseAnswerLetters:
    def test_single_letter(self):
        assert parse_answer("b", "ABCD") == "B"

    def test_multi_letter_deduped_sorted(self):
        assert parse_answer("DB", "ABCD") == "BD"

    def test_letter_outside_alphabet_ignored(self):
        assert parse_answer("E", "ABCD") == ""
        assert parse_answer("Z", "ABCD") == ""


class TestParseAnswerMixed:
    def test_letters_and_digits_combined(self):
        # "1C3" -> A,C,C -> AC
        assert parse_answer("1C3", "ABCD") == "AC"

    def test_digit_outside_alphabet_with_valid_letter(self):
        assert parse_answer("5A", "ABCD") == "A"


class TestParseAnswerAlphabets:
    def test_five_letter_alphabet(self):
        # The pentagon case: E is a normal option now
        assert parse_answer("5", "ABCDE") == "E"
        assert parse_answer("15", "ABCDE") == "AE"

    def test_case_insensitive(self):
        assert parse_answer("ac", "ABCD") == "AC"
