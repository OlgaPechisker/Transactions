"""openpyxl adapter: reads a whole workbook eagerly into plain immutable views."""

import io
import warnings
import zipfile
import zlib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO
from xml.etree import ElementTree

import openpyxl  # type: ignore[import-untyped]
from openpyxl.utils.exceptions import InvalidFileException  # type: ignore[import-untyped]

from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.workbook import CellValue, Row, SheetView, WorkbookView
from bank_transactions.ports.workbook_reader import WorkbookReader

_PLAIN_CELL_TYPES = (str, int, float, Decimal, bool, datetime, date)
# What openpyxl raises for damaged input: a bad or truncated archive (BadZipFile, zlib.error,
# EOFError), an unsupported compression method (NotImplementedError, as zipfile reports it), a
# missing package part (KeyError), malformed XML, unparsable cell content (ValueError), a
# mistyped style attribute (TypeError), a package without a workbook part (OSError) or a
# rejected file type.
# Anything else is a defect or a resource failure and must propagate.
_BAD_INPUT_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    NotImplementedError,
    ElementTree.ParseError,
    InvalidFileException,
    KeyError,
    ValueError,
    TypeError,
    OSError,
)


class ExcelWorkbookReader(WorkbookReader):
    """Reads `.xlsx` files. Cached formula results are used; number formats are not exposed."""

    def open(self, path: Path) -> WorkbookView:
        try:
            with path.open("rb") as stream:
                return self.read_stream(stream)
        except OSError:
            raise ParseError("the workbook file could not be opened") from None

    def open_bytes(self, data: bytes) -> WorkbookView:
        return self.read_stream(io.BytesIO(data))

    def read_stream(self, stream: BinaryIO) -> WorkbookView:
        book = self._load(stream)
        try:
            return WorkbookView(tuple(self._sheet_view(sheet) for sheet in book.worksheets))
        finally:
            book.close()

    @staticmethod
    def _load(stream: BinaryIO) -> Any:
        try:
            with warnings.catch_warnings():
                # Some issuer exports omit the default style; openpyxl warns once and recovers.
                warnings.filterwarnings(
                    "ignore",
                    message="Workbook contains no default style",
                    category=UserWarning,
                )
                return openpyxl.load_workbook(stream, data_only=True)
        except _BAD_INPUT_ERRORS:
            raise ParseError("the file is not a readable Excel workbook") from None

    @staticmethod
    def _sheet_view(sheet: Any) -> SheetView:
        rows = [
            tuple(_plain_value(value) for value in row) for row in sheet.iter_rows(values_only=True)
        ]
        while rows and _is_blank_row(rows[-1]):
            rows.pop()
        return SheetView(sheet.title, tuple(rows))


def _plain_value(value: object) -> CellValue:
    if value is None or isinstance(value, _PLAIN_CELL_TYPES):
        return value
    return str(value)


def _is_blank_row(row: Row) -> bool:
    return all(value is None or (isinstance(value, str) and not value.strip()) for value in row)
