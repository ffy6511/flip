import pytest

from flip.engine import chapter_selector


class TestChapterSelector:
    def test_none_returns_all(self):
        assert chapter_selector(None, 5) == {1, 2, 3, 4, 5}

    def test_single_number(self):
        assert chapter_selector("5", 10) == {5}

    def test_range(self):
        assert chapter_selector("3-5", 10) == {3, 4, 5}

    def test_negative_means_first_n(self):
        assert chapter_selector("-3", 10) == {1, 2, 3}

    def test_negative_zero_means_all(self):
        assert chapter_selector("-0", 5) == {1, 2, 3, 4, 5}

    def test_range_start_greater_than_end_raises(self):
        with pytest.raises(ValueError):
            chapter_selector("5-3", 10)

    def test_negative_as_int(self):
        # int("-3") is negative, exercises the num_list <= 0 branch
        assert chapter_selector("-2", 10) == {1, 2}

    def test_comma_union_of_singles(self):
        assert chapter_selector("5,3", 10) == {3, 5}

    def test_comma_union_with_range(self):
        assert chapter_selector("5,3-4", 10) == {3, 4, 5}

    def test_comma_dedupes(self):
        # overlapping segments collapse to a set
        assert chapter_selector("3-5,4", 10) == {3, 4, 5}

    def test_comma_with_negative_segment(self):
        assert chapter_selector("9,-2", 10) == {1, 2, 9}

    def test_comma_whitespace_tolerant(self):
        assert chapter_selector("5, 3-4 ", 10) == {3, 4, 5}
