"""Tests for TWSE OpenAPI client — utility functions and integration."""

from datetime import datetime

from market_monitor.fetchers.twse_openapi import clean_number, roc_to_date


class TestRocToDate:
    """Test ROC (民國) date conversion."""

    def test_7digit_standard(self):
        assert roc_to_date("1150317") == datetime(2026, 3, 17)

    def test_7digit_january(self):
        assert roc_to_date("1150101") == datetime(2026, 1, 1)

    def test_7digit_december(self):
        assert roc_to_date("1141231") == datetime(2025, 12, 31)

    def test_slash_format(self):
        assert roc_to_date("115/03/17") == datetime(2026, 3, 17)

    def test_slash_format_single_digit(self):
        assert roc_to_date("115/1/5") == datetime(2026, 1, 5)

    def test_gregorian_8digit(self):
        assert roc_to_date("20260317") == datetime(2026, 3, 17)

    def test_empty_string(self):
        assert roc_to_date("") is None

    def test_none(self):
        assert roc_to_date(None) is None

    def test_whitespace(self):
        assert roc_to_date("  1150317  ") == datetime(2026, 3, 17)

    def test_invalid_string(self):
        assert roc_to_date("abc") is None

    def test_short_string(self):
        assert roc_to_date("123") is None


class TestCleanNumber:
    """Test numeric string parsing."""

    def test_simple_float(self):
        assert clean_number("76.65") == 76.65

    def test_with_commas(self):
        assert clean_number("1,234,567") == 1234567.0

    def test_with_commas_and_decimal(self):
        assert clean_number("1,234.56") == 1234.56

    def test_integer_string(self):
        assert clean_number("100") == 100.0

    def test_zero(self):
        assert clean_number("0") == 0.0

    def test_negative(self):
        assert clean_number("-1.5") == -1.5

    def test_empty_string(self):
        assert clean_number("") is None

    def test_double_dash(self):
        assert clean_number("--") is None

    def test_single_dash(self):
        assert clean_number("-") is None

    def test_full_width_dash(self):
        assert clean_number("－") is None

    def test_na(self):
        assert clean_number("N/A") is None

    def test_none(self):
        assert clean_number(None) is None

    def test_whitespace(self):
        assert clean_number("  76.65  ") == 76.65

    def test_already_float(self):
        assert clean_number(3.14) == 3.14

    def test_already_int(self):
        assert clean_number(42) == 42.0
