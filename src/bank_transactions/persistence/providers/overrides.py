"""Per-transaction category overrides. Stub: implemented by U7."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import CustomCategoryId, TransactionId, UserId
from bank_transactions.persistence.interfaces import OverrideRepository


class SqlOverrideRepository(OverrideRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(
        self, user_id: UserId, transaction_id: TransactionId, category_id: CustomCategoryId
    ) -> None:
        raise NotImplementedError

    def delete(self, user_id: UserId, transaction_id: TransactionId) -> None:
        raise NotImplementedError
