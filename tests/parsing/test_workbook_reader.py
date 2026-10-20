import io
import re
import warnings
import zipfile
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


def test_corrupt_archives_and_corrupt_xml_parts_raise_a_sanitised_parse_error():
    def build(book):
        book.active.append(["secret-1234"])

    good = workbook_stream(build).getvalue()
    damaged = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as source, zipfile.ZipFile(damaged, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            target.writestr(
                item, b"<broken" if item.filename.startswith("xl/worksheets/") else data
            )
    damaged.seek(0)

    with pytest.raises(ParseError) as xml_error:
        ExcelWorkbookReader().read_stream(damaged)
    with pytest.raises(ParseError) as zip_error:
        ExcelWorkbookReader().read_stream(io.BytesIO(good[: len(good) // 2]))

    assert "1234" not in str(xml_error.value) + str(zip_error.value)


EMPTY_CONTENT_TYPES = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
)


def _workbook_with_part(part_name, transform):
    def build(book):
        book.active.append(["secret-1234"])

    good = workbook_stream(build).getvalue()
    damaged = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as source, zipfile.ZipFile(damaged, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            target.writestr(item, transform(data) if item.filename == part_name else data)
    return damaged.getvalue()


DAMAGED_WORKBOOKS = {
    "non-numeric font size": _workbook_with_part(
        "xl/styles.xml", lambda data: re.sub(rb'<sz val="11"\s*/>', b'<sz val="abc"/>', data)
    ),
    "content types without any part": _workbook_with_part(
        "[Content_Types].xml", lambda data: EMPTY_CONTENT_TYPES
    ),
}


@pytest.mark.parametrize("damaged", DAMAGED_WORKBOOKS.values(), ids=DAMAGED_WORKBOOKS.keys())
def test_workbooks_with_unreadable_parts_raise_a_sanitised_parse_error_from_a_stream(damaged):
    with pytest.raises(ParseError) as raised:
        ExcelWorkbookReader().read_stream(io.BytesIO(damaged))

    assert "1234" not in str(raised.value)
    assert "abc" not in str(raised.value)


@pytest.mark.parametrize("damaged", DAMAGED_WORKBOOKS.values(), ids=DAMAGED_WORKBOOKS.keys())
def test_workbooks_with_unreadable_parts_raise_a_sanitised_parse_error_from_a_path(
    tmp_path, damaged
):
    path = tmp_path / "secret-1234.xlsx"
    path.write_bytes(damaged)

    with pytest.raises(ParseError) as raised:
        ExcelWorkbookReader().open(path)

    assert "1234" not in str(raised.value)
    assert "abc" not in str(raised.value)


@pytest.mark.parametrize("failure", [RuntimeError("bug"), MemoryError()])
def test_unexpected_failures_are_not_reported_as_bad_input(monkeypatch, failure):
    def explode(*args, **kwargs):
        raise failure

    monkeypatch.setattr("bank_transactions.parsing.workbook.openpyxl.load_workbook", explode)

    with pytest.raises(type(failure)):
        ExcelWorkbookReader().read_stream(io.BytesIO(b"x"))


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
