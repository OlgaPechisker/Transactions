"""Currency validation: the queue, batches and their effect on transactions.

A validation sets `transactions.original_currency` and removes the UNKNOWN_CURRENCY review reason;
`source_original_currency` is never written. Writers lock the transaction rows before they read
the state they decide on: a statement of a READ COMMITTED transaction does not see commits that
land while it waits for a lock, so the state is read by a separate statement after the lock."""

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    Select,
    Subquery,
    Table,
    delete,
    exists,
    func,
    insert,
    literal,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from bank_transactions.domain.enums import ReviewReason, ValidationStatus
from bank_transactions.domain.ids import BatchId, PublicId, TransactionId, UserId
from bank_transactions.domain.queries import (
    Page,
    PageRequest,
    PendingValidationFilter,
    SortSpec,
)
from bank_transactions.persistence.commands import NewValidationBatch
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import CurrencyValidationRepository
from bank_transactions.persistence.orm.models import (
    CurrencyValidationRow,
    ReviewReasonColumn,
    ReviewReasonRow,
    TransactionRow,
    ValidationBatchMemberRow,
    ValidationBatchRow,
)
from bank_transactions.persistence.providers.transaction_query import _contains_any
from bank_transactions.persistence.public_ids import new_public_id
from bank_transactions.persistence.records import (
    BatchMemberState,
    BatchRecord,
    CountryOptionRecord,
    ValidationItemRecord,
)
from bank_transactions.persistence.visibility import (
    to_view_record,
    transaction_view_select,
    visible_transactions,
    visible_unknown_currency,
)

__all__ = ["SqlCurrencyValidationRepository"]

_t: Table = cast(Table, TransactionRow.__table__)
_batches: Table = cast(Table, ValidationBatchRow.__table__)
_members: Table = cast(Table, ValidationBatchMemberRow.__table__)
_validations: Table = cast(Table, CurrencyValidationRow.__table__)
_reasons: Table = cast(Table, ReviewReasonRow.__table__)

# Keeps a statement far below the driver's limit of 65 535 bind parameters; a batch has no
# business size limit.
_CHUNK = 10_000

_SORT_COLUMNS = {
    "transactionDate": "transaction_date",
    "merchant": "merchant",
    "country": "country",
    "originalAmount": "original_amount",
    "billingAmount": "billing_amount",
}


def _chunks(ids: Sequence[TransactionId]) -> Iterator[list[int]]:
    for start in range(0, len(ids), _CHUNK):
        yield [int(transaction_id) for transaction_id in ids[start : start + _CHUNK]]


def _batch_record(row: Any) -> BatchRecord:
    return BatchRecord(
        batch_id=BatchId(row.id),
        public_id=PublicId(row.public_id),
        user_id=UserId(row.user_id),
        currency=row.currency,
        created_at=row.created_at,
        undone_at=row.undone_at,
        validated_count=row.applied_count,
    )


def _narrowed(statement: Select[Any], filter: PendingValidationFilter) -> Select[Any]:
    """The same conditions, on the same columns, as `lock_pending_matching` of U6."""
    if filter.countries:
        statement = statement.where(_t.c.merchant_country.in_(filter.countries))
    if filter.date_from is not None:
        statement = statement.where(_t.c.transaction_date >= filter.date_from)
    if filter.date_to is not None:
        statement = statement.where(_t.c.transaction_date <= filter.date_to)
    if filter.merchants:
        statement = statement.where(_contains_any(_t.c.merchant_name_raw, filter.merchants))
    return statement


def _ordering(view: Subquery, sort: SortSpec) -> list[Any]:
    """Nulls sort last in both directions; the transaction id breaks ties so pages are stable."""
    ordering: list[Any] = []
    for field, descending in sort.fields:
        column = view.c[_SORT_COLUMNS[field]]
        ordering.append(column.desc().nulls_last() if descending else column.asc().nulls_last())
    ordering.append(view.c.transaction_id.asc())
    return ordering


class SqlCurrencyValidationRepository(CurrencyValidationRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def count_pending(self, user_id: UserId) -> int:
        return self._session.execute(
            visible_transactions(user_id)
            .with_only_columns(func.count())
            .where(visible_unknown_currency())
        ).scalar_one()

    @translates_database_errors
    def count_validated(self, user_id: UserId) -> int:
        return self._session.execute(
            visible_transactions(user_id)
            .with_only_columns(func.count())
            .where(exists().where(_validations.c.transaction_id == _t.c.id))
        ).scalar_one()

    @translates_database_errors
    def pending_countries(self, user_id: UserId) -> tuple[CountryOptionRecord, ...]:
        rows = self._session.execute(
            visible_transactions(user_id)
            .with_only_columns(_t.c.merchant_country, func.count())
            .where(visible_unknown_currency(), _t.c.merchant_country.is_not(None))
            .group_by(_t.c.merchant_country)
            .order_by(_t.c.merchant_country)
        ).all()
        return tuple(CountryOptionRecord(code, count) for code, count in rows)

    @translates_database_errors
    def page_items(
        self,
        user_id: UserId,
        status: ValidationStatus,
        filter: PendingValidationFilter,
        sort: SortSpec,
        page: PageRequest,
    ) -> Page[ValidationItemRecord]:
        statement = transaction_view_select(user_id)
        if status is ValidationStatus.PENDING:
            statement = statement.where(visible_unknown_currency())
        else:
            statement = statement.join(_validations, _validations.c.transaction_id == _t.c.id).join(
                _batches, _batches.c.id == _validations.c.batch_id
            )
            statement = statement.add_columns(
                _validations.c.currency.label("validated_currency"),
                _validations.c.validated_at.label("validated_at"),
                _batches.c.public_id.label("batch_public_id"),
            )
        view = _narrowed(statement, filter).subquery("v")
        total = self._session.execute(select(func.count()).select_from(view)).scalar_one()
        rows = self._session.execute(
            select(view).order_by(*_ordering(view, sort)).limit(page.size).offset(page.offset)
        ).all()
        pending = status is ValidationStatus.PENDING
        items = tuple(
            ValidationItemRecord(
                transaction=to_view_record(row),
                status=status,
                validated_currency=None if pending else row.validated_currency,
                validated_at=None if pending else row.validated_at,
                batch_public_id=None if pending else PublicId(row.batch_public_id),
            )
            for row in rows
        )
        return Page(items, page.page, page.size, total)

    @translates_database_errors
    def validated_among(
        self, user_id: UserId, transaction_ids: Sequence[TransactionId]
    ) -> frozenset[TransactionId]:
        found: set[TransactionId] = set()
        for chunk in _chunks(transaction_ids):
            rows = self._session.execute(
                visible_transactions(user_id)
                .with_only_columns(_t.c.id)
                .where(
                    _t.c.id.in_(chunk),
                    exists().where(_validations.c.transaction_id == _t.c.id),
                )
            ).scalars()
            found.update(TransactionId(row) for row in rows)
        return frozenset(found)

    @translates_database_errors
    def pending_among(
        self, user_id: UserId, transaction_ids: Sequence[TransactionId]
    ) -> frozenset[TransactionId]:
        found: set[TransactionId] = set()
        for chunk in _chunks(transaction_ids):
            rows = self._session.execute(
                visible_transactions(user_id)
                .with_only_columns(_t.c.id)
                .where(_t.c.id.in_(chunk), visible_unknown_currency())
            ).scalars()
            found.update(TransactionId(row) for row in rows)
        return frozenset(found)

    @translates_database_errors
    def create_batch(self, batch: NewValidationBatch) -> BatchRecord:
        row = self._session.execute(
            insert(_batches)
            .values(
                public_id=new_public_id("cvb"),
                user_id=batch.user_id,
                currency=batch.currency,
                applied_count=0,
                created_at=batch.created_at,
            )
            .returning(*_batches.c)
        ).one()
        return _batch_record(row)

    @translates_database_errors
    def add_members(self, batch_id: BatchId, transaction_ids: Sequence[TransactionId]) -> None:
        for chunk in _chunks(transaction_ids):
            self._session.execute(
                insert(_members),
                [{"batch_id": batch_id, "transaction_id": member} for member in chunk],
            )
        self._session.execute(
            update(_batches)
            .where(_batches.c.id == batch_id)
            .values(
                applied_count=select(func.count())
                .select_from(_members)
                .where(_members.c.batch_id == batch_id)
                .scalar_subquery()
            )
        )

    @translates_database_errors
    def apply(
        self,
        user_id: UserId,
        batch_id: BatchId,
        transaction_ids: Sequence[TransactionId],
        currency: str,
    ) -> None:
        validated_at = self._session.execute(
            select(_batches.c.created_at).where(_batches.c.id == batch_id)
        ).scalar_one()
        for chunk in _chunks(transaction_ids):
            self._session.execute(
                update(_t).where(_t.c.id.in_(chunk)).values(original_currency=currency)
            )
            self._session.execute(
                delete(_reasons).where(
                    _reasons.c.transaction_id.in_(chunk),
                    _reasons.c.reason == ReviewReason.UNKNOWN_CURRENCY,
                )
            )
            self._session.execute(
                insert(_validations),
                [
                    {
                        "transaction_id": member,
                        "batch_id": batch_id,
                        "currency": currency,
                        "validated_by_user_id": user_id,
                        "validated_at": validated_at,
                    }
                    for member in chunk
                ],
            )

    @translates_database_errors
    def lock_batch(self, user_id: UserId, public_id: PublicId) -> BatchRecord | None:
        row = self._session.execute(
            select(_batches)
            .where(_batches.c.public_id == public_id, _batches.c.user_id == user_id)
            .with_for_update()
        ).one_or_none()
        return None if row is None else _batch_record(row)

    @translates_database_errors
    def members_state(self, batch_id: BatchId) -> tuple[BatchMemberState, ...]:
        members = select(_members.c.transaction_id).where(_members.c.batch_id == batch_id)
        self._session.execute(
            select(_t.c.id).where(_t.c.id.in_(members)).order_by(_t.c.id).with_for_update(of=_t)
        ).all()
        rows = self._session.execute(
            select(
                _t.c.id,
                _t.c.public_id,
                _validations.c.batch_id.label("validation_batch_id"),
                _t.c.original_currency,
            )
            .select_from(_t.outerjoin(_validations, _validations.c.transaction_id == _t.c.id))
            .where(_t.c.id.in_(members))
            .order_by(_t.c.id)
        ).all()
        return tuple(
            BatchMemberState(
                transaction_id=TransactionId(row.id),
                public_id=PublicId(row.public_id),
                validation_batch_id=None
                if row.validation_batch_id is None
                else BatchId(row.validation_batch_id),
                original_currency=row.original_currency,
            )
            for row in rows
        )

    @translates_database_errors
    def undo(self, batch_id: BatchId, undone_at: datetime) -> None:
        members = select(_members.c.transaction_id).where(_members.c.batch_id == batch_id)
        self._session.execute(update(_t).where(_t.c.id.in_(members)).values(original_currency=None))
        self._session.execute(
            pg_insert(_reasons)
            .from_select(
                ["transaction_id", "reason"],
                select(
                    _members.c.transaction_id,
                    literal(ReviewReason.UNKNOWN_CURRENCY, ReviewReasonColumn),
                ).where(_members.c.batch_id == batch_id),
            )
            .on_conflict_do_nothing()
        )
        self._session.execute(delete(_validations).where(_validations.c.batch_id == batch_id))
        self._session.execute(
            update(_batches).where(_batches.c.id == batch_id).values(undone_at=undone_at)
        )

    @translates_database_errors
    def lock_validation(self, transaction_id: TransactionId) -> bool:
        self._session.execute(
            select(_t.c.id).where(_t.c.id == transaction_id).with_for_update()
        ).all()
        return (
            self._session.execute(
                select(_validations.c.transaction_id).where(
                    _validations.c.transaction_id == transaction_id
                )
            ).first()
            is not None
        )

    @translates_database_errors
    def reset(self, transaction_id: TransactionId) -> None:
        self._session.execute(
            update(_t).where(_t.c.id == transaction_id).values(original_currency=None)
        )
        self._session.execute(
            pg_insert(_reasons)
            .values(transaction_id=transaction_id, reason=ReviewReason.UNKNOWN_CURRENCY)
            .on_conflict_do_nothing()
        )
        self._session.execute(
            delete(_validations).where(_validations.c.transaction_id == transaction_id)
        )
