"""Trips and their membership. Creating a trip serialises the user's trip writers on the user row
lock, so the date-overlap check and the insert cannot interleave with another create."""

from dataclasses import dataclass
from datetime import date, timedelta

from bank_transactions.domain.errors import (
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from bank_transactions.domain.ids import PublicId, TripId
from bank_transactions.domain.queries import SortSpec
from bank_transactions.persistence.commands import NewTrip
from bank_transactions.persistence.interfaces import Isolation, UnitOfWork, UnitOfWorkFactory
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    TransactionRef,
    TransactionViewRecord,
    TripBreakdownRecord,
    TripMembersRecord,
    TripRecord,
    TripSummaryRecord,
)
from bank_transactions.ports.clock import Clock
from bank_transactions.ports.currency_catalogue import CurrencyCatalogue
from bank_transactions.services.interfaces import AuthContext

MAX_NAME_LENGTH = 80
CANDIDATE_DAYS_BEFORE = 30
CANDIDATE_DAYS_AFTER = 7


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
        with self._uow_factory.create(isolation=Isolation.REPEATABLE_READ) as uow:
            return uow.trips.list_with_stats(context.user_id, sort)

    def create(self, context: AuthContext, command: NewTripCommand) -> TripDetailView:
        """Raises ValidationFailedError or ConflictError(TRIP_DATE_OVERLAP)."""
        name = self._validated(command)
        user_id = context.user_id
        with self._uow_factory.create() as uow:
            uow.users.lock(user_id)
            if uow.trips.exists_overlap(user_id, command.start_date, command.end_date):
                raise ConflictError(
                    ConflictError.TRIP_DATE_OVERLAP, "The dates overlap another trip."
                )
            trip = uow.trips.create(
                NewTrip(user_id, name, command.currency, command.start_date, command.end_date)
            )
            detail = self._detail(uow, context, trip)
            uow.commit()
        return detail

    def get(self, context: AuthContext, trip_id: PublicId) -> TripDetailView:
        with self._uow_factory.create(isolation=Isolation.REPEATABLE_READ) as uow:
            trip = uow.trips.get(context.user_id, trip_id)
            if trip is None:
                raise NotFoundError("The trip was not found")
            return self._detail(uow, context, trip)

    def candidates(
        self, context: AuthContext, trip_id: PublicId, query: str | None, limit: int
    ) -> tuple[TransactionViewRecord, ...]:
        text = (query or "").strip() or None
        with self._uow_factory.create(isolation=Isolation.REPEATABLE_READ) as uow:
            trip = uow.trips.get(context.user_id, trip_id)
            if trip is None:
                raise NotFoundError("The trip was not found")
            window = (
                None
                if text is not None
                else (
                    trip.start_date - timedelta(days=CANDIDATE_DAYS_BEFORE),
                    trip.end_date + timedelta(days=CANDIDATE_DAYS_AFTER),
                )
            )
            return uow.trips.candidates(context.user_id, trip.trip_id, window, text, limit)

    def tag(self, context: AuthContext, trip_id: PublicId, transaction_id: PublicId) -> None:
        """Pins the transaction to the trip, moving it from another trip, and lifts this trip's
        exclusion."""
        with self._uow_factory.create() as uow:
            trip, transaction = self._lock(uow, context, trip_id, transaction_id)
            uow.tags.upsert(transaction.transaction_id, trip)
            uow.exclusions.delete(trip, transaction.transaction_id)
            uow.commit()

    def untag(self, context: AuthContext, trip_id: PublicId, transaction_id: PublicId) -> None:
        with self._uow_factory.create() as uow:
            trip, transaction = self._lock(uow, context, trip_id, transaction_id)
            uow.tags.delete(transaction.transaction_id, trip)
            uow.commit()

    def exclude(self, context: AuthContext, trip_id: PublicId, transaction_id: PublicId) -> None:
        """Raises ConflictError(NOT_AUTO_MEMBER) unless the transaction is an automatic member."""
        with self._uow_factory.create() as uow:
            trip, transaction = self._lock(uow, context, trip_id, transaction_id)
            if uow.exclusions.exists(trip, transaction.transaction_id):
                return
            if not uow.trips.is_auto_member(context.user_id, trip, transaction.transaction_id):
                raise ConflictError(
                    ConflictError.NOT_AUTO_MEMBER,
                    "Only an automatic member of the trip can be excluded.",
                )
            uow.exclusions.insert_if_absent(trip, transaction.transaction_id)
            uow.commit()

    def restore(self, context: AuthContext, trip_id: PublicId, transaction_id: PublicId) -> None:
        with self._uow_factory.create() as uow:
            trip, transaction = self._lock(uow, context, trip_id, transaction_id)
            uow.exclusions.delete(trip, transaction.transaction_id)
            uow.commit()

    def _validated(self, command: NewTripCommand) -> str:
        name = command.name.strip()
        if not name:
            raise ValidationFailedError.single("name", "TOO_SHORT", "The name is required.")
        if len(name) > MAX_NAME_LENGTH:
            raise ValidationFailedError.single(
                "name", "TOO_LONG", f"The name is limited to {MAX_NAME_LENGTH} characters."
            )
        if command.currency not in self._catalogue.codes():
            raise ValidationFailedError.single(
                "currency", "INVALID_CURRENCY", "The currency is not a known ISO 4217 code."
            )
        if command.end_date < command.start_date:
            raise ValidationFailedError.single(
                "endDate", "BEFORE_START_DATE", "The end date cannot precede the start date."
            )
        return name

    @staticmethod
    def _lock(
        uow: UnitOfWork, context: AuthContext, trip_id: PublicId, transaction_id: PublicId
    ) -> tuple[TripId, TransactionRef]:
        """The trip id and the locked, visible transaction; anything else is a 404."""
        trip = uow.trips.get_id(context.user_id, trip_id)
        locked = uow.transaction_query.lock_visible(context.user_id, [transaction_id])
        if trip is None or not locked:
            raise NotFoundError("The trip or the transaction was not found")
        return trip, locked[0]

    @staticmethod
    def _detail(uow: UnitOfWork, context: AuthContext, trip: TripRecord) -> TripDetailView:
        breakdown = uow.trips.breakdowns(context.user_id, trip.trip_id)
        members = uow.trips.members(context.user_id, trip.trip_id)
        return TripDetailView(trip, breakdown.overall, breakdown, members)
