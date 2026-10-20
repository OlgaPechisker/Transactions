from datetime import date, datetime

import pytest

from bank_transactions.parsing.normalizers.dates import (
    InvalidDateError,
    month_bounds,
    parse_date,
    parse_month_year,
    resolve_two_digit_year,
)

ANCHOR = date(2026, 2, 10)


def test_excel_datetimes_become_plain_dates():
    assert parse_date(datetime(2026, 9, 4, 0, 0), ANCHOR) == date(2026, 9, 4)
    assert parse_date(date(2026, 9, 4), ANCHOR) == date(2026, 9, 4)


def test_max_style_day_month_year_text():
    assert parse_date("10-07-2026", ANCHOR) == date(2026, 7, 10)


def test_isracard_style_two_digit_year_text():
    assert parse_date("09.02.26", ANCHOR) == date(2026, 2, 9)


def test_slash_separated_and_iso_text_are_accepted():
    assert parse_date("10/09/2026", ANCHOR) == date(2026, 9, 10)
    assert parse_date("2026-07-10", ANCHOR) == date(2026, 7, 10)


def test_blank_cells_are_missing_not_invalid():
    assert parse_date(None, ANCHOR) is None
    assert parse_date("   ", ANCHOR) is None


@pytest.mark.parametrize("bad", ["32.01.2026", "30.02.2026", "abc", "10-2026", 45000, True])
def test_unreadable_dates_raise(bad):
    with pytest.raises(InvalidDateError):
        parse_date(bad, ANCHOR)


def test_sliding_window_puts_two_digit_years_between_80_back_and_19_ahead():
    assert resolve_two_digit_year(26, 2026) == 2026
    assert resolve_two_digit_year(45, 2026) == 2045
    assert resolve_two_digit_year(46, 2026) == 1946
    assert resolve_two_digit_year(99, 2026) == 1999


def test_two_digit_year_follows_the_supplied_anchor_across_a_century_boundary():
    assert parse_date("31.12.99", date(2001, 1, 5)) == date(1999, 12, 31)
    assert parse_date("01.01.02", date(2001, 1, 5)) == date(2002, 1, 1)


def test_hebrew_statement_month_and_year():
    assert parse_month_year("פברואר 2026") == (2026, 2)
    assert parse_month_year("\u202bדצמבר 2025\u202c") == (2025, 12)
    assert parse_month_year("05/2026") == (2026, 5)


def test_unknown_month_text_is_not_a_month():
    assert parse_month_year("פירוט עסקאות") is None
    assert parse_month_year(None) is None


def test_month_bounds_handle_leap_years():
    assert month_bounds(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_bounds(2028, 2) == (date(2028, 2, 1), date(2028, 2, 29))
    assert month_bounds(2026, 12) == (date(2026, 12, 1), date(2026, 12, 31))


def test_the_default_window_resolves_a_year_ending_in_50_to_1950():
    assert resolve_two_digit_year(50, 2026) == 1950
    assert parse_date("01.01.50", ANCHOR) == date(1950, 1, 1)


def test_a_shorter_look_back_moves_the_same_year_forward_a_century():
    assert resolve_two_digit_year(50, 2026, years_back=20) == 2050
    assert parse_date("01.01.50", ANCHOR, two_digit_years_back=20) == date(2050, 1, 1)


@pytest.mark.parametrize(
    ("years_back", "earliest", "latest"),
    [(0, 2026, 2125), (20, 2006, 2105), (80, 1946, 2045), (99, 1927, 2026)],
)
def test_the_window_always_spans_one_hundred_years_from_the_anchor_minus_the_look_back(
    years_back, earliest, latest
):
    resolved = [resolve_two_digit_year(n, 2026, years_back=years_back) for n in range(100)]

    assert min(resolved) == earliest
    assert max(resolved) == latest
    assert sorted(year % 100 for year in resolved) == list(range(100))


@pytest.mark.parametrize("bad", [-1, 100])
def test_a_look_back_outside_0_to_99_is_refused(bad):
    with pytest.raises(ValueError, match="two_digit_years_back"):
        resolve_two_digit_year(50, 2026, years_back=bad)
    with pytest.raises(ValueError, match="two_digit_years_back"):
        parse_date("01.01.50", ANCHOR, two_digit_years_back=bad)
