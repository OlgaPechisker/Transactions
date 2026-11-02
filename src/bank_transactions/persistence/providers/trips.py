"""Trips. Stub: implemented by U8."""

from datetime import date

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import PublicId, TransactionId, TripId, UserId
from bank_transactions.domain.queries import SortSpec
from bank_transactions.persistence.commands import NewTrip
from bank_transactions.persistence.interfaces import TripRepository
from bank_transactions.persistence.records import (
    TransactionViewRecord,
    TripBreakdownRecord,
    TripMembersRecord,
    TripRecord,
    TripSummaryRecord,
)


class SqlTripRepository(TripRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_with_stats(self, user_id: UserId, sort: SortSpec) -> tuple[TripSummaryRecord, ...]:
        raise NotImplementedError

    def list(self, user_id: UserId) -> tuple[TripRecord, ...]:
        raise NotImplementedError

    def get(self, user_id: UserId, public_id: PublicId) -> TripRecord | None:
        raise NotImplementedError

    def get_id(self, user_id: UserId, public_id: PublicId) -> TripId | None:
        raise NotImplementedError

    def exists_overlap(self, user_id: UserId, start_date: date, end_date: date) -> bool:
        raise NotImplementedError

    def create(self, trip: NewTrip) -> TripRecord:
        raise NotImplementedError

    def members(self, user_id: UserId, trip_id: TripId) -> TripMembersRecord:
        raise NotImplementedError

    def breakdowns(self, user_id: UserId, trip_id: TripId) -> TripBreakdownRecord:
        raise NotImplementedError

    def candidates(
        self,
        user_id: UserId,
        trip_id: TripId,
        window: tuple[date, date] | None,
        query: str | None,
        limit: int,
    ) -> tuple[TransactionViewRecord, ...]:
        raise NotImplementedError

    def is_auto_member(
        self, user_id: UserId, trip_id: TripId, transaction_id: TransactionId
    ) -> bool:
        raise NotImplementedError
