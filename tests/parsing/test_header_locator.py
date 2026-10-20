import pytest

from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.workbook import SheetView
from bank_transactions.parsing.column_map import ColumnMap, ColumnSpec
from bank_transactions.parsing.header_locator import HeaderLocator

COLUMNS = ColumnMap(
    ColumnSpec("date", ("תאריך עסקה", "תאריך רכישה")),
    ColumnSpec("merchant", ("שם בית עסק", "שם בית העסק")),
    ColumnSpec("billing", ("סכום חיוב",)),
    ColumnSpec("notes", ("הערות",), required=False),
)


def sheet(*rows):
    return SheetView("sheet", tuple(rows))


def test_header_is_found_below_a_banner_and_columns_resolve_by_alias():
    view = sheet(
        ("banner",),
        (None, None),
        ("תאריך רכישה", "שם בית עסק", "סכום חיוב", "הערות"),
        ("09.02.26", "x", 5, "n"),
    )

    layout = HeaderLocator(COLUMNS).locate(view)

    assert layout.header_row == 3
    assert [layout.column(key) for key in ("date", "merchant", "billing", "notes")] == [1, 2, 3, 4]


def test_multiline_headers_and_extra_unknown_columns_are_tolerated():
    view = sheet(("תאריך\nעסקה", "זבל", "שם בית\nהעסק", "סכום\nחיוב"))

    layout = HeaderLocator(COLUMNS).locate(view)

    assert (layout.column("date"), layout.column("merchant"), layout.column("billing")) == (1, 3, 4)
    assert not layout.has("notes")


def test_cell_reads_a_value_by_key_and_none_for_an_absent_optional_column():
    view = sheet(("תאריך עסקה", "שם בית עסק", "סכום חיוב"))
    layout = HeaderLocator(COLUMNS).locate(view)

    assert layout.cell(("d", "m", 7), "billing") == 7
    assert layout.cell(("d", "m"), "billing") is None
    assert layout.cell(("d", "m", 7), "notes") is None


def test_the_first_matching_column_wins_over_a_duplicate_header():
    view = sheet(("תאריך עסקה", "שם בית עסק", "סכום חיוב", "סכום חיוב"))

    assert HeaderLocator(COLUMNS).locate(view).column("billing") == 3


def test_a_missing_required_column_is_a_structural_error_naming_the_column_key_only():
    view = sheet(("תאריך עסקה", "שם בית עסק"))

    with pytest.raises(ParseError, match="billing"):
        HeaderLocator(COLUMNS).locate(view)


def test_a_sheet_without_any_header_is_a_structural_error():
    with pytest.raises(ParseError, match="header"):
        HeaderLocator(COLUMNS).locate(sheet(("nothing",), (1, 2, 3)))


def test_headers_below_the_scan_limit_are_not_found():
    rows = [("filler",)] * 5 + [("תאריך עסקה", "שם בית עסק", "סכום חיוב")]

    assert HeaderLocator(COLUMNS, max_scan_rows=5).find(sheet(*rows)) is None
    assert HeaderLocator(COLUMNS, max_scan_rows=6).find(sheet(*rows)) is not None


def test_find_returns_none_instead_of_raising_for_sniffing():
    assert HeaderLocator(COLUMNS).find(sheet(("x",))) is None


def test_the_row_with_all_required_columns_beats_an_earlier_partial_match():
    view = sheet(("תאריך עסקה",), ("תאריך עסקה", "שם בית עסק", "סכום חיוב"))

    assert HeaderLocator(COLUMNS).locate(view).header_row == 2


def test_duplicate_column_keys_are_rejected_when_the_map_is_built():
    with pytest.raises(ValueError, match="duplicate"):
        ColumnMap(ColumnSpec("a", ("x",)), ColumnSpec("a", ("y",)))
