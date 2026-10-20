"""Typed access to the cells of one data row, turning bad values into row-level rejections."""

from datetime import date as Day  # noqa: N812 - RowReader.date would shadow the type
from decimal import Decimal

from bank_transactions.domain.enums import IssueSeverity, RowIssueCode
from bank_transactions.domain.money import Currency
from bank_transactions.domain.workbook import CellValue, Row
from bank_transactions.parsing.column_map import ColumnLayout
from bank_transactions.parsing.normalizers.amounts import (
    InvalidAmountError,
    parse_amount,
    parse_rate,
)
from bank_transactions.parsing.normalizers.currency import parse_currency
from bank_transactions.parsing.normalizers.dates import (
    DEFAULT_TWO_DIGIT_YEARS_BACK,
    InvalidDateError,
    parse_date,
)
from bank_transactions.parsing.normalizers.text import cell_text


class RowRejected(Exception):  # noqa: N818 - a signal about a row, not an error condition
    """The row cannot become a transaction. The message names columns, never cell contents."""

    def __init__(
        self,
        code: RowIssueCode,
        message: str,
        severity: IssueSeverity = IssueSeverity.ERROR,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.severity = severity


class PendingRow(RowRejected):
    """A row the issuer has not charged yet (blank billed amount or charge date)."""

    def __init__(self, message: str) -> None:
        super().__init__(RowIssueCode.PENDING_TRANSACTION, message, IssueSeverity.INFO)


class RowReader:
    """Reads logical columns of one row. `date_anchor` and `two_digit_years_back` resolve
    two-digit years (see `normalizers.dates.parse_date`)."""

    def __init__(
        self,
        layout: ColumnLayout,
        cells: Row,
        date_anchor: Day,
        two_digit_years_back: int = DEFAULT_TWO_DIGIT_YEARS_BACK,
    ) -> None:
        self._layout = layout
        self._cells = cells
        self._anchor = date_anchor
        self._years_back = two_digit_years_back

    def has_column(self, key: str) -> bool:
        return self._layout.has(key)

    def raw(self, key: str) -> CellValue:
        return self._layout.cell(self._cells, key)

    def text(self, key: str) -> str | None:
        return cell_text(self.raw(key))

    def verbatim(self, key: str) -> str | None:
        """The cell as written (whitespace kept); None only for blank cells."""
        value = self.raw(key)
        return None if cell_text(value) is None else str(value)

    def identifier(self, key: str) -> str | None:
        """A reference number as text; Excel's float form of a whole number loses its `.0`."""
        value = self.raw(key)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return cell_text(value)

    def required_text(self, key: str) -> str:
        value = self.verbatim(key)
        if value is None:
            raise self._missing(key)
        return value

    def amount(self, key: str) -> Decimal | None:
        try:
            return parse_amount(self.raw(key))
        except InvalidAmountError as error:
            raise RowRejected(
                RowIssueCode.INVALID_AMOUNT, f"column '{key}' is not a valid amount"
            ) from error

    def required_amount(self, key: str) -> Decimal:
        value = self.amount(key)
        if value is None:
            raise self._missing(key)
        return value

    def date(self, key: str) -> Day | None:
        try:
            return parse_date(self.raw(key), self._anchor, two_digit_years_back=self._years_back)
        except InvalidDateError as error:
            raise RowRejected(
                RowIssueCode.INVALID_DATE, f"column '{key}' is not a valid date"
            ) from error

    def required_date(self, key: str) -> Day:
        value = self.date(key)
        if value is None:
            raise self._missing(key)
        return value

    def currency(self, key: str) -> Currency | None:
        return parse_currency(self.raw(key))

    def rate(self, key: str) -> Decimal | None:
        """The stated FX rate is informational, so an unreadable one is ignored, not fatal."""
        try:
            return parse_rate(self.raw(key))
        except InvalidAmountError:
            return None

    @staticmethod
    def _missing(key: str) -> RowRejected:
        return RowRejected(RowIssueCode.MISSING_REQUIRED_VALUE, f"column '{key}' is empty")
