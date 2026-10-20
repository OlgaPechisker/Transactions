import io
import warnings
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from bank_transactions.domain.errors import ParseError
from bank_transactions.parsing.workbook import ExcelWorkbookReader

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def workbook_stream(build):
    book = openpyxl.Workbook()
    build(book)
    stream = io.BytesIO()
    book.save(stream)
    stream.seek(0)
    return stream


def test_sheets_keep_their_order_names_and_cell_types():
    def build(book):
        first = book.active
        first.title = "פירוט עסקאות"
        first.append(["תאריך", "סכום", None, "טקסט"])
        first.append([datetime(2026, 9, 4), 245.83, None, "  raw text "])
        book.create_sheet('עסקאות חו"ל').append(["x"])

    view = ExcelWorkbookReader().read_stream(workbook_stream(build))

    assert view.sheet_names == ("פירוט עסקאות", 'עסקאות חו"ל')
    sheet = view.sheets[0]
    assert sheet.row(2) == (datetime(2026, 9, 4), 245.83, None, "  raw text ")
    assert sheet.cell(1, 4) == "טקסט"


def test_trailing_blank_rows_are_not_part_of_the_view():
    def build(book):
        sheet = book.active
        sheet.append(["a"])
        sheet.append([None, None])
        sheet.append([])
        sheet["B9"].number_format = "0.00"

    view = ExcelWorkbookReader().read_stream(workbook_stream(build))

    assert view.sheets[0].row_count == 1


def test_blank_rows_between_data_rows_are_kept_so_row_numbers_match_excel():
    def build(book):
        sheet = book.active
        sheet.append(["a"])
        sheet.append([])
        sheet.append(["b"])

    view = ExcelWorkbookReader().read_stream(workbook_stream(build))

    assert view.sheets[0].cell(3, 1) == "b"


def test_bytes_that_are_not_a_workbook_raise_a_sanitised_parse_error():
    with pytest.raises(ParseError) as raised:
        ExcelWorkbookReader().read_stream(io.BytesIO(b"definitely not a workbook"))

    assert "definitely" not in str(raised.value)


def test_missing_and_non_workbook_paths_raise_parse_error_without_the_path():
    missing = Path("no-such-folder") / "secret-1234.xlsx"
    with pytest.raises(ParseError) as raised:
        ExcelWorkbookReader().open(missing)
    assert "1234" not in str(raised.value)

    with pytest.raises(ParseError):
        ExcelWorkbookReader().open(Path(__file__))


def test_example_workbooks_open_without_leaking_openpyxl_style_warnings():
    example = next(EXAMPLES.glob("*.xlsx"))

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        view = ExcelWorkbookReader().open(example)

    assert view.sheets


def test_the_reader_does_not_hide_unrelated_warnings():
    def build(book):
        book.active.append(["a"])

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("unrelated", UserWarning, stacklevel=1)
        ExcelWorkbookReader().read_stream(workbook_stream(build))

    assert [str(item.message) for item in caught] == ["unrelated"]
