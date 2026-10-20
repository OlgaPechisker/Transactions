"""Cal (Visa) statement: one sheet per account, a title naming bank, account and card, a charge
banner and a multi-line-header table."""

import re
from dataclasses import dataclass
from datetime import date

from bank_transactions.domain.enums import IssuerCode, ReviewReason, TransactionType
from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.models import (
    AccountHint,
    CardHint,
    CardHintKey,
    ParsedInstallmentRef,
    StatementContext,
    StatementTotals,
)
from bank_transactions.domain.money import Currency, Money
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
from bank_transactions.parsing.normalizers.text import cell_text, strip_bidi_marks
from bank_transactions.parsing.row_reader import PendingRow
from bank_transactions.parsing.shared.draft import TransactionDraft
from bank_transactions.parsing.shared.installment_fields import read_installment_fields

LEUMI_BANK_CODE = "LEUMI"
_LEUMI_NAME = "לאומי"
_TITLE = re.compile(
    r"פירוט עסקאות לחשבון (?P<bank>.+?) (?P<account>\d[\d-]*) לכרטיס (?P<label>.+?) "
    r"המסתיים ב-(?P<last4>\d{4})"
)
_BANNER = re.compile(
    r"עסקאות לחיוב ב-(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4}):\s*(?P<total>.+)"
)
_TYPES = {
    fold_text("רגילה"): TransactionType.REGULAR,
    fold_text("תשלומים"): TransactionType.INSTALLMENTS,
}
_COLUMNS = ColumnMap(
    ColumnSpec("date", ("תאריך עסקה",)),
    ColumnSpec("merchant", ("שם בית עסק",)),
    ColumnSpec("amount", ("סכום עסקה",)),
    ColumnSpec("charge", ("סכום חיוב",)),
    ColumnSpec("type", ("סוג עסקה",), required=False),
    ColumnSpec("category", ("ענף",), required=False),
    ColumnSpec("notes", ("הערות",), required=False),
)


@dataclass(frozen=True)
class _Title:
    bank: str
    account: str
    card_label: str
    last4: str


@dataclass(frozen=True)
class _Banner:
    charge_date: date
    total: Money


class CalParser(TabularStatementParser):
    """Reads one Cal statement sheet.

    The title gives the bank (Leumi becomes bank code LEUMI), the account and the card; the
    banner gives the charge date and the total with its currency, which every row inherits.
    A row with a blank charge amount is pending; its transaction amount is the original. An
    installment row's amount is the whole purchase, its charge the monthly instalment.
    """

    issuer_code = IssuerCode.CAL
    _locator = HeaderLocator(_COLUMNS)

    def read_context(self, wb: WorkbookView) -> StatementContext:
        sheet = _first_sheet(wb)
        layout = self._locator.locate(sheet)
        title = _read_title(sheet)
        banner = _read_banner(sheet, layout.header_row)
        label = None if title.last4 in title.card_label else title.card_label
        return StatementContext(
            issuer_code=self.issuer_code,
            cards=(CardHint(CardHintKey(self.issuer_code, title.last4), label),),
            account=AccountHint(
                bank_code=LEUMI_BANK_CODE if _LEUMI_NAME in title.bank else None,
                account_number=title.account,
                display_name=title.bank,
            ),
            charge_date=banner.charge_date,
            declared_totals=StatementTotals((banner.total,)),
        )

    def _format_confidence(self, wb: WorkbookView) -> float:
        sheet = _first_sheet(wb)
        titled = 0.4 if _find_title(sheet) is not None else 0.0
        return titled + (0.6 if self._locator.find(sheet) is not None else 0.0)

    def _sheet_plans(self, wb: WorkbookView) -> list[SheetPlan]:
        sheet = _first_sheet(wb)
        return [SheetPlan(1, sheet, self._locator.locate(sheet))]

    def _is_footer(self, plan: SheetPlan, cells: Row) -> bool:
        first, rest = cell_text(cells[0]), cells[1:]
        return first is not None and all(cell_text(cell) is None for cell in rest)

    def _read_draft(self, row: SourceRow, ctx: StatementContext) -> TransactionDraft:
        reader = row.reader
        transaction_date = reader.required_date("date")
        merchant = reader.required_text("merchant")
        charged = reader.amount("charge")
        if charged is None:
            raise PendingRow("the charge amount is blank")
        currency = _statement_currency(ctx)
        billing = Money(charged, currency)
        source_amount = reader.amount("amount")
        original = None if source_amount is None else Money(source_amount, currency)
        notes = reader.text("notes")
        transaction_type = _TYPES.get(fold_text(reader.text("type") or ""), TransactionType.REGULAR)
        installment: ParsedInstallmentRef | None = None
        reasons: frozenset[ReviewReason] = frozenset()
        if transaction_type is TransactionType.INSTALLMENTS:
            fields = read_installment_fields(notes, original, billing)
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
            bank_category_raw=reader.verbatim("category"),
            notes=notes,
            installment=installment,
            review_reasons=reasons,
        )


def _first_sheet(wb: WorkbookView) -> SheetView:
    if not wb.sheets:
        raise ParseError("the workbook has no sheets")
    return wb.sheets[0]


def _sheet_texts(sheet: SheetView, before_row: int) -> list[str]:
    texts = (
        cell_text(cell) for number, row in sheet.iter_rows() if number < before_row for cell in row
    )
    return [" ".join(strip_bidi_marks(text).split()) for text in texts if text is not None]


def _find_title(sheet: SheetView) -> _Title | None:
    for text in _sheet_texts(sheet, before_row=sheet.row_count + 1):
        match = _TITLE.fullmatch(text)
        if match is not None:
            return _Title(*match.group("bank", "account", "label", "last4"))
    return None


def _read_title(sheet: SheetView) -> _Title:
    title = _find_title(sheet)
    if title is None:
        raise ParseError("the statement title is missing")
    return title


def _read_banner(sheet: SheetView, header_row: int) -> _Banner:
    for text in _sheet_texts(sheet, header_row):
        match = _BANNER.fullmatch(text)
        if match is not None:
            return _banner_from(match)
    raise ParseError("the charge banner is missing")


def _banner_from(match: re.Match[str]) -> _Banner:
    total = parse_money_text(match.group("total"))
    if total is None or total.currency is None:
        raise ParseError("the banner total has no recognisable currency")
    try:
        charge_date = date(*(int(match.group(part)) for part in ("year", "month", "day")))
    except ValueError as error:
        raise ParseError("the banner charge date is not a calendar date") from error
    return _Banner(charge_date, total)


def _statement_currency(ctx: StatementContext) -> Currency:
    if not ctx.declared_totals.amounts or ctx.declared_totals.amounts[0].currency is None:
        raise ParseError("the statement currency is unknown")
    return ctx.declared_totals.amounts[0].currency


def _charge_date(ctx: StatementContext) -> date:
    if ctx.charge_date is None:
        raise ParseError("the statement charge date is unknown")
    return ctx.charge_date
