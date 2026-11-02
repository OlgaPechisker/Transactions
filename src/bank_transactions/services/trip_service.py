"""Trips and their membership. Stub: implemented by U8."""

from dataclasses import dataclass
from datetime import date

from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import SortSpec
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    TransactionViewRecord,
    TripBreakdownRecord,
    TripMembersRecord,
    TripRecord,
    TripSummaryRecord,
)
from bank_transactions.ports.clock import Clock
from bank_transactions.ports.currency_catalogue import CurrencyCatalogue
from bank_transactions.services.interfaces import AuthContext


@dataclass(frozen=True)
class NewTripCommand:
    name: str
    currency: str
    start_date: date
    end_date: date


@dataclass(frozen=True)
class TripDetailView:
    trip: TripRecord
    summary: BillingSummaryRecord
    breakdown: TripBreakdownRecord
    members: TripMembersRecord


class TripService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, catalogue: CurrencyCatalogue, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._catalogue = catalogue
        self._clock = clock

    def list(self, context: AuthContext, sort: SortSpec) -> tuple[TripSummaryRecord, ...]:
        raise NotImplementedError

    def create(self, context: AuthContext, command: NewTripCommand) -> TripDetailView:
        """Raises ValidationFailedError or ConflictError(TRIP_DATE_OVERLAP)."""
        raise NotImplementedError

    def get(self, context: AuthContext, trip_id: PublicId) -> TripDetailView:
        raise NotImplementedError

    def candidates(
        self, context: AuthContext, trip_id: PublicId, query: str | None, limit: int
    ) -> tuple[TransactionViewRecord, ...]:
        raise NotImplementedError

    def tag(self, context: AuthContext, trip_id: PublicId, transaction_id: PublicId) -> None:
        raise NotImplementedError

    def untag(self, context: AuthContext, trip_id: PublicId, transaction_id: PublicId) -> None:
        raise NotImplementedError

    def exclude(self, context: AuthContext, trip_id: PublicId, transaction_id: PublicId) -> None:
        """Raises ConflictError(NOT_AUTO_MEMBER) unless the transaction is an automatic member."""
        raise NotImplementedError

    def restore(self, context: AuthContext, trip_id: PublicId, transaction_id: PublicId) -> None:
        raise NotImplementedError
