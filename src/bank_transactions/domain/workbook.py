"""Library-free immutable workbook views. Adapters (openpyxl, tests) build them eagerly."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.immutability import freeze_field

CellValue = str | int | float | Decimal | bool | datetime | date | None
Row = tuple[CellValue, ...]


@dataclass(frozen=True)
class SheetView:
    """Rows and columns are addressed one-based, like Excel. repr hides name and cells."""

    name: str = field(repr=False)
    rows: tuple[Row, ...] = field(repr=False)

    def __post_init__(self) -> None:
        freeze_field(self, "rows", lambda rows: tuple(tuple(row) for row in rows))

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def row(self, number: int) -> Row:
        self._require_positive(number)
        return self.rows[number - 1] if number <= self.row_count else ()

    def cell(self, row: int, column: int) -> CellValue:
        self._require_positive(column)
        values = self.row(row)
        return values[column - 1] if column <= len(values) else None

    def iter_rows(self, start_row: int = 1) -> Iterator[tuple[int, Row]]:
        self._require_positive(start_row)
        for number in range(start_row, self.row_count + 1):
            yield number, self.rows[number - 1]

    @staticmethod
    def _require_positive(number: int) -> None:
        if number < 1:
            raise ValueError("rows and columns are one-based")


@dataclass(frozen=True)
class WorkbookView:
    sheets: tuple[SheetView, ...] = field(repr=False)

    def __post_init__(self) -> None:
        freeze_field(self, "sheets", tuple)

    @property
    def sheet_names(self) -> tuple[str, ...]:
        return tuple(sheet.name for sheet in self.sheets)

    def find_sheet(self, name: str) -> SheetView | None:
        return next((sheet for sheet in self.sheets if sheet.name == name), None)

    def require_sheet(self, name: str) -> SheetView:
        sheet = self.find_sheet(name)
        if sheet is None:
            raise ParseError("a required sheet is missing")
        return sheet
