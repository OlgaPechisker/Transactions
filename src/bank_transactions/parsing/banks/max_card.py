"""Max credit-card export: a domestic sheet and a foreign-currency sheet with the same 16-column
table under a three-line banner."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from bank_transactions.domain.enums import (
    Channel,
    IssuerCode,
    ReviewReason,
    RowIssueCode,
    TransactionType,
)
from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.models import (
    CardHint,
    CardHintKey,
    ParsedInstallmentRef,
    StatementContext,
    StatementPeriod,
    StatementTotals,
)
from bank_transactions.domain.money import Money
from bank_transactions.domain.text import fold_text
from bank_transactions.domain.workbook import CellValue, Row, SheetView, WorkbookView
from bank_transactions.parsing.banks.tabular_base import (
    SheetPlan,
    SourceRow,
    TabularStatementParser,
)
from bank_transactions.parsing.column_map import ColumnMap, ColumnSpec
from bank_transactions.parsing.header_locator import HeaderLocator
from bank_transactions.parsing.normalizers.amounts import parse_money_text
from bank_transactions.parsing.normalizers.dates import month_bounds, parse_month_year
from bank_transactions.parsing.normalizers.text import (
    cell_text,
    normalize_last4,
    strip_bidi_marks,
)
from bank_transactions.parsing.row_reader import PendingRow, RowRejected
from bank_transactions.parsing.shared.draft import TransactionDraft
from bank_transactions.parsing.shared.fx_calculator import currencies_differ
from bank_transactions.parsing.shared.installment_fields import read_installment_fields

DOMESTIC_SHEET_NAME = "עסקאות במועד החיוב"
FOREIGN_SHEET_NAME = 'עסקאות חו"ל ומט"ח'
_FOOTER_MARKER = "סך הכל"
_PERIOD_RANGE = re.compile(r"(\d{4})-(\d{2})-(\d{2})-(\d{4})-(\d{2})-(\d{2})")
_CLUB_LABEL = "מועדון הנחות"
_CLUB_KEY_LABEL = "מפתח דיסקונט"
_NOTE_SEPARATOR = " | "

_TYPES = {
    fold_text("רגילה"): TransactionType.REGULAR,
    fold_text("תשלומים"): TransactionType.INSTALLMENTS,
    fold_text("חיוב חודשי"): TransactionType.RECURRING,
    fold_text("דחוי לחיוב החודשי"): TransactionType.RECURRING,
    fold_text("חיוב עסקות מיידי"): TransactionType.IMMEDIATE_CHARGE,
}
_CHANNELS = {
    fold_text("אינטרנט"): Channel.INTERNET,
    fold_text("טלפוני"): Channel.PHONE,
    fold_text("תשלום בנייד"): Channel.MOBILE,
}
_COLUMNS = ColumnMap(
    ColumnSpec("date", ("תאריך עסקה",)),
    ColumnSpec("merchant", ("שם בית העסק",)),
    ColumnSpec("card", ("4 ספרות אחרונות של כרטיס האשראי",)),
    ColumnSpec("type", ("סוג עסקה",)),
    ColumnSpec("billing", ("סכום חיוב",)),
    ColumnSpec("billing_currency", ("מטבע חיוב",)),
    ColumnSpec("original", ("סכום עסקה מקורי",)),
    ColumnSpec("original_currency", ("מטבע עסקה מקורי",)),
    ColumnSpec("charge_date", ("תאריך חיוב",)),
    ColumnSpec("category", ("קטגוריה",), required=False),
    ColumnSpec("notes", ("הערות",), required=False),
    ColumnSpec("club", (_CLUB_LABEL,), required=False),
    ColumnSpec("club_key", (_CLUB_KEY_LABEL,), required=False),
    ColumnSpec("channel", ("אופן ביצוע ההעסקה",), required=False),
    ColumnSpec("rate", ('שער המרה ממטבע מקור/התחשבנות לש"ח',), required=False),
)


@dataclass(frozen=True)
class _SheetKind:
    name: str
    foreign: bool


_DOMESTIC = _SheetKind(DOMESTIC_SHEET_NAME, foreign=False)
_FOREIGN = _SheetKind(FOREIGN_SHEET_NAME, foreign=True)
_KINDS = (_DOMESTIC, _FOREIGN)


@dataclass(frozen=True)
class _FoundSheet:
    position: int
    sheet: SheetView
    foreign: bool


class MaxParser(TabularStatementParser):
    """Reads a Max export holding two sheets, `עסקאות במועד החיוב` (domestic) and
    `עסקאות חו"ל ומט"ח` (foreign currency), each with the same table.

    `read_context` looks only at the card column (plus the banner and the footer totals): the
    export lists several cards, and the caller must see them before any transaction is parsed.
    Every foreign-sheet row is a foreign charge (even in the same currency); a domestic row is
    too when its original currency is known and differs from the billing currency. The
    declared totals are the footers of both sheets added per currency.

    `fee_keywords`, `fee_window_days` and `today` are described on `TabularStatementParser`.
    """

    issuer_code = IssuerCode.MAX
    _locator = HeaderLocator(_COLUMNS)

    def read_context(self, wb: WorkbookView) -> StatementContext:
        plans = self._sheet_plans(wb)
        cards = _card_keys(self.issuer_code, plans)
        return StatementContext(
            issuer_code=self.issuer_code,
            cards=tuple(CardHint(key) for key in cards),
            period=_read_period(plans[0]),
            declared_totals=_declared_totals(plans),
        )

    def _format_confidence(self, wb: WorkbookView) -> float:
        return sum(self._sheet_confidence(wb, kind) for kind in _KINDS)

    def _sheet_confidence(self, wb: WorkbookView, kind: _SheetKind) -> float:
        found = _find_sheet(wb, kind)
        if found is None:
            return 0.0
        return 0.5 if self._locator.find(found.sheet) is not None else 0.2

    def _sheet_plans(self, wb: WorkbookView) -> list[SheetPlan]:
        plans = []
        for kind in _KINDS:
            found = _find_sheet(wb, kind)
            if found is None:
                raise ParseError("a Max transactions sheet is missing")
            layout = self._locator.locate(found.sheet)
            plans.append(SheetPlan(found.position, found.sheet, layout, kind.foreign))
        return plans

    def _is_footer(self, plan: SheetPlan, cells: Row) -> bool:
        return _is_footer_cell(cells)

    def _read_draft(self, row: SourceRow, ctx: StatementContext) -> TransactionDraft:
        reader = row.reader
        card_key = _row_card(reader.raw("card"), ctx)
        transaction_date = reader.required_date("date")
        merchant = reader.required_text("merchant")
        billed = reader.amount("billing")
        if billed is None:
            raise PendingRow("the billed amount is blank")
        charge_date = reader.date("charge_date")
        if charge_date is None:
            raise PendingRow("the charge date is blank")
        billing_currency = reader.currency("billing_currency")
        original_currency = reader.currency("original_currency")
        billing = Money(billed, billing_currency)
        source_amount = reader.amount("original")
        original = None if source_amount is None else Money(source_amount, original_currency)
        note = reader.text("notes")
        transaction_type = _TYPES.get(fold_text(reader.text("type") or ""), TransactionType.REGULAR)
        installment: ParsedInstallmentRef | None = None
        reasons: frozenset[ReviewReason] = frozenset()
        if transaction_type is TransactionType.INSTALLMENTS:
            fields = read_installment_fields(note, original, billing)
            installment, original, reasons = fields.ref, fields.original, fields.review_reasons
        return TransactionDraft(
            card_key=card_key,
            location=row.location,
            transaction_date=transaction_date,
            charge_date=charge_date,
            billing=billing,
            merchant_name_raw=merchant,
            transaction_type=transaction_type,
            original=original,
            bank_category_raw=reader.verbatim("category"),
            channel=_CHANNELS.get(fold_text(reader.text("channel") or ""), Channel.UNKNOWN),
            notes=_notes(note, reader.text("club"), reader.text("club_key")),
            is_foreign=row.foreign_sheet or currencies_differ(billing_currency, original_currency),
            stated_fx_rate=reader.rate("rate"),
            installment=installment,
            review_reasons=reasons,
        )


def _find_sheet(wb: WorkbookView, kind: _SheetKind) -> _FoundSheet | None:
    wanted = fold_text(kind.name)
    for position, sheet in enumerate(wb.sheets, start=1):
        if fold_text(sheet.name) == wanted:
            return _FoundSheet(position, sheet, kind.foreign)
    return None


def _is_footer_cell(cells: Row) -> bool:
    first = cell_text(cells[0]) if cells else None
    return first is not None and fold_text(first) == fold_text(_FOOTER_MARKER)


def _data_rows(plan: SheetPlan) -> Iterator[tuple[int, Row]]:
    for number, cells in plan.sheet.iter_rows(plan.layout.header_row + 1):
        if _is_footer_cell(cells):
            return
        yield number, cells


def _card_keys(issuer: IssuerCode, plans: list[SheetPlan]) -> list[CardHintKey]:
    last4s: dict[str, None] = {}
    for plan in plans:
        for _, cells in _data_rows(plan):
            last4 = normalize_last4(plan.layout.cell(cells, "card"))
            if last4 is not None:
                last4s.setdefault(last4)
    return [CardHintKey(issuer, last4) for last4 in last4s]


def _row_card(value: CellValue, ctx: StatementContext) -> CardHintKey:
    last4 = normalize_last4(value)
    if last4 is None:
        raise RowRejected(RowIssueCode.MISSING_REQUIRED_VALUE, "column 'card' is empty")
    for hint in ctx.cards:
        if hint.key.last4 == last4:
            return hint.key
    raise RowRejected(RowIssueCode.MALFORMED_ROW, "the card is not part of this statement")


def _read_period(plan: SheetPlan) -> StatementPeriod | None:
    for number, cells in plan.sheet.iter_rows():
        if number >= plan.layout.header_row:
            return None
        for cell in cells:
            period = _period_of(cell)
            if period is not None:
                return period
    return None


def _period_of(cell: CellValue) -> StatementPeriod | None:
    if not isinstance(cell, str):
        return None
    month = parse_month_year(cell)
    if month is not None:
        return StatementPeriod(*month_bounds(*month))
    match = _PERIOD_RANGE.fullmatch(strip_bidi_marks(cell).strip())
    if match is None:
        return None
    parts = [int(part) for part in match.groups()]
    try:
        return StatementPeriod(date(*parts[:3]), date(*parts[3:]))
    except ValueError:
        return None


def _declared_totals(plans: list[SheetPlan]) -> StatementTotals:
    footers = [_footer_total(plan) for plan in plans]
    if any(money is None for money in footers):
        return StatementTotals()
    return StatementTotals.of(*(money for money in footers if money is not None))


def _footer_total(plan: SheetPlan) -> Money | None:
    for number, cells in plan.sheet.iter_rows(plan.layout.header_row + 1):
        if _is_footer_cell(cells):
            money = parse_money_text(plan.sheet.cell(number + 1, 1))
            return money if money is not None and money.currency is not None else None
    return None


def _notes(note: str | None, club: str | None, club_key: str | None) -> str | None:
    parts = [
        note,
        None if club is None else f"{_CLUB_LABEL}: {club}",
        None if club_key is None else f"{_CLUB_KEY_LABEL}: {club_key}",
    ]
    joined = _NOTE_SEPARATOR.join(part for part in parts if part is not None)
    return joined or None
