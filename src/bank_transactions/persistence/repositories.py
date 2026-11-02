"""SQLAlchemy implementations of the persistence repository contracts.

Commands arrive already quantized (money to 2 places, rates to 6), so values are stored exactly as
given. Every public operation raises only neutral PersistenceErrors.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from bank_transactions.domain.enums import ImportStatus
from bank_transactions.domain.errors import PersistenceError
from bank_transactions.domain.ids import (
    AccountId,
    CardId,
    IdentifierToken,
    ImportRunId,
    InstallmentPlanId,
    OwnerId,
    TransactionId,
    UserId,
)
from bank_transactions.domain.keys import ComparisonKey, IssuerReferenceKey, OwnerMatchKey
from bank_transactions.domain.money import Currency
from bank_transactions.persistence.commands import (
    ImportRunOutcome,
    NewAccount,
    NewCard,
    NewImportRun,
    NewInstallmentPlan,
    NewOwner,
    NewTransaction,
    NewTransactionAggregate,
)
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import (
    AccountRepository,
    CardRepository,
    ImportRunRepository,
    InstallmentPlanRepository,
    OwnerRepository,
    TransactionRepository,
)
from bank_transactions.persistence.orm.models import (
    AccountRow,
    CardRow,
    ForeignDetailRow,
    ImportRunRow,
    InstallmentPlanRow,
    OwnerRow,
    ReviewReasonRow,
    TransactionRow,
)
from bank_transactions.persistence.records import (
    AccountRecord,
    CardLookup,
    CardRecord,
    OwnerRecord,
)

KEY_CHUNK_SIZE = 1000
_INCONSISTENT_PLAN = (
    "inconsistent installment plan: its reference and its purchase details identify different plans"
)


def _chunks[T](items: Sequence[T], size: int = KEY_CHUNK_SIZE) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _require_aware(*moments: datetime) -> None:
    if any(moment.tzinfo is None for moment in moments):
        raise PersistenceError("timestamps must carry a timezone")


def _code(currency: Currency | None) -> str | None:
    return None if currency is None else currency.code


def _require_unscoped(scope: object | None) -> None:
    """U0 keeps the legacy CLI behaviour; the user-scoped SQL is added by U1."""
    if scope is not None:
        raise NotImplementedError("user-scoped persistence is implemented by U1")


def _owner_record(row: OwnerRow) -> OwnerRecord:
    return OwnerRecord(OwnerId(row.id), row.display_name, OwnerMatchKey(row.match_key))


def _account_record(row: AccountRow) -> AccountRecord:
    token = (
        None if row.account_number_enc is None else IdentifierToken(bytes(row.account_number_enc))
    )
    return AccountRecord(
        AccountId(row.id), OwnerId(row.owner_id), row.bank_code, token, row.display_name
    )


def _card_record(row: CardRow) -> CardRecord:
    return CardRecord(
        CardId(row.id),
        OwnerId(row.owner_id),
        AccountId(row.account_id),
        row.issuer_code,
        IdentifierToken(bytes(row.last4_enc)),
        row.display_name,
    )


class SqlImportRunRepository(ImportRunRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def start_run(self, run: NewImportRun) -> ImportRunId:
        _require_aware(run.started_at)
        row = ImportRunRow(
            file_path=run.file_path,
            file_name=run.file_name,
            file_sha256=run.file_sha256,
            started_at=run.started_at,
            status=ImportStatus.RUNNING,
        )
        self._session.add(row)
        self._session.flush()
        return ImportRunId(row.id)

    @translates_database_errors
    def finish_run(self, run_id: ImportRunId, outcome: ImportRunOutcome) -> None:
        _require_aware(outcome.finished_at)
        result = self._session.execute(
            sa.update(ImportRunRow)
            .where(ImportRunRow.id == run_id, ImportRunRow.status == ImportStatus.RUNNING)
            .values(
                status=outcome.status,
                finished_at=outcome.finished_at,
                issuer_code=outcome.issuer_code,
                rows_total=outcome.rows_total,
                rows_imported=outcome.rows_imported,
                rows_duplicate=outcome.rows_duplicate,
                rows_skipped=outcome.rows_skipped,
                error_summary=outcome.error_summary,
            )
            .execution_options(synchronize_session=False)
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            raise PersistenceError("the import run is unknown or already finished")

    @translates_database_errors
    def sweep_stuck_runs(
        self, started_before: datetime, finished_at: datetime, error_summary: str
    ) -> int:
        _require_aware(started_before, finished_at)
        result = self._session.execute(
            sa.update(ImportRunRow)
            .where(
                ImportRunRow.status == ImportStatus.RUNNING,
                ImportRunRow.started_at < started_before,
            )
            .values(
                status=ImportStatus.FAILED, finished_at=finished_at, error_summary=error_summary
            )
            .execution_options(synchronize_session=False)
        )
        return cast(CursorResult[Any], result).rowcount


class SqlOwnerRepository(OwnerRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def list_all(self, user_id: UserId | None = None) -> tuple[OwnerRecord, ...]:
        _require_unscoped(user_id)
        rows = self._session.scalars(select(OwnerRow).order_by(OwnerRow.id))
        return tuple(_owner_record(row) for row in rows)

    @translates_database_errors
    def get(self, owner_id: OwnerId, user_id: UserId | None = None) -> OwnerRecord | None:
        _require_unscoped(user_id)
        row = self._session.get(OwnerRow, owner_id)
        return None if row is None else _owner_record(row)

    @translates_database_errors
    def find_by_match_key(
        self, match_key: OwnerMatchKey, user_id: UserId | None = None
    ) -> OwnerRecord | None:
        _require_unscoped(user_id)
        row = self._session.scalars(
            select(OwnerRow).where(OwnerRow.match_key == match_key.value)
        ).one_or_none()
        return None if row is None else _owner_record(row)

    @translates_database_errors
    def create(self, owner: NewOwner) -> OwnerRecord:
        _require_unscoped(owner.user_id)
        row = OwnerRow(display_name=owner.display_name, match_key=owner.match_key.value)
        self._session.add(row)
        self._session.flush()
        return _owner_record(row)


class SqlAccountRepository(AccountRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def list_all(self, user_id: UserId | None = None) -> tuple[AccountRecord, ...]:
        _require_unscoped(user_id)
        rows = self._session.scalars(select(AccountRow).order_by(AccountRow.id))
        return tuple(_account_record(row) for row in rows)

    @translates_database_errors
    def get(self, account_id: AccountId, user_id: UserId | None = None) -> AccountRecord | None:
        _require_unscoped(user_id)
        row = self._session.get(AccountRow, account_id)
        return None if row is None else _account_record(row)

    @translates_database_errors
    def find_by_number(
        self,
        bank_code: str | None,
        account_number_enc: IdentifierToken,
        user_id: UserId | None = None,
    ) -> tuple[AccountRecord, ...]:
        _require_unscoped(user_id)
        rows = self._session.scalars(
            select(AccountRow)
            .where(
                AccountRow.bank_code.is_not_distinct_from(bank_code),
                AccountRow.account_number_enc == bytes(account_number_enc),
            )
            .order_by(AccountRow.id)
        )
        return tuple(_account_record(row) for row in rows)

    @translates_database_errors
    def create(self, account: NewAccount) -> AccountRecord:
        token = None if account.account_number_enc is None else bytes(account.account_number_enc)
        row = AccountRow(
            owner_id=account.owner_id,
            bank_code=account.bank_code,
            account_number_enc=token,
            display_name=account.display_name,
        )
        self._session.add(row)
        self._session.flush()
        return _account_record(row)


class SqlCardRepository(CardRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def get(self, card_id: CardId, user_id: UserId | None = None) -> CardRecord | None:
        _require_unscoped(user_id)
        row = self._session.get(CardRow, card_id)
        return None if row is None else _card_record(row)

    @translates_database_errors
    def find_candidates(self, lookup: CardLookup) -> tuple[CardRecord, ...]:
        _require_unscoped(lookup.user_id)
        query = select(CardRow).where(
            CardRow.issuer_code == lookup.issuer_code,
            CardRow.last4_enc == bytes(lookup.last4_enc),
        )
        if lookup.account_id is not None:
            query = query.where(CardRow.account_id == lookup.account_id)
        if lookup.owner_id is not None:
            query = query.where(CardRow.owner_id == lookup.owner_id)
        return tuple(_card_record(row) for row in self._session.scalars(query.order_by(CardRow.id)))

    @translates_database_errors
    def create(self, card: NewCard) -> CardRecord:
        _require_unscoped(card.claim_id)
        row = CardRow(
            owner_id=card.owner_id,
            account_id=card.account_id,
            issuer_code=card.issuer_code,
            last4_enc=bytes(card.last4_enc),
            display_name=card.display_name,
        )
        self._session.add(row)
        self._session.flush()
        return _card_record(row)


class SqlInstallmentPlanRepository(InstallmentPlanRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def upsert_plans(
        self, plans: Collection[NewInstallmentPlan]
    ) -> Mapping[NewInstallmentPlan, InstallmentPlanId]:
        return {plan: self._resolve(plan) for plan in dict.fromkeys(plans)}

    def _resolve(self, plan: NewInstallmentPlan) -> InstallmentPlanId:
        self._insert_when_new(plan)
        natural = self._find_natural(plan)
        by_reference = self._find_by_reference(plan)
        if natural is None or (by_reference is not None and by_reference.id != natural.id):
            raise PersistenceError(_INCONSISTENT_PLAN)
        if natural.issuer_reference not in (None, plan.issuer_reference) and plan.issuer_reference:
            raise PersistenceError(_INCONSISTENT_PLAN)
        return InstallmentPlanId(natural.id)

    def _insert_when_new(self, plan: NewInstallmentPlan) -> None:
        self._session.execute(
            postgresql_insert(InstallmentPlanRow)
            .values(
                card_id=plan.card_id,
                issuer_reference=plan.issuer_reference,
                merchant_name_norm=plan.merchant_name_norm,
                purchase_date=plan.purchase_date,
                total_amount=plan.total_amount,
                currency=plan.currency.code,
                total_installments=plan.total_installments,
            )
            .on_conflict_do_nothing()
        )

    def _find_natural(self, plan: NewInstallmentPlan) -> InstallmentPlanRow | None:
        return self._session.scalars(
            select(InstallmentPlanRow).where(
                InstallmentPlanRow.card_id == plan.card_id,
                InstallmentPlanRow.merchant_name_norm == plan.merchant_name_norm,
                InstallmentPlanRow.purchase_date == plan.purchase_date,
                InstallmentPlanRow.total_amount == plan.total_amount,
                InstallmentPlanRow.currency == plan.currency.code,
                InstallmentPlanRow.total_installments == plan.total_installments,
            )
        ).one_or_none()

    def _find_by_reference(self, plan: NewInstallmentPlan) -> InstallmentPlanRow | None:
        if plan.issuer_reference is None:
            return None
        return self._session.scalars(
            select(InstallmentPlanRow).where(
                InstallmentPlanRow.card_id == plan.card_id,
                InstallmentPlanRow.issuer_reference == plan.issuer_reference,
            )
        ).one_or_none()


class SqlTransactionRepository(TransactionRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def find_known_references(
        self, keys: Collection[IssuerReferenceKey]
    ) -> frozenset[IssuerReferenceKey]:
        unique = list(dict.fromkeys(keys))
        standalone = [key for key in unique if key.installment_number is None]
        numbered = [key for key in unique if key.installment_number is not None]
        found: set[IssuerReferenceKey] = set()
        for chunk in _chunks(standalone):
            found.update(self._stored_standalone(chunk))
        for chunk in _chunks(numbered):
            found.update(self._stored_numbered(chunk))
        return frozenset(found)

    def _stored_standalone(
        self, keys: Sequence[IssuerReferenceKey]
    ) -> Iterable[IssuerReferenceKey]:
        rows = self._session.execute(
            select(
                TransactionRow.card_id,
                TransactionRow.transaction_date,
                TransactionRow.issuer_reference,
            ).where(
                TransactionRow.installment_number.is_(None),
                tuple_(
                    TransactionRow.card_id,
                    TransactionRow.transaction_date,
                    TransactionRow.issuer_reference,
                ).in_([(key.card_id, key.transaction_date, key.issuer_reference) for key in keys]),
            )
        )
        return [
            IssuerReferenceKey(CardId(card), day, cast(str, reference), None)
            for card, day, reference in rows
        ]

    def _stored_numbered(self, keys: Sequence[IssuerReferenceKey]) -> Iterable[IssuerReferenceKey]:
        rows = self._session.execute(
            select(
                TransactionRow.card_id,
                TransactionRow.transaction_date,
                TransactionRow.issuer_reference,
                TransactionRow.installment_number,
            ).where(
                tuple_(
                    TransactionRow.card_id,
                    TransactionRow.transaction_date,
                    TransactionRow.issuer_reference,
                    TransactionRow.installment_number,
                ).in_(
                    [
                        (
                            key.card_id,
                            key.transaction_date,
                            key.issuer_reference,
                            key.installment_number,
                        )
                        for key in keys
                    ]
                )
            )
        )
        return [
            IssuerReferenceKey(CardId(card), day, cast(str, reference), number)
            for card, day, reference, number in rows
        ]

    @translates_database_errors
    def count_matching_groups(self, keys: Collection[ComparisonKey]) -> Mapping[ComparisonKey, int]:
        unique = list(dict.fromkeys(keys))
        days = list(dict.fromkeys((key.card_id, key.transaction_date) for key in unique))
        counts: dict[ComparisonKey, int] = {}
        for chunk in _chunks(days):
            counts.update(self._stored_group_counts(chunk))
        return {key: counts.get(key, 0) for key in unique}

    def _stored_group_counts(
        self, days: Sequence[tuple[CardId, date]]
    ) -> Mapping[ComparisonKey, int]:
        columns = (
            TransactionRow.card_id,
            TransactionRow.transaction_date,
            TransactionRow.merchant_name_norm,
            TransactionRow.original_amount,
            TransactionRow.original_currency,
            TransactionRow.installment_number,
        )
        rows = self._session.execute(
            select(*columns, sa.func.count())
            .where(
                TransactionRow.issuer_reference.is_(None),
                tuple_(TransactionRow.card_id, TransactionRow.transaction_date).in_(list(days)),
            )
            .group_by(*columns)
        )
        return {
            ComparisonKey(
                CardId(card),
                day,
                merchant,
                amount,
                None if currency is None else Currency(currency),
                number,
            ): count
            for card, day, merchant, amount, currency, number, count in rows
        }

    @translates_database_errors
    def insert_many(
        self, aggregates: Sequence[NewTransactionAggregate]
    ) -> tuple[TransactionId, ...]:
        rows = [_transaction_row(aggregate.transaction) for aggregate in aggregates]
        self._session.add_all(rows)
        self._session.flush()
        for row, aggregate in zip(rows, aggregates, strict=True):
            self._session.add_all(_child_rows(row.id, aggregate))
        self._session.flush()
        return tuple(TransactionId(row.id) for row in rows)


def _transaction_row(transaction: NewTransaction) -> TransactionRow:
    return TransactionRow(
        card_id=transaction.card_id,
        import_run_id=transaction.import_run_id,
        issuer_reference=transaction.issuer_reference,
        transaction_date=transaction.transaction_date,
        charge_date=transaction.charge_date,
        billing_amount=transaction.billing_amount,
        billing_currency=_code(transaction.billing_currency),
        original_amount=transaction.original_amount,
        original_currency=_code(transaction.original_currency),
        merchant_name_raw=transaction.merchant_name_raw,
        merchant_name_norm=transaction.merchant_name_norm,
        merchant_city=transaction.merchant_city,
        merchant_country=transaction.merchant_country,
        bank_category_raw=transaction.bank_category_raw,
        transaction_type=transaction.transaction_type,
        channel=transaction.channel,
        notes=transaction.notes,
        installment_plan_id=transaction.installment_plan_id,
        installment_number=transaction.installment_number,
    )


def _child_rows(
    transaction_id: int, aggregate: NewTransactionAggregate
) -> list[ReviewReasonRow | ForeignDetailRow]:
    children: list[ReviewReasonRow | ForeignDetailRow] = [
        ReviewReasonRow(transaction_id=transaction_id, reason=reason)
        for reason in sorted(aggregate.review_reasons)
    ]
    detail = aggregate.foreign_detail
    if detail is not None:
        children.append(
            ForeignDetailRow(
                transaction_id=transaction_id,
                commission_amount=detail.commission_amount,
                stated_fx_rate=detail.stated_fx_rate,
                effective_fx_rate=detail.effective_fx_rate,
            )
        )
    return children
