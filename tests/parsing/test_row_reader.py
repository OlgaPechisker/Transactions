from datetime import date, datetime
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import IssueSeverity, RowIssueCode
from bank_transactions.domain.money import EUR, ILS
from bank_transactions.parsing.column_map import ColumnLayout
from bank_transactions.parsing.row_reader import PendingRow, RowReader, RowRejected

LAYOUT = ColumnLayout(1, {"date": 1, "name": 2, "amount": 3, "currency": 4, "rate": 5, "note": 6})
ANCHOR = date(2026, 2, 10)


def reader(*cells):
    return RowReader(LAYOUT, cells, ANCHOR)


def rejection(action):
    with pytest.raises(RowRejected) as caught:
        action()
    return caught.value


def test_required_text_is_returned_exactly_as_written():
    assert reader(None, " KLOOK  TRAVEL ").required_text("name") == " KLOOK  TRAVEL "


@pytest.mark.parametrize("cell", [None, "", "   "])
def test_a_blank_required_text_names_the_missing_column_only(cell):
    error = rejection(lambda: reader(None, cell).required_text("name"))

    assert error.code is RowIssueCode.MISSING_REQUIRED_VALUE
    assert error.severity is IssueSeverity.ERROR
    assert "name" in str(error)


def test_text_is_trimmed_and_blank_is_none():
    assert reader(None, None, None, None, None, "  note ").text("note") == "note"
    assert reader().text("note") is None


def test_verbatim_keeps_whitespace_but_not_blank_cells():
    assert reader(None, None, None, None, None, " a  b ").verbatim("note") == " a  b "
    assert reader(None, None, None, None, None, "  ").verbatim("note") is None


def test_amounts_are_exact_decimals_and_blank_is_none():
    assert reader(None, None, 245.83).amount("amount") == Decimal("245.83")
    assert reader().amount("amount") is None


def test_an_unreadable_amount_is_an_invalid_amount_issue_without_cell_data():
    error = rejection(lambda: reader(None, None, "12abc").amount("amount"))

    assert error.code is RowIssueCode.INVALID_AMOUNT
    assert "12abc" not in str(error)


def test_a_blank_required_amount_is_missing():
    error = rejection(lambda: reader().required_amount("amount"))

    assert error.code is RowIssueCode.MISSING_REQUIRED_VALUE


def test_dates_accept_datetimes_and_text():
    assert reader(datetime(2026, 9, 3, 0, 0)).date("date") == date(2026, 9, 3)
    assert reader("09.02.26").date("date") == date(2026, 2, 9)
    assert reader().date("date") is None


def test_an_impossible_date_is_an_invalid_date_issue():
    error = rejection(lambda: reader("31.02.26").date("date"))

    assert error.code is RowIssueCode.INVALID_DATE
    assert "31.02.26" not in str(error)


def test_a_blank_required_date_is_missing():
    error = rejection(lambda: reader().required_date("date"))

    assert error.code is RowIssueCode.MISSING_REQUIRED_VALUE


def test_currency_is_recognised_and_unknown_spellings_are_none():
    assert reader(None, None, None, "₪").currency("currency") == ILS
    assert reader(None, None, None, "EUR").currency("currency") == EUR
    assert reader(None, None, None, "זהב").currency("currency") is None
    assert reader().currency("currency") is None


def test_a_missing_column_reads_as_blank():
    short = RowReader(ColumnLayout(1, {"date": 1}), ("x",), ANCHOR)

    assert short.text("amount") is None
    assert short.amount("amount") is None


def test_a_stated_rate_in_wording_is_read_and_garbage_is_ignored():
    assert reader(None, None, None, None, " 3.2900").rate("rate") == Decimal("3.2900")
    assert reader(None, None, None, None, "n/a").rate("rate") is None


def test_pending_is_an_informational_rejection():
    pending = PendingRow("charge amount not settled yet")

    assert pending.code is RowIssueCode.PENDING_TRANSACTION
    assert pending.severity is IssueSeverity.INFO
    assert isinstance(pending, RowRejected)


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("839176964", "839176964"),
        (839176964, "839176964"),
        (839176964.0, "839176964"),
        (" 0012 ", "0012"),
        (None, None),
        ("  ", None),
    ],
)
def test_identifiers_lose_excel_float_noise_but_keep_leading_zeros(cell, expected):
    assert reader(None, None, None, None, None, cell).identifier("note") == expected


def test_the_row_reader_resolves_two_digit_years_with_its_own_look_back():
    cells = ("01.01.50",)

    assert RowReader(LAYOUT, cells, ANCHOR).date("date") == date(1950, 1, 1)
    assert RowReader(LAYOUT, cells, ANCHOR, two_digit_years_back=20).date("date") == date(
        2050, 1, 1
    )
