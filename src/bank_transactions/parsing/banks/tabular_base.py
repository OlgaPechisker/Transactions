"""Template for header-driven statement parsers: sheet plans in, source-ordered rows through a
shared pipeline (fee folding, installment plans, FX detail) out. Issuer subclasses only say how
to find their sheets, recognise their footer and turn one row into a draft."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from bank_transactions.domain.enums import IssueSeverity, ReviewReason, RowIssueCode
from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.issues import RowIssue, SourceLocation
from bank_transactions.domain.models import (
    CardHintKey,
    ParsedInstallmentPlan,
    ParsedTransaction,
    StatementContext,
    StatementParseResult,
    StatementTotals,
)
from bank_transactions.domain.workbook import Row, SheetView, WorkbookView
from bank_transactions.parsing.column_map import ColumnLayout
from bank_transactions.parsing.interfaces import StatementParser
from bank_transactions.parsing.normalizers.dates import (
    DEFAULT_TWO_DIGIT_YEARS_BACK,
    check_two_digit_years_back,
)
from bank_transactions.parsing.normalizers.text import cell_text
from bank_transactions.parsing.row_reader import RowReader, RowRejected
from bank_transactions.parsing.shared.draft import FeeMarker, TransactionDraft
from bank_transactions.parsing.shared.fee_folder import (
    DEFAULT_FEE_KEYWORDS,
    DEFAULT_FEE_WINDOW_DAYS,
    ForeignFeeFolder,
    ForeignFeeMatcher,
)
from bank_transactions.parsing.shared.fx_calculator import EffectiveRateCalculator
from bank_transactions.parsing.shared.installment_grouper import (
    InstallmentPlanBuilder,
    InstallmentPlans,
)


@dataclass(frozen=True)
class SheetPlan:
    """A sheet to read: its one-based position, header layout and whether every row on it is a
    foreign-currency charge."""

    position: int
    sheet: SheetView
    layout: ColumnLayout
    foreign: bool = False


@dataclass(frozen=True)
class SourceRow:
    """One data row handed to an issuer's `_read_draft`."""

    location: SourceLocation
    reader: RowReader
    foreign_sheet: bool


@dataclass
class _Collected:
    rows_seen: int = 0
    drafts: list[TransactionDraft] = field(default_factory=list)
    fees: list[FeeMarker] = field(default_factory=list)
    issues: list[RowIssue] = field(default_factory=list)


class TabularStatementParser(StatementParser, ABC):
    """`fee_keywords` and `fee_window_days` configure foreign-commission folding (defaults are
    documented in `shared.fee_folder`); `today` supplies the anchor for two-digit years when a
    statement states no date of its own; `two_digit_years_back` (0..99, default 80) is how far
    before that anchor a two-digit year may fall, so the window is
    [anchor - back, anchor + 99 - back]."""

    def __init__(
        self,
        *,
        fee_keywords: Iterable[str] = DEFAULT_FEE_KEYWORDS,
        fee_window_days: int = DEFAULT_FEE_WINDOW_DAYS,
        today: Callable[[], date] = date.today,
        two_digit_years_back: int = DEFAULT_TWO_DIGIT_YEARS_BACK,
    ) -> None:
        self._fee_matcher = ForeignFeeMatcher(fee_keywords)
        self._fee_folder = ForeignFeeFolder(fee_window_days)
        self._today = today
        self._years_back = check_two_digit_years_back(two_digit_years_back)
        self._plan_builder = InstallmentPlanBuilder()
        self._fx = EffectiveRateCalculator()

    def sniff(self, wb: WorkbookView) -> float:
        try:
            return self._format_confidence(wb)
        except ParseError:
            return 0.0

    def read_transactions(self, wb: WorkbookView, ctx: StatementContext) -> StatementParseResult:
        collected = _Collected()
        for plan in self._sheet_plans(wb):
            self._read_sheet(plan, ctx, collected)
        return self._finalize(collected)

    @abstractmethod
    def _format_confidence(self, wb: WorkbookView) -> float: ...

    @abstractmethod
    def _sheet_plans(self, wb: WorkbookView) -> Sequence[SheetPlan]:
        """Sheets to read, in order; structural problems raise ParseError."""

    @abstractmethod
    def _is_footer(self, plan: SheetPlan, cells: Row) -> bool:
        """True for the first row after the data; reading of the sheet stops there."""

    @abstractmethod
    def _read_draft(self, row: SourceRow, ctx: StatementContext) -> TransactionDraft:
        """Raise RowRejected (or PendingRow) when the row cannot become a charge."""

    def _date_anchor(self, ctx: StatementContext) -> date:
        if ctx.period is not None and ctx.period.end is not None:
            return ctx.period.end
        return ctx.charge_date or self._today()

    @staticmethod
    def _only_card(ctx: StatementContext) -> CardHintKey:
        if len(ctx.cards) != 1:
            raise ParseError("this issuer's statement holds exactly one card")
        return ctx.cards[0].key

    def _read_sheet(self, plan: SheetPlan, ctx: StatementContext, collected: _Collected) -> None:
        anchor = self._date_anchor(ctx)
        for number, cells in plan.sheet.iter_rows(plan.layout.header_row + 1):
            if _is_blank(cells):
                continue
            if self._is_footer(plan, cells):
                return
            collected.rows_seen += 1
            location = SourceLocation(plan.position, number)
            reader = RowReader(plan.layout, cells, anchor, self._years_back)
            row = SourceRow(location, reader, plan.foreign)
            self._collect_row(row, ctx, collected)

    def _collect_row(self, row: SourceRow, ctx: StatementContext, collected: _Collected) -> None:
        try:
            draft = self._read_draft(row, ctx)
            if self._fee_matcher.is_fee(draft.merchant_name_raw):
                collected.fees.append(FeeMarker.of(draft))
                return
            _require_fx_ready(draft)
            collected.drafts.append(draft)
        except RowRejected as rejection:
            issue = RowIssue(row.location, rejection.code, rejection.severity, str(rejection))
            collected.issues.append(issue)

    def _finalize(self, collected: _Collected) -> StatementParseResult:
        folded = self._fee_folder.fold(collected.drafts, collected.fees)
        plans = self._plan_builder.build(folded)
        issues = list(collected.issues)
        transactions: list[ParsedTransaction] = []
        for draft in folded:
            try:
                transactions.append(self._build(draft, plans))
            except ValueError:
                issues.append(
                    RowIssue(
                        draft.location,
                        RowIssueCode.MALFORMED_ROW,
                        IssueSeverity.ERROR,
                        "the row breaks the transaction rules",
                    )
                )
        return StatementParseResult(
            transactions=tuple(transactions),
            issues=tuple(issues),
            rows_seen=collected.rows_seen,
            calculated_totals=StatementTotals.of(*(t.billing for t in transactions)),
            installment_plans=_distinct_plans(transactions),
        )

    def _build(self, draft: TransactionDraft, plans: InstallmentPlans) -> ParsedTransaction:
        parts = draft.merchant_parts
        reasons = set(draft.review_reasons)
        if draft.billing.currency is None or (
            draft.original is not None and draft.original.currency is None
        ):
            reasons.add(ReviewReason.UNKNOWN_CURRENCY)
        if plans.is_ambiguous(draft.location):
            reasons.add(ReviewReason.AMBIGUOUS_INSTALLMENT)
        fx = None
        if draft.is_foreign:
            fx = self._fx.detail(
                draft.billing, draft.original, draft.commission, draft.stated_fx_rate
            )
        return ParsedTransaction(
            card_key=draft.card_key,
            location=draft.location,
            transaction_date=draft.transaction_date,
            charge_date=draft.charge_date,
            billing=draft.billing,
            merchant_name_raw=draft.merchant_name_raw,
            merchant_name_norm=draft.merchant_name_norm,
            transaction_type=draft.transaction_type,
            original=draft.original,
            merchant_city=parts.city,
            merchant_country=parts.country,
            bank_category_raw=draft.bank_category_raw,
            channel=draft.channel,
            notes=draft.notes,
            issuer_reference=draft.issuer_reference,
            installment=draft.installment,
            installment_plan=plans.plan_for(draft.location),
            fx=fx,
            review_reasons=frozenset(reasons),
        )


def _is_blank(cells: Row) -> bool:
    return all(cell_text(cell) is None for cell in cells)


def _require_fx_ready(draft: TransactionDraft) -> None:
    if not draft.is_foreign:
        return
    if draft.original is None or draft.original.is_zero():
        raise RowRejected(
            RowIssueCode.MISSING_OR_ZERO_FX_ORIGINAL_AMOUNT,
            "a foreign charge needs a non-zero original amount",
        )
    if draft.billing.currency is None:
        raise RowRejected(
            RowIssueCode.MISSING_REQUIRED_VALUE, "a foreign charge needs a billing currency"
        )


def _distinct_plans(transactions: Sequence[ParsedTransaction]) -> tuple[ParsedInstallmentPlan, ...]:
    plans = (t.installment_plan for t in transactions if t.installment_plan is not None)
    return tuple(dict.fromkeys(plans))
