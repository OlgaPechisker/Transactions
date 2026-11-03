"""Trip tags: a transaction is pinned to at most one trip (the primary key is the transaction)."""

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from bank_transactions.domain.ids import TransactionId, TripId
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import TagRepository
from bank_transactions.persistence.orm.models import TripTagRow

_tags = TripTagRow.__table__


class SqlTagRepository(TagRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def upsert(self, transaction_id: TransactionId, trip_id: TripId) -> None:
        statement = insert(TripTagRow).values(transaction_id=transaction_id, trip_id=trip_id)
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[_tags.c.transaction_id],
                set_={"trip_id": statement.excluded.trip_id},
            )
        )

    @translates_database_errors
    def delete(self, transaction_id: TransactionId, trip_id: TripId) -> None:
        self._session.execute(
            delete(TripTagRow).where(
                TripTagRow.transaction_id == transaction_id, TripTagRow.trip_id == trip_id
            )
        )
