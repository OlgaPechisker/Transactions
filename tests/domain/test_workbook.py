from datetime import datetime

import pytest

from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.workbook import SheetView, WorkbookView


def sheet():
    return SheetView(
        name="לאומי לישראל 891-7221038",
        rows=[["תאריך", "סכום"], [datetime(2026, 9, 1), 12], [None]],
    )


def test_cells_are_addressed_one_based_like_excel():
    view = sheet()

    assert view.cell(1, 1) == "תאריך"
    assert view.cell(2, 2) == 12
    assert view.row_count == 3


def test_cells_outside_the_used_range_are_empty():
    view = sheet()

    assert view.cell(3, 5) is None
    assert view.cell(99, 1) is None


def test_zero_row_or_column_is_a_programming_error():
    with pytest.raises(ValueError):
        sheet().cell(0, 1)


def test_rows_are_immutable_tuples():
    view = sheet()

    assert isinstance(view.rows, tuple)
    assert view.row(1) == ("תאריך", "סכום")


def test_iter_rows_yields_row_numbers_from_the_requested_start():
    numbered = list(sheet().iter_rows(start_row=2))

    assert [number for number, _ in numbered] == [2, 3]


def test_repr_hides_sheet_names_and_cell_contents():
    text = repr(WorkbookView(sheets=[sheet()]))

    assert "891" not in text
    assert "תאריך" not in text


def test_workbook_finds_sheets_by_name():
    workbook = WorkbookView(sheets=[sheet()])

    assert workbook.sheet_names == ("לאומי לישראל 891-7221038",)
    assert workbook.find_sheet("missing") is None
    assert workbook.require_sheet("לאומי לישראל 891-7221038").row_count == 3


def test_missing_required_sheet_is_a_parse_error_without_leaking_the_name():
    with pytest.raises(ParseError) as caught:
        WorkbookView(sheets=[sheet()]).require_sheet("secret 123")

    assert "secret 123" not in str(caught.value)
