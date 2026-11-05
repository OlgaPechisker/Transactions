"""The trip service: validation, inclusive overlap, and the tag / exclude rules."""

from dataclasses import dataclass, field
from datetime import date

import pytest
from tests.fakes.auth import make_auth_context
from tests.fakes.clock import FakeClock
from tests.fakes.identity import FakeCatalogue
from tests.fakes.unit_of_work import InMemoryUnitOfWorkFactory

from bank_transactions.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from bank_transactions.domain.ids import PublicId, TransactionId, TripId
from bank_transactions.persistence.commands import NewTrip
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    TransactionRef,
    TripBreakdownRecord,
    TripMembersRecord,
    TripRecord,
)
from bank_transactions.services.trip_service import NewTripCommand, TripService

ANN = make_auth_context(user_id=1)
EMPTY = BillingSummaryRecord(0, 0)


@dataclass
class World:
    """Trips, tags, exclusions and which transactions fall into a trip's dates and currency, for
    every user. A transaction is visible to the user that owns it."""

    trips: dict[int, tuple[int, TripRecord]] = field(default_factory=dict)  # id -> (owner, trip)
    owners: dict[str, tuple[int, int]] = field(default_factory=dict)  # public id -> (owner, id)
    in_range: set[tuple[int, int]] = field(default_factory=set)  # (trip id, transaction id)
    tags: dict[int, int] = field(default_factory=dict)  # transaction id -> trip id
    exclusions: set[tuple[int, int]] = field(default_factory=set)
    candidate_calls: list[tuple] = field(default_factory=list)

    def add_trip(self, owner: int, name: str, start: date, end: date) -> TripRecord:
        trip_id = len(self.trips) + 1
        trip = TripRecord(TripId(trip_id), PublicId(f"trp_{trip_id}"), name, "EUR", start, end)
        self.trips[trip_id] = (owner, trip)
        return trip

    def add_transaction(
        self, owner: int, public_id: str, trips: tuple[TripRecord, ...] = ()
    ) -> int:
        transaction_id = len(self.owners) + 1
        self.owners[public_id] = (owner, transaction_id)
        self.in_range.update((trip.trip_id, transaction_id) for trip in trips)
        return transaction_id


class FakeTrips:
    def __init__(self, world: World) -> None:
        self._w = world

    def get(self, user_id, public_id):
        return next(
            (
                trip
                for owner, trip in self._w.trips.values()
                if owner == user_id and trip.public_id == public_id
            ),
            None,
        )

    def get_id(self, user_id, public_id):
        trip = self.get(user_id, public_id)
        return None if trip is None else trip.trip_id

    def exists_overlap(self, user_id, start, end):
        return any(
            owner == user_id and trip.start_date <= end and trip.end_date >= start
            for owner, trip in self._w.trips.values()
        )

    def create(self, trip: NewTrip):
        return self._w.add_trip(trip.user_id, trip.name, trip.start_date, trip.end_date)

    def members(self, user_id, trip_id):
        return TripMembersRecord()

    def breakdowns(self, user_id, trip_id):
        return TripBreakdownRecord(EMPTY, EMPTY)

    def candidates(self, user_id, trip_id, window, query, limit):
        self._w.candidate_calls.append((window, query, limit))
        return ()

    def is_auto_member(self, user_id, trip_id, transaction_id):
        key = (trip_id, transaction_id)
        return (
            key in self._w.in_range
            and transaction_id not in self._w.tags
            and key not in self._w.exclusions
        )


class FakeTags:
    def __init__(self, world: World) -> None:
        self._w = world

    def upsert(self, transaction_id, trip_id):
        self._w.tags[transaction_id] = trip_id

    def delete(self, transaction_id, trip_id):
        if self._w.tags.get(transaction_id) == trip_id:
            del self._w.tags[transaction_id]


class FakeExclusions:
    def __init__(self, world: World) -> None:
        self._w = world

    def insert_if_absent(self, trip_id, transaction_id):
        self._w.exclusions.add((trip_id, transaction_id))

    def delete(self, trip_id, transaction_id):
        self._w.exclusions.discard((trip_id, transaction_id))

    def exists(self, trip_id, transaction_id):
        return (trip_id, transaction_id) in self._w.exclusions


class FakeUsers:
    def lock(self, user_id):
        pass


class FakeTransactionQuery:
    def __init__(self, world: World) -> None:
        self._w = world

    def lock_visible(self, user_id, public_ids):
        return tuple(
            TransactionRef(TransactionId(self._w.owners[p][1]), PublicId(p))
            for p in public_ids
            if p in self._w.owners and self._w.owners[p][0] == user_id
        )


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def service(world: World) -> TripService:
    factory = InMemoryUnitOfWorkFactory()
    factory.template.trips = FakeTrips(world)
    factory.template.tags = FakeTags(world)
    factory.template.exclusions = FakeExclusions(world)
    factory.template.users = FakeUsers()
    factory.template.transaction_query = FakeTransactionQuery(world)
    return TripService(factory, FakeCatalogue(), FakeClock())


def command(start: date, end: date, name: str = "Rome", currency: str = "EUR") -> NewTripCommand:
    return NewTripCommand(name, currency, start, end)


def test_creating_a_trip_returns_its_detail_with_a_trimmed_name(service: TripService):
    detail = service.create(ANN, command(date(2026, 7, 1), date(2026, 7, 9), name="  Rome  "))

    assert detail.trip.name == "Rome"
    assert (detail.trip.start_date, detail.trip.end_date) == (date(2026, 7, 1), date(2026, 7, 9))


def test_a_trip_touching_the_last_day_of_another_trip_overlaps(service: TripService, world: World):
    world.add_trip(1, "January", date(2026, 1, 1), date(2026, 1, 5))

    with pytest.raises(ConflictError) as error:
        service.create(ANN, command(date(2026, 1, 5), date(2026, 1, 10)))

    assert error.value.code == ConflictError.TRIP_DATE_OVERLAP


def test_a_trip_touching_the_first_day_of_another_trip_overlaps(service: TripService, world: World):
    world.add_trip(1, "January", date(2026, 1, 10), date(2026, 1, 15))

    with pytest.raises(ConflictError):
        service.create(ANN, command(date(2026, 1, 1), date(2026, 1, 10)))


def test_a_trip_inside_another_trip_overlaps(service: TripService, world: World):
    world.add_trip(1, "January", date(2026, 1, 1), date(2026, 1, 31))

    with pytest.raises(ConflictError):
        service.create(ANN, command(date(2026, 1, 10), date(2026, 1, 12)))


def test_a_trip_starting_the_day_after_another_ends_does_not_overlap(
    service: TripService, world: World
):
    world.add_trip(1, "January", date(2026, 1, 1), date(2026, 1, 5))

    service.create(ANN, command(date(2026, 1, 6), date(2026, 1, 10)))

    assert len(world.trips) == 2


def test_the_trips_of_another_user_never_overlap(service: TripService, world: World):
    world.add_trip(2, "January", date(2026, 1, 1), date(2026, 1, 31))

    service.create(ANN, command(date(2026, 1, 10), date(2026, 1, 12)))

    assert {owner for owner, _ in world.trips.values()} == {1, 2}


@pytest.mark.parametrize(
    ("changes", "field_name", "code"),
    [
        ({"name": "   "}, "name", "TOO_SHORT"),
        ({"name": "x" * 81}, "name", "TOO_LONG"),
        ({"currency": "ZZZ"}, "currency", "INVALID_CURRENCY"),
        ({"start": date(2026, 7, 9), "end": date(2026, 7, 1)}, "endDate", "BEFORE_START_DATE"),
    ],
)
def test_an_invalid_trip_names_the_field_and_is_not_created(
    service: TripService, world: World, changes: dict, field_name: str, code: str
):
    arguments = {
        "start": date(2026, 7, 1),
        "end": date(2026, 7, 9),
        "name": "Rome",
        "currency": "EUR",
        **changes,
    }

    with pytest.raises(ValidationFailedError) as error:
        service.create(ANN, command(**arguments))

    assert [(e.field, e.code) for e in error.value.errors] == [(field_name, code)]
    assert world.trips == {}


def test_a_trip_of_one_day_is_valid(service: TripService):
    detail = service.create(ANN, command(date(2026, 7, 1), date(2026, 7, 1)))

    assert detail.trip.start_date == detail.trip.end_date


def test_getting_the_trip_of_another_user_is_not_found(service: TripService, world: World):
    trip = world.add_trip(2, "Rome", date(2026, 7, 1), date(2026, 7, 9))

    with pytest.raises(NotFoundError):
        service.get(ANN, trip.public_id)


def test_the_candidates_without_a_search_span_a_month_before_and_a_week_after(
    service: TripService, world: World
):
    trip = world.add_trip(1, "Rome", date(2026, 7, 10), date(2026, 7, 20))

    service.candidates(ANN, trip.public_id, None, 8)

    assert world.candidate_calls == [((date(2026, 6, 10), date(2026, 7, 27)), None, 8)]


def test_a_search_looks_at_every_date(service: TripService, world: World):
    trip = world.add_trip(1, "Rome", date(2026, 7, 10), date(2026, 7, 20))

    service.candidates(ANN, trip.public_id, " pharm ", 5)

    assert world.candidate_calls == [(None, "pharm", 5)]


def test_the_candidates_of_a_trip_of_another_user_are_not_found(service: TripService, world: World):
    trip = world.add_trip(2, "Rome", date(2026, 7, 10), date(2026, 7, 20))

    with pytest.raises(NotFoundError):
        service.candidates(ANN, trip.public_id, None, 8)


def test_tagging_pins_the_transaction_to_the_trip(service: TripService, world: World):
    trip = world.add_trip(1, "Rome", date(2026, 7, 1), date(2026, 7, 9))
    transaction = world.add_transaction(1, "txn_1")

    service.tag(ANN, trip.public_id, PublicId("txn_1"))
    service.tag(ANN, trip.public_id, PublicId("txn_1"))

    assert world.tags == {transaction: trip.trip_id}


def test_tagging_moves_the_transaction_from_another_trip_and_lifts_the_exclusion(
    service: TripService, world: World
):
    rome = world.add_trip(1, "Rome", date(2026, 7, 1), date(2026, 7, 9))
    paris = world.add_trip(1, "Paris", date(2026, 8, 1), date(2026, 8, 9))
    transaction = world.add_transaction(1, "txn_1", (paris,))
    service.tag(ANN, rome.public_id, PublicId("txn_1"))
    world.exclusions.add((paris.trip_id, transaction))

    service.tag(ANN, paris.public_id, PublicId("txn_1"))

    assert world.tags == {transaction: paris.trip_id}
    assert (paris.trip_id, transaction) not in world.exclusions


def test_untagging_only_removes_the_tag_of_this_trip(service: TripService, world: World):
    rome = world.add_trip(1, "Rome", date(2026, 7, 1), date(2026, 7, 9))
    paris = world.add_trip(1, "Paris", date(2026, 8, 1), date(2026, 8, 9))
    transaction = world.add_transaction(1, "txn_1")
    service.tag(ANN, paris.public_id, PublicId("txn_1"))

    service.untag(ANN, rome.public_id, PublicId("txn_1"))
    assert world.tags == {transaction: paris.trip_id}

    service.untag(ANN, paris.public_id, PublicId("txn_1"))
    assert world.tags == {}


def test_an_automatic_member_can_be_excluded_and_restored(service: TripService, world: World):
    trip = world.add_trip(1, "Rome", date(2026, 7, 1), date(2026, 7, 9))
    transaction = world.add_transaction(1, "txn_1", (trip,))

    service.exclude(ANN, trip.public_id, PublicId("txn_1"))
    service.exclude(ANN, trip.public_id, PublicId("txn_1"))
    assert world.exclusions == {(trip.trip_id, transaction)}

    service.restore(ANN, trip.public_id, PublicId("txn_1"))
    service.restore(ANN, trip.public_id, PublicId("txn_1"))
    assert world.exclusions == set()


def test_a_transaction_outside_the_trip_cannot_be_excluded(service: TripService, world: World):
    trip = world.add_trip(1, "Rome", date(2026, 7, 1), date(2026, 7, 9))
    world.add_transaction(1, "txn_1")

    with pytest.raises(ConflictError) as error:
        service.exclude(ANN, trip.public_id, PublicId("txn_1"))

    assert error.value.code == ConflictError.NOT_AUTO_MEMBER
    assert world.exclusions == set()


def test_a_tagged_transaction_cannot_be_excluded(service: TripService, world: World):
    trip = world.add_trip(1, "Rome", date(2026, 7, 1), date(2026, 7, 9))
    world.add_transaction(1, "txn_1", (trip,))
    service.tag(ANN, trip.public_id, PublicId("txn_1"))

    with pytest.raises(ConflictError) as error:
        service.exclude(ANN, trip.public_id, PublicId("txn_1"))

    assert error.value.code == ConflictError.NOT_AUTO_MEMBER


@pytest.mark.parametrize("operation", ["tag", "untag", "exclude", "restore"])
def test_the_transaction_of_another_user_is_not_found(
    service: TripService, world: World, operation: str
):
    trip = world.add_trip(1, "Rome", date(2026, 7, 1), date(2026, 7, 9))
    world.add_transaction(2, "txn_1", (trip,))

    with pytest.raises(NotFoundError):
        getattr(service, operation)(ANN, trip.public_id, PublicId("txn_1"))

    assert world.tags == {} and world.exclusions == set()


@pytest.mark.parametrize("operation", ["tag", "untag", "exclude", "restore"])
def test_the_trip_of_another_user_is_not_found(service: TripService, world: World, operation: str):
    trip = world.add_trip(2, "Rome", date(2026, 7, 1), date(2026, 7, 9))
    world.add_transaction(1, "txn_1", (trip,))

    with pytest.raises(NotFoundError):
        getattr(service, operation)(ANN, trip.public_id, PublicId("txn_1"))

    assert world.tags == {} and world.exclusions == set()
