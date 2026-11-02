"""Trip tags. Stub: implemented by U8."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import TransactionId, TripId
from bank_transactions.persistence.interfaces import TagRepository


class SqlTagRepository(TagRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, transaction_id: TransactionId, trip_id: TripId) -> None:
        raise NotImplementedError

    def delete(self, transaction_id: TransactionId, trip_id: TripId) -> None:
        raise NotImplementedError
