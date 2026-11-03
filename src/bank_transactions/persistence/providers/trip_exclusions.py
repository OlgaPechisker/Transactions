"""Trip exclusions: automatic members a user took out of a trip."""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from bank_transactions.domain.ids import TransactionId, TripId
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import ExclusionRepository
from bank_transactions.persistence.orm.models import TripExclusionRow

_exclusions = TripExclusionRow.__table__


class SqlExclusionRepository(ExclusionRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def insert_if_absent(self, trip_id: TripId, transaction_id: TransactionId) -> None:
        self._session.execute(
            insert(TripExclusionRow)
            .values(trip_id=trip_id, transaction_id=transaction_id)
            .on_conflict_do_nothing()
        )

    @translates_database_errors
    def delete(self, trip_id: TripId, transaction_id: TransactionId) -> None:
        self._session.execute(
            delete(TripExclusionRow).where(
                TripExclusionRow.trip_id == trip_id,
                TripExclusionRow.transaction_id == transaction_id,
            )
        )

    @translates_database_errors
    def exists(self, trip_id: TripId, transaction_id: TransactionId) -> bool:
        return bool(
            self._session.execute(
                select(
                    select(1)
                    .where(
                        _exclusions.c.trip_id == trip_id,
                        _exclusions.c.transaction_id == transaction_id,
                    )
                    .exists()
                )
            ).scalar_one()
        )
