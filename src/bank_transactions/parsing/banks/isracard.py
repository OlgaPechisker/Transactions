"""Isracard credit-card statement (one sheet `פירוט עסקאות`, banner above a header row)."""

import re
from dataclasses import dataclass
from datetime import date

from bank_transactions.domain.enums import IssuerCode, ReviewReason, TransactionType
from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.models import (
    CardHint,
    CardHintKey,
    OwnerHint,
    ParsedInstallmentRef,
    StatementContext,
    StatementPeriod,
    StatementTotals,
)
from bank_transactions.domain.money import ILS, Currency, Money
from bank_transactions.domain.text import fold_text
from bank_transactions.domain.workbook import Row, SheetView, WorkbookView
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
    parse_installment_note,
    strip_bidi_marks,
)
from bank_transactions.parsing.row_reader import PendingRow
from bank_transactions.parsing.shared.draft import TransactionDraft
from bank_transactions.parsing.shared.fx_calculator import currencies_differ
from bank_transactions.parsing.shared.installment_fields import read_installment_fields

SHEET_NAME = "פירוט עסקאות"
FOREIGN_NOTE = 'חו"ל'
_FOOTER_MARKER = 'סה"כ'
_CARD_BANNER = re.compile(r"(?P<label>.*?)\s*-\s*(?P<last4>\d{4})")
_OWNER_PREFIX = "על שם "
_CHARGE_BANNER = re.compile(r"לחיוב ב-(?P<day>\d{1,2})\.(?P<month>\d{1,2})")

_COLUMNS = ColumnMap(
    ColumnSpec("date", ("תאריך רכישה",)),
    ColumnSpec("merchant", ("שם בית עסק",)),
    ColumnSpec("original", ("סכום עסקה",)),
    ColumnSpec("original_currency", ("מטבע עסקה",), required=False),
    ColumnSpec("billing", ("סכום חיוב",)),
    ColumnSpec("billing_currency", ("מטבע חיוב",)),
    ColumnSpec("voucher", ("מס' שובר",), required=False),
    ColumnSpec("details", ("פירוט נוסף",), required=False),
)


@dataclass(frozen=True)
class _Banner:
    card_label: str | None
    last4: str
    owner: str | None
    statement_month: tuple[int, int]
    charge_date: date
    totals: StatementTotals


class IsracardParser(TabularStatementParser):
    """Reads one Isracard card statement.

    The banner gives the card (label and last four digits), the owner, the statement month, the
    charge date ("לחיוב ב-dd.mm") and the per-currency totals. Rows carry the purchase date
    (dd.MM.yy), merchant, purchase and billed amounts with currencies, a voucher number and
    free-text details. A row is foreign when its details say so (`חו"ל`) or a currency other
    than ILS, or two different currencies, appear on it.
    """

    issuer_code = IssuerCode.ISRACARD
    _locator = HeaderLocator(_COLUMNS)

    def read_context(self, wb: WorkbookView) -> StatementContext:
        _, sheet = self._require_sheet(wb)
        banner = _read_banner(sheet, self._locator.locate(sheet).header_row)
        year, month = banner.statement_month
        start, end = month_bounds(year, month)
        label = banner.card_label
        return StatementContext(
            issuer_code=self.issuer_code,
            cards=(CardHint(CardHintKey(self.issuer_code, banner.last4), label),),
            owner=OwnerHint(banner.owner) if banner.owner else None,
            period=StatementPeriod(start, end),
            charge_date=banner.charge_date,
            declared_totals=banner.totals,
        )

    def _format_confidence(self, wb: WorkbookView) -> float:
        found = _find_sheet(wb)
        if found is None:
            return 0.0
        _, sheet = found
        layout = self._locator.find(sheet)
        if layout is None:
            return 0.2
        banner_card = _find_card(sheet, layout.header_row) is not None
        return 0.8 + (0.2 if banner_card else 0.0)

    def _sheet_plans(self, wb: WorkbookView) -> list[SheetPlan]:
        position, sheet = self._require_sheet(wb)
        return [SheetPlan(position, sheet, self._locator.locate(sheet))]

    def _is_footer(self, plan: SheetPlan, cells: Row) -> bool:
        merchant = cell_text(plan.layout.cell(cells, "merchant"))
        return merchant is not None and fold_text(merchant).startswith(fold_text(_FOOTER_MARKER))

    def _read_draft(self, row: SourceRow, ctx: StatementContext) -> TransactionDraft:
        reader = row.reader
        transaction_date = reader.required_date("date")
        merchant = reader.required_text("merchant")
        billed = reader.amount("billing")
        if billed is None:
            raise PendingRow("the billed amount is blank")
        billing_currency = reader.currency("billing_currency")
        original_currency = reader.currency("original_currency")
        billing = Money(billed, billing_currency)
        purchased = reader.amount("original")
        original = None if purchased is None else Money(purchased, original_currency)
        details = reader.text("details")
        installment: ParsedInstallmentRef | None = None
        reasons: frozenset[ReviewReason] = frozenset()
        transaction_type = TransactionType.REGULAR
        if parse_installment_note(details) is not None:
            fields = read_installment_fields(details, original, billing)
            transaction_type = TransactionType.INSTALLMENTS
            installment, original, reasons = fields.ref, fields.original, fields.review_reasons
        return TransactionDraft(
            card_key=self._only_card(ctx),
            location=row.location,
            transaction_date=transaction_date,
            charge_date=_charge_date(ctx),
            billing=billing,
            merchant_name_raw=merchant,
            transaction_type=transaction_type,
            original=original,
            notes=details,
            issuer_reference=reader.identifier("voucher"),
            installment=installment,
            is_foreign=_is_foreign(details, billing_currency, original_currency),
            review_reasons=reasons,
        )

    def _require_sheet(self, wb: WorkbookView) -> tuple[int, SheetView]:
        found = _find_sheet(wb)
        if found is None:
            raise ParseError("the Isracard transactions sheet is missing")
        return found


def _find_sheet(wb: WorkbookView) -> tuple[int, SheetView] | None:
    wanted = fold_text(SHEET_NAME)
    for position, sheet in enumerate(wb.sheets, start=1):
        if fold_text(sheet.name) == wanted:
            return position, sheet
    return None


def _charge_date(ctx: StatementContext) -> date:
    if ctx.charge_date is None:
        raise ParseError("the statement charge date is unknown")
    return ctx.charge_date


def _is_foreign(details: str | None, billing: Currency | None, original: Currency | None) -> bool:
    noted = details is not None and fold_text(FOREIGN_NOTE) in fold_text(details)
    other_currency = any(currency not in (None, ILS) for currency in (billing, original))
    return noted or other_currency or currencies_differ(billing, original)


def _banner_cells(sheet: SheetView, header_row: int) -> list[str]:
    texts = (
        cell_text(cell) for number, row in sheet.iter_rows() if number < header_row for cell in row
    )
    return [strip_bidi_marks(text).strip() for text in texts if text is not None]


def _find_card(sheet: SheetView, header_row: int) -> tuple[str, str] | None:
    for text in _banner_cells(sheet, header_row):
        match = _CARD_BANNER.fullmatch(text)
        if match is not None:
            return match.group("label").strip(), match.group("last4")
    return None


def _read_banner(sheet: SheetView, header_row: int) -> _Banner:
    texts = _banner_cells(sheet, header_row)
    card = _find_card(sheet, header_row)
    if card is None:
        raise ParseError("the card banner is missing")
    month = next((m for m in map(parse_month_year, texts) if m is not None), None)
    if month is None:
        raise ParseError("the statement month is missing")
    return _Banner(
        card_label=card[0] or None,
        last4=card[1],
        owner=_owner(texts),
        statement_month=month,
        charge_date=_banner_charge_date(texts, month),
        totals=_banner_totals(texts),
    )


def _owner(texts: list[str]) -> str | None:
    named = next((t for t in texts if t.startswith(_OWNER_PREFIX)), None)
    return None if named is None else named.removeprefix(_OWNER_PREFIX).strip() or None


def _banner_charge_date(texts: list[str], statement_month: tuple[int, int]) -> date:
    for text in texts:
        match = _CHARGE_BANNER.search(text)
        if match is None:
            continue
        year, month = statement_month
        charge_month = int(match.group("month"))
        try:
            return date(
                year + (1 if charge_month < month else 0), charge_month, int(match.group("day"))
            )
        except ValueError as error:
            raise ParseError("the statement charge date is not a calendar date") from error
    raise ParseError("the statement charge date is missing")


def _banner_totals(texts: list[str]) -> StatementTotals:
    by_currency: dict[object, Money] = {}
    for text in texts:
        money = parse_money_text(text)
        if money is not None and money.currency is not None:
            by_currency.setdefault(money.currency, money)
    return StatementTotals(tuple(by_currency.values()))
