"""Locates the header row of a sheet whose banner height varies."""

from collections.abc import Iterator

from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.workbook import Row, SheetView
from bank_transactions.parsing.column_map import ColumnLayout, ColumnMap

DEFAULT_SCAN_ROWS = 30


class HeaderLocator:
    def __init__(self, column_map: ColumnMap, max_scan_rows: int = DEFAULT_SCAN_ROWS) -> None:
        self._column_map = column_map
        self._max_scan_rows = max_scan_rows

    def find(self, sheet: SheetView) -> ColumnLayout | None:
        """The layout of the first row naming every required column, else None."""
        for number, row in self._scan(sheet):
            matched = self._column_map.match(row)
            if set(self._column_map.required_keys) <= matched.keys():
                return ColumnLayout(number, matched)
        return None

    def locate(self, sheet: SheetView) -> ColumnLayout:
        """Like `find`, but a structural ParseError says whether the header or columns failed."""
        layout = self.find(sheet)
        if layout is not None:
            return layout
        missing = self._missing_from_best_row(sheet)
        if missing is None:
            raise ParseError("header row not found")
        raise ParseError(f"required columns missing: {', '.join(missing)}")

    def _scan(self, sheet: SheetView) -> Iterator[tuple[int, Row]]:
        last = min(self._max_scan_rows, sheet.row_count)
        return ((number, sheet.row(number)) for number in range(1, last + 1))

    def _missing_from_best_row(self, sheet: SheetView) -> tuple[str, ...] | None:
        required = self._column_map.required_keys
        best: tuple[str, ...] | None = None
        best_found = 0
        for _, row in self._scan(sheet):
            matched = self._column_map.match(row)
            found = sum(1 for key in required if key in matched)
            if found > best_found:
                best_found = found
                best = tuple(key for key in required if key not in matched)
        return best
