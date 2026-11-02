"""Read side of the transaction list. Stub: implemented by U6."""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import PublicId, TransactionId, UserId
from bank_transactions.domain.queries import (
    Dimension,
    Page,
    PageRequest,
    PendingValidationFilter,
    SortSpec,
    TransactionFilter,
)
from bank_transactions.persistence.interfaces import TransactionQueryRepository
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    FilterOptionsRecord,
    GroupRecord,
    NamedCountRecord,
    TransactionRef,
    TransactionViewRecord,
)


class SqlTransactionQueryRepository(TransactionQueryRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def page(
        self, user_id: UserId, filter: TransactionFilter, sort: SortSpec, page: PageRequest
    ) -> Page[TransactionViewRecord]:
        raise NotImplementedError

    def summary(self, user_id: UserId, filter: TransactionFilter) -> BillingSummaryRecord:
        raise NotImplementedError

    def groups(
        self, user_id: UserId, filter: TransactionFilter, dimension: Dimension
    ) -> tuple[GroupRecord, ...]:
        raise NotImplementedError

    def get(self, user_id: UserId, public_id: PublicId) -> TransactionViewRecord | None:
        raise NotImplementedError

    def visible_id(self, user_id: UserId, public_id: PublicId) -> TransactionId | None:
        raise NotImplementedError

    def lock_visible(
        self, user_id: UserId, public_ids: Sequence[PublicId]
    ) -> tuple[TransactionRef, ...]:
        raise NotImplementedError

    def lock_pending_matching(
        self, user_id: UserId, filter: PendingValidationFilter
    ) -> tuple[TransactionRef, ...]:
        raise NotImplementedError

    def filter_options(self, user_id: UserId) -> FilterOptionsRecord:
        raise NotImplementedError

    def bank_category_counts(self, user_id: UserId) -> tuple[NamedCountRecord, ...]:
        raise NotImplementedError

    def has_bank_category(self, user_id: UserId, bank_category: str) -> bool:
        raise NotImplementedError
