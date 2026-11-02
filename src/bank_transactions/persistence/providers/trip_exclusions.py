"""Trip exclusions. Stub: implemented by U8."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import TransactionId, TripId
from bank_transactions.persistence.interfaces import ExclusionRepository


class SqlExclusionRepository(ExclusionRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert_if_absent(self, trip_id: TripId, transaction_id: TransactionId) -> None:
        raise NotImplementedError

    def delete(self, trip_id: TripId, transaction_id: TransactionId) -> None:
        raise NotImplementedError

    def exists(self, trip_id: TripId, transaction_id: TransactionId) -> bool:
        raise NotImplementedError
