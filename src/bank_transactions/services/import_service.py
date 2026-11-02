"""Imports one bank statement workbook: audit, identity gate, parse, de-duplicate, persist.

Identity changes, plans and transactions share ONE live unit of work. A normal run commits it
after a successful insertion; a dry run always rolls it back. The audit record uses separate
committed units of work, so it survives either outcome. The service has no issuer-specific code.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bank_transactions.domain.enums import ImportStatus, IssuerCode, IssueSeverity, RowIssueCode
from bank_transactions.domain.errors import (
    BankTransactionsError,
    IdentityUnresolvedError,
    ParseError,
)
from bank_transactions.domain.ids import ImportRunId, UserId
from bank_transactions.domain.issues import RowIssue
from bank_transactions.domain.models import (
    CardHintKey,
    ParsedInstallmentPlan,
    ParsedTransaction,
    StatementContext,
    StatementParseResult,
)
from bank_transactions.domain.money import Currency
from bank_transactions.domain.text import contains_identifier
from bank_transactions.domain.workbook import WorkbookView
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import ResolvedCards
from bank_transactions.parsing.interfaces import StatementParser
from bank_transactions.persistence.commands import ImportRunOutcome, NewInstallmentPlan
from bank_transactions.persistence.interfaces import UnitOfWork, UnitOfWorkFactory
from bank_transactions.persistence.mappers import TransactionMapper
from bank_transactions.ports.identifier_protector import IdentifierProtector
from bank_transactions.ports.parser_selector import ParserSelector
from bank_transactions.ports.workbook_reader import WorkbookReader
from bank_transactions.services.audit_trail import AuditTrail, hash_file
from bank_transactions.services.deduplication import select_new_transactions
from bank_transactions.services.identity_gate import IdentityGate, statement_identifiers
from bank_transactions.services.report import ImportReport
from bank_transactions.services.totals_check import compare_totals

_MAX_SUMMARY_CHARS = 500
_TOTALS_UNAVAILABLE = "The file declares no totals, so the totals check was not available."
_INTERRUPTED = "import interrupted"


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Attempt:
    """Mutable bookkeeping of one import_file call; never leaves the service."""

    run_id: ImportRunId
    dry_run: bool
    warnings: list[str] = field(default_factory=list)
    issuer: IssuerCode | None = None
    identifiers: tuple[str, ...] = ()
    finish_attempted: bool = False


@dataclass(frozen=True)
class _Counts:
    total: int
    imported: int
    duplicate: int
    skipped: int
    needing_review: int


class StatementImportService:
    def __init__(
        self,
        reader: WorkbookReader,
        selector: ParserSelector,
        protector: IdentifierProtector,
        resolver: IdentityResolver,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] = utc_now,
        stale_after: timedelta = timedelta(hours=1),
    ) -> None:
        self._reader = reader
        self._selector = selector
        self._uow_factory = uow_factory
        self._gate = IdentityGate(protector, resolver)
        self._audit = AuditTrail(uow_factory, clock, stale_after)

    def find_user(self, email_key: str) -> UserId | None:
        """The id of the user with this normalised email, for the CLI's `--user` option."""
        with self._uow_factory.create() as uow:
            record = uow.users.find_by_email_key(email_key)
        return None if record is None else record.user_id

    def import_file(self, path: Path, *, user_id: UserId, dry_run: bool = False) -> ImportReport:
        warnings: list[str] = []
        swept = self._audit.sweep_stuck_runs()
        if swept:
            warnings.append(f"{swept} interrupted import run(s) were marked as failed.")
        digest = hash_file(path)
        attempt = _Attempt(self._audit.start(path, digest), dry_run, warnings)
        try:
            return self._execute(path, attempt, user_id)
        except BaseException as error:
            if not attempt.finish_attempted:
                self._record_failure(attempt, error)
            raise

    def _execute(self, path: Path, attempt: _Attempt, user_id: UserId) -> ImportReport:
        workbook = self._reader.open(path)
        parser = self._selector.select(workbook)
        context = parser.read_context(workbook)
        attempt.issuer = context.issuer_code
        attempt.identifiers = statement_identifiers(context)
        with self._uow_factory.create() as uow:
            try:
                cards = self._gate.resolve(context, uow, user_id)
            except IdentityUnresolvedError as unresolved:
                uow.rollback()
                return self._needs_input(attempt, context, unresolved.keys)
            return self._import_rows(attempt, parser, workbook, context, cards, uow)

    def _import_rows(
        self,
        attempt: _Attempt,
        parser: StatementParser,
        workbook: WorkbookView,
        context: StatementContext,
        cards: ResolvedCards,
        uow: UnitOfWork,
    ) -> ImportReport:
        result = parser.read_transactions(workbook, context)
        _require_consistent(result, cards)
        selected = select_new_transactions(result.transactions, cards, uow.transactions)
        self._store(uow, result, selected, cards, attempt.run_id)
        if attempt.dry_run:
            uow.rollback()
        else:
            uow.commit()
        return self._finish_import(attempt, context, result, selected)

    @staticmethod
    def _store(
        uow: UnitOfWork,
        result: StatementParseResult,
        selected: Sequence[ParsedTransaction],
        cards: ResolvedCards,
        run_id: ImportRunId,
    ) -> None:
        plans = {_plan_command(plan, cards) for plan in result.installment_plans}
        plans.update(
            _plan_command(tx.installment_plan, cards) for tx in selected if tx.installment_plan
        )
        plan_ids = uow.installment_plans.upsert_plans(plans) if plans else {}
        aggregates = tuple(
            TransactionMapper.to_aggregate(
                tx,
                cards.card_id_for(tx.card_key),
                run_id,
                plan_ids[_plan_command(tx.installment_plan, cards)]
                if tx.installment_plan
                else None,
            )
            for tx in selected
        )
        if aggregates:
            uow.transactions.insert_many(aggregates)

    def _finish_import(
        self,
        attempt: _Attempt,
        context: StatementContext,
        result: StatementParseResult,
        selected: Sequence[ParsedTransaction],
    ) -> ImportReport:
        counts = _Counts(
            total=result.rows_seen,
            imported=len(selected),
            duplicate=len(result.transactions) - len(selected),
            skipped=result.rows_seen - len(result.transactions),
            needing_review=sum(1 for tx in selected if tx.review_reasons),
        )
        issues, notes = _quality_findings(context, result)
        status = _status_of(result.issues)
        summary = _summary(attempt.dry_run, counts, result.issues, notes)
        self._finish(
            attempt,
            status,
            summary,
            counts
            if not attempt.dry_run
            else _Counts(counts.total, 0, counts.duplicate, counts.skipped, 0),
        )
        return ImportReport(
            attempt.run_id,
            status,
            context.issuer_code,
            counts.total,
            counts.imported,
            counts.duplicate,
            counts.skipped,
            counts.needing_review,
            issues=issues,
            warnings=(*attempt.warnings, *notes.warnings),
            dry_run=attempt.dry_run,
        )

    def _needs_input(
        self,
        attempt: _Attempt,
        context: StatementContext,
        keys: Sequence[CardHintKey],
    ) -> ImportReport:
        labels = _unresolved_labels(context, keys)
        summary = f"identity input required for {len(keys)} card(s)"
        self._finish(attempt, ImportStatus.NEEDS_INPUT, summary, _Counts(0, 0, 0, 0, 0))
        return ImportReport(
            attempt.run_id,
            ImportStatus.NEEDS_INPUT,
            context.issuer_code,
            0,
            0,
            0,
            0,
            0,
            warnings=tuple(attempt.warnings),
            unresolved_cards=labels,
            dry_run=attempt.dry_run,
        )

    def _finish(
        self, attempt: _Attempt, status: ImportStatus, summary: str | None, counts: _Counts
    ) -> None:
        outcome = ImportRunOutcome(
            status,
            self._audit.now(),
            attempt.issuer,
            counts.total,
            counts.imported,
            counts.duplicate,
            counts.skipped,
            summary[:_MAX_SUMMARY_CHARS] if summary else None,
        )
        attempt.finish_attempted = True
        self._audit.finish(attempt.run_id, outcome)

    def _record_failure(self, attempt: _Attempt, error: BaseException) -> None:
        """The domain unit of work is already rolled back when the error reaches this point."""
        try:
            self._finish(
                attempt,
                ImportStatus.FAILED,
                _failure_summary(error, attempt.identifiers),
                _Counts(0, 0, 0, 0, 0),
            )
        except Exception:
            error.add_note("the import audit record could not be finalized")


def _plan_command(plan: ParsedInstallmentPlan, cards: ResolvedCards) -> NewInstallmentPlan:
    return TransactionMapper.plan_command(plan, cards.card_id_for(plan.card_key))


def _require_consistent(result: StatementParseResult, cards: ResolvedCards) -> None:
    transaction_keys = {tx.card_key for tx in result.transactions}
    plan_keys = {plan.card_key for plan in result.installment_plans}
    if not (transaction_keys | plan_keys) <= set(cards.cards):
        raise ParseError("the parser returned rows for a card the statement did not name")
    if result.rows_seen < len(result.transactions):
        raise ParseError("the parser reported fewer rows seen than transactions")


def _status_of(issues: Sequence[RowIssue]) -> ImportStatus:
    has_error = any(issue.severity is IssueSeverity.ERROR for issue in issues)
    return ImportStatus.PARTIAL if has_error else ImportStatus.SUCCESS


@dataclass(frozen=True)
class _Notes:
    warnings: tuple[str, ...] = ()
    summary: str | None = None


def _quality_findings(
    context: StatementContext, result: StatementParseResult
) -> tuple[tuple[RowIssue, ...], _Notes]:
    comparison = compare_totals(context.declared_totals, result.calculated_totals)
    if not comparison.available:
        return tuple(result.issues), _Notes((_TOTALS_UNAVAILABLE,))
    if not comparison.mismatched:
        return tuple(result.issues), _Notes()
    names = ", ".join(_currency_name(currency) for currency in comparison.mismatched)
    message = f"Declared and calculated totals differ for {names}."
    mismatch = RowIssue(None, RowIssueCode.TOTALS_MISMATCH, IssueSeverity.WARNING, message)
    return (*result.issues, mismatch), _Notes((message,), f"totals differ for {names}")


def _currency_name(currency: Currency | None) -> str:
    return currency.code if currency else "unknown currency"


def _summary(
    dry_run: bool, counts: _Counts, issues: Sequence[RowIssue], notes: _Notes
) -> str | None:
    parts: list[str] = []
    if dry_run:
        parts.append(f"DRY RUN: nothing was committed; would import {counts.imported} row(s)")
    errors = Counter(i.code.value for i in issues if i.severity is IssueSeverity.ERROR)
    if errors:
        parts.append("errors: " + ", ".join(f"{code} x{n}" for code, n in sorted(errors.items())))
    if notes.summary:
        parts.append(notes.summary)
    return "; ".join(parts) or None


def _unresolved_labels(context: StatementContext, keys: Sequence[CardHintKey]) -> tuple[str, ...]:
    total = len(context.cards)
    wanted = set(keys)
    return tuple(
        f"{context.issuer_code.value} card {position} of {total}"
        for position, card in enumerate(context.cards, start=1)
        if card.key in wanted
    )


def _failure_summary(error: BaseException, identifiers: Sequence[str]) -> str:
    """Never str() of an untrusted error: only our own error types contribute their message."""
    if isinstance(error, KeyboardInterrupt):
        return _INTERRUPTED
    name = type(error).__name__
    if not isinstance(error, BankTransactionsError):
        return f"unexpected error ({name})"
    text = f"{name}: {error}"
    return name if contains_identifier(text, identifiers) else text
