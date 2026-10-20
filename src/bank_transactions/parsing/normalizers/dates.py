"""Date parsing for issuer cells. Two-digit years use a sliding window around an anchor date."""

import calendar
import re
from datetime import date, datetime

from bank_transactions.domain.text import fold_text
from bank_transactions.domain.workbook import CellValue
from bank_transactions.parsing.normalizers.text import strip_bidi_marks

DEFAULT_TWO_DIGIT_YEARS_BACK = 80
_YEARS_PER_CENTURY = 100
_DAY_FIRST = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})")
_YEAR_FIRST = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_MONTH_SLASH_YEAR = re.compile(r"(\d{1,2})/(\d{4})")
_MONTH_YEAR_TEXT = re.compile(r"(\S+)\s+(\d{4})")
_HEBREW_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "ינואר",
            "פברואר",
            "מרץ",
            "אפריל",
            "מאי",
            "יוני",
            "יולי",
            "אוגוסט",
            "ספטמבר",
            "אוקטובר",
            "נובמבר",
            "דצמבר",
        ),
        start=1,
    )
}


class InvalidDateError(ValueError):
    """A non-blank cell that is not a real calendar date."""


def check_two_digit_years_back(years_back: int) -> int:
    """`years_back` is how many years before the anchor a two-digit year may fall (0..99); the
    window is the hundred years [anchor - years_back, anchor + 99 - years_back]."""
    if not 0 <= years_back < _YEARS_PER_CENTURY:
        raise ValueError("two_digit_years_back must be between 0 and 99")
    return years_back


def resolve_two_digit_year(
    two_digit: int, anchor_year: int, years_back: int = DEFAULT_TWO_DIGIT_YEARS_BACK
) -> int:
    """The year ending in `two_digit` inside [anchor - years_back, anchor + 99 - years_back]."""
    earliest = anchor_year - check_two_digit_years_back(years_back)
    year = earliest - earliest % _YEARS_PER_CENTURY + two_digit
    return year if year >= earliest else year + _YEARS_PER_CENTURY


def parse_date(
    value: CellValue,
    anchor: date,
    *,
    two_digit_years_back: int = DEFAULT_TWO_DIGIT_YEARS_BACK,
) -> date | None:
    """A cell as a date. `anchor` (statement date or today) and `two_digit_years_back` resolve
    two-digit years."""
    check_two_digit_years_back(two_digit_years_back)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise InvalidDateError("date cell is neither a date nor text")
    text = strip_bidi_marks(value).strip()
    if not text:
        return None
    return _from_text(text, anchor.year, two_digit_years_back)


def parse_month_year(value: CellValue) -> tuple[int, int] | None:
    """(year, month) of a statement-month label such as `פברואר 2026` or `05/2026`."""
    if not isinstance(value, str):
        return None
    text = fold_text(strip_bidi_marks(value))
    slash = _MONTH_SLASH_YEAR.fullmatch(text)
    if slash is not None:
        month = int(slash.group(1))
        return (int(slash.group(2)), month) if 1 <= month <= 12 else None
    named = _MONTH_YEAR_TEXT.fullmatch(text)
    if named is None or named.group(1) not in _HEBREW_MONTHS:
        return None
    return int(named.group(2)), _HEBREW_MONTHS[named.group(1)]


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _from_text(text: str, anchor_year: int, years_back: int) -> date:
    year_first = _YEAR_FIRST.fullmatch(text)
    if year_first is not None:
        year, month, day = (int(part) for part in year_first.groups())
        return _calendar_date(year, month, day)
    day_first = _DAY_FIRST.fullmatch(text)
    if day_first is None:
        raise InvalidDateError("date text has an unknown layout")
    day_text, month_text, year_text = day_first.groups()
    year = (
        resolve_two_digit_year(int(year_text), anchor_year, years_back)
        if len(year_text) == 2
        else int(year_text)
    )
    return _calendar_date(year, int(month_text), int(day_text))


def _calendar_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as error:
        raise InvalidDateError("not a calendar date") from error
