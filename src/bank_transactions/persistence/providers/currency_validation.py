"""Currency validation. Stub: implemented by U10."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.orm import Session

from bank_transactions.domain.enums import ValidationStatus
from bank_transactions.domain.ids import BatchId, PublicId, TransactionId, UserId
from bank_transactions.domain.queries import (
    Page,
    PageRequest,
    PendingValidationFilter,
    SortSpec,
)
from bank_transactions.persistence.commands import NewValidationBatch
from bank_transactions.persistence.interfaces import CurrencyValidationRepository
from bank_transactions.persistence.records import (
    BatchMemberState,
    BatchRecord,
    CountryOptionRecord,
    ValidationItemRecord,
)


class SqlCurrencyValidationRepository(CurrencyValidationRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def count_pending(self, user_id: UserId) -> int:
        raise NotImplementedError

    def count_validated(self, user_id: UserId) -> int:
        raise NotImplementedError

    def pending_countries(self, user_id: UserId) -> tuple[CountryOptionRecord, ...]:
        raise NotImplementedError

    def page_items(
        self,
        user_id: UserId,
        status: ValidationStatus,
        filter: PendingValidationFilter,
        sort: SortSpec,
        page: PageRequest,
    ) -> Page[ValidationItemRecord]:
        raise NotImplementedError

    def validated_among(
        self, user_id: UserId, transaction_ids: Sequence[TransactionId]
    ) -> frozenset[TransactionId]:
        raise NotImplementedError

    def pending_among(
        self, user_id: UserId, transaction_ids: Sequence[TransactionId]
    ) -> frozenset[TransactionId]:
        raise NotImplementedError

    def create_batch(self, batch: NewValidationBatch) -> BatchRecord:
        raise NotImplementedError

    def add_members(self, batch_id: BatchId, transaction_ids: Sequence[TransactionId]) -> None:
        raise NotImplementedError

    def apply(
        self,
        user_id: UserId,
        batch_id: BatchId,
        transaction_ids: Sequence[TransactionId],
        currency: str,
    ) -> None:
        raise NotImplementedError

    def lock_batch(self, user_id: UserId, public_id: PublicId) -> BatchRecord | None:
        raise NotImplementedError

    def members_state(self, batch_id: BatchId) -> tuple[BatchMemberState, ...]:
        raise NotImplementedError

    def undo(self, batch_id: BatchId, undone_at: datetime) -> None:
        raise NotImplementedError

    def lock_validation(self, transaction_id: TransactionId) -> bool:
        raise NotImplementedError

    def reset(self, transaction_id: TransactionId) -> None:
        raise NotImplementedError
