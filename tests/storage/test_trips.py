"""Trips under real PostgreSQL: membership read through the shared visibility rule, the overlap
check under the user row lock, candidates, tags and exclusions."""

import threading
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from tests.fakes.auth import make_auth_context
from tests.fakes.clock import FakeClock
from tests.fakes.identity import FakeCatalogue

from bank_transactions.domain.enums import ReviewReason, TripMembership
from bank_transactions.domain.errors import ConflictError
from bank_transactions.domain.ids import PublicId, TransactionId, TripId
from bank_transactions.domain.money import USD
from bank_transactions.domain.queries import SortSpec
from bank_transactions.persistence.commands import NewForeignDetail, NewTrip
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import TripRecord
from bank_transactions.services.trip_service import NewTripCommand, TripService

pytestmark = pytest.mark.postgres

FOREIGN = {
    "original_amount": Decimal("10.00"),
    "original_currency": USD,
    "aggregate_foreign_detail": NewForeignDetail(Decimal("0.50"), Decimal("3.5"), Decimal("3.65")),
}
UNKNOWN_FOREIGN = {
    "original_amount": Decimal("10.00"),
    "original_currency": None,
    "aggregate_review_reasons": {ReviewReason.UNKNOWN_CURRENCY},
    "aggregate_foreign_detail": NewForeignDetail(Decimal("0.50"), None, Decimal("3.65")),
}
START_DATE_FIRST = SortSpec((("startDate", False),))
NAME_DESCENDING = SortSpec((("name", True),))


@pytest.fixture
def make_trip(factory: UnitOfWorkFactory) -> Callable[..., TripRecord]:
    def make(
        user: Any,
        name: str = "Rome",
        start: date = date(2026, 7, 1),
        end: date = date(2026, 7, 9),
        currency: str = "USD",
    ) -> TripRecord:
        with factory.create() as uow:
            trip = uow.trips.create(NewTrip(user.user_id, name, currency, start, end))
            uow.commit()
        return trip

    return make


@pytest.fixture
def tag(factory: UnitOfWorkFactory) -> Callable[[TransactionId, TripId], None]:
    def put(transaction_id: TransactionId, trip_id: TripId) -> None:
        with factory.create() as uow:
            uow.tags.upsert(transaction_id, trip_id)
            uow.commit()

    return put


@pytest.fixture
def exclude(factory: UnitOfWorkFactory) -> Callable[[TripId, TransactionId], None]:
    def put(trip_id: TripId, transaction_id: TransactionId) -> None:
        with factory.create() as uow:
            uow.exclusions.insert_if_absent(trip_id, transaction_id)
            uow.commit()

    return put


@pytest.fixture
def rome(seed_user, seed_claimed_card, seed_transaction, make_trip, tag):
    """Ann's Rome trip (USD, July 1-9): two automatic members, one tagged member dated outside
    the trip, a domestic transaction inside the dates, and a transaction of Ben."""
    ann, ben = seed_user("ann@example.com", "Ann"), seed_user("ben@example.com", "Ben")
    card = seed_claimed_card(user=ann, last4="1111", owner_name="Ann Levi")
    ben_card = seed_claimed_card(user=ben, last4="2222", owner_name="Ben Katz")
    trip = make_trip(ann)
    auto_early = seed_transaction(
        card,
        transaction_date=date(2026, 7, 3),
        merchant_name_raw="Colosseum Tickets",
        bank_category_raw="Fun",
        **FOREIGN,
    )
    auto_late = seed_transaction(
        card, transaction_date=date(2026, 7, 5), merchant_name_raw="Trattoria", **FOREIGN
    )
    tagged = seed_transaction(
        card, transaction_date=date(2026, 7, 20), merchant_name_raw="Souvenirs", **FOREIGN
    )
    domestic = seed_transaction(card, transaction_date=date(2026, 7, 4), merchant_name_raw="Pharm")
    bens = seed_transaction(
        ben_card, transaction_date=date(2026, 7, 4), merchant_name_raw="Ben Only", **FOREIGN
    )
    tag(tagged, trip.trip_id)
    return SimpleNamespace(
        ann=ann,
        ben=ben,
        card=card,
        ben_card=ben_card,
        trip=trip,
        auto_early=auto_early,
        auto_late=auto_late,
        tagged=tagged,
        domestic=domestic,
        bens=bens,
    )


def test_a_created_trip_is_found_by_its_public_id_for_its_owner_only(factory, seed_user, make_trip):
    ann, ben = seed_user("ann@example.com", "Ann"), seed_user("ben@example.com", "Ben")

    trip = make_trip(ann, "Rome")

    with factory.create() as uow:
        assert uow.trips.get(ann.user_id, trip.public_id) == trip
        assert uow.trips.get_id(ann.user_id, trip.public_id) == trip.trip_id
        assert uow.trips.get(ben.user_id, trip.public_id) is None
        assert uow.trips.get_id(ben.user_id, trip.public_id) is None
        assert uow.trips.get(ann.user_id, PublicId("trp_missing")) is None
    assert trip.public_id.startswith("trp_")


def test_the_trip_list_is_the_trips_of_the_user_newest_first(factory, seed_user, make_trip):
    ann, ben = seed_user("ann@example.com", "Ann"), seed_user("ben@example.com", "Ben")
    make_trip(ann, "June", date(2026, 6, 1), date(2026, 6, 5))
    make_trip(ann, "August", date(2026, 8, 1), date(2026, 8, 5))
    make_trip(ben, "Ben's", date(2026, 7, 1), date(2026, 7, 5))

    with factory.create() as uow:
        names = [trip.name for trip in uow.trips.list(ann.user_id)]

    assert names == ["August", "June"]


@pytest.mark.parametrize(
    ("start", "end", "overlaps"),
    [
        (date(2026, 7, 9), date(2026, 7, 12), True),
        (date(2026, 6, 28), date(2026, 7, 1), True),
        (date(2026, 7, 3), date(2026, 7, 4), True),
        (date(2026, 6, 1), date(2026, 7, 31), True),
        (date(2026, 7, 10), date(2026, 7, 12), False),
        (date(2026, 6, 25), date(2026, 6, 30), False),
    ],
)
def test_a_range_overlaps_a_trip_when_it_shares_a_day_with_it(factory, rome, start, end, overlaps):
    with factory.create() as uow:
        assert uow.trips.exists_overlap(rome.ann.user_id, start, end) is overlaps


def test_the_trips_of_another_user_do_not_overlap(factory, rome):
    with factory.create() as uow:
        assert (
            uow.trips.exists_overlap(rome.ben.user_id, date(2026, 7, 1), date(2026, 7, 9)) is False
        )


def test_the_trip_list_counts_tagged_and_automatic_members_and_sums_them(factory, rome, make_trip):
    make_trip(rome.ann, "Empty", date(2026, 1, 1), date(2026, 1, 2))

    with factory.create() as uow:
        stats = {
            s.trip.name: s for s in uow.trips.list_with_stats(rome.ann.user_id, START_DATE_FIRST)
        }

    assert (stats["Rome"].tagged_count, stats["Rome"].auto_count) == (1, 2)
    assert stats["Rome"].summary.count == 3
    assert [(t.currency, t.count, t.net) for t in stats["Rome"].summary.totals] == [
        ("ILS", 3, Decimal("360.00"))
    ]
    assert (stats["Empty"].tagged_count, stats["Empty"].auto_count) == (0, 0)
    assert stats["Empty"].summary.count == 0


def test_the_trip_list_follows_the_requested_sort(factory, seed_user, make_trip):
    ann = seed_user()
    make_trip(ann, "bravo", date(2026, 3, 1), date(2026, 3, 2))
    make_trip(ann, "Alpha", date(2026, 5, 1), date(2026, 5, 2))
    make_trip(ann, "Charlie", date(2026, 1, 1), date(2026, 1, 2))

    with factory.create() as uow:
        by_start = [s.trip.name for s in uow.trips.list_with_stats(ann.user_id, START_DATE_FIRST)]
        by_name = [s.trip.name for s in uow.trips.list_with_stats(ann.user_id, NAME_DESCENDING)]

    assert by_start == ["Charlie", "bravo", "Alpha"]
    assert by_name == ["Charlie", "bravo", "Alpha"]


def test_the_trip_list_never_counts_the_transactions_of_another_user(factory, rome, make_trip):
    make_trip(rome.ben, "Ben in Rome", date(2026, 7, 1), date(2026, 7, 9))

    with factory.create() as uow:
        (stats,) = uow.trips.list_with_stats(rome.ben.user_id, START_DATE_FIRST)

    assert (stats.tagged_count, stats.auto_count, stats.summary.count) == (0, 1, 1)


def test_the_members_are_split_into_tagged_automatic_and_excluded(factory, rome, exclude):
    exclude(rome.trip.trip_id, rome.auto_late)

    with factory.create() as uow:
        members = uow.trips.members(rome.ann.user_id, rome.trip.trip_id)

    assert [m.transaction_id for m in members.tagged] == [rome.tagged]
    assert [m.transaction_id for m in members.auto] == [rome.auto_early]
    assert [m.transaction_id for m in members.excluded] == [rome.auto_late]
    assert members.tagged[0].trip.membership is TripMembership.TAGGED
    assert members.auto[0].trip.membership is TripMembership.AUTO


def test_the_members_of_the_trip_of_another_user_are_empty(factory, rome):
    with factory.create() as uow:
        members = uow.trips.members(rome.ben.user_id, rome.trip.trip_id)

    assert (members.tagged, members.auto, members.excluded) == ((), (), ())


def test_the_breakdowns_cover_every_member_by_category_and_day(factory, rome):
    with factory.create() as uow:
        breakdown = uow.trips.breakdowns(rome.ann.user_id, rome.trip.trip_id)

    assert breakdown.overall.count == 3
    assert breakdown.outside_range.count == 1
    assert {g.key: g.summary.count for g in breakdown.by_category} == {"Fun": 1, None: 2}
    assert [(d.day, d.summary.count) for d in breakdown.by_day] == [
        (date(2026, 7, 3), 1),
        (date(2026, 7, 5), 1),
        (date(2026, 7, 20), 1),
    ]


def test_the_breakdown_of_a_trip_without_members_is_empty(factory, seed_user, make_trip):
    ann = seed_user()
    trip = make_trip(ann)

    with factory.create() as uow:
        breakdown = uow.trips.breakdowns(ann.user_id, trip.trip_id)

    assert breakdown.overall.count == 0
    assert (breakdown.by_category, breakdown.by_day) == ((), ())


def test_the_breakdown_counts_members_with_an_unknown_original_currency(
    factory, rome, seed_transaction, tag
):
    unknown = seed_transaction(
        rome.card, transaction_date=date(2026, 7, 6), merchant_name_raw="Mystery", **UNKNOWN_FOREIGN
    )
    tag(unknown, rome.trip.trip_id)

    with factory.create() as uow:
        breakdown = uow.trips.breakdowns(rome.ann.user_id, rome.trip.trip_id)

    assert breakdown.overall.count == 4
    assert breakdown.overall.unknown_currency_count == 1


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ("auto_early", True),
        ("tagged", False),
        ("domestic", False),
        ("bens", False),
    ],
)
def test_only_an_untagged_visible_foreign_transaction_in_range_is_an_automatic_member(
    factory, rome, member, expected
):
    with factory.create() as uow:
        result = uow.trips.is_auto_member(
            rome.ann.user_id, rome.trip.trip_id, getattr(rome, member)
        )

    assert result is expected


def test_a_transaction_tagged_to_another_trip_or_excluded_is_not_an_automatic_member(
    factory, rome, make_trip, tag, exclude
):
    paris = make_trip(rome.ann, "Paris", date(2026, 9, 1), date(2026, 9, 9))
    tag(rome.auto_early, paris.trip_id)
    exclude(rome.trip.trip_id, rome.auto_late)

    with factory.create() as uow:
        trips = uow.trips
        assert trips.is_auto_member(rome.ann.user_id, rome.trip.trip_id, rome.auto_early) is False
        assert trips.is_auto_member(rome.ann.user_id, rome.trip.trip_id, rome.auto_late) is False


def test_a_trip_in_another_currency_has_no_automatic_members(factory, rome, make_trip):
    euro = make_trip(rome.ann, "Euro", date(2026, 9, 1), date(2026, 9, 9), currency="EUR")

    with factory.create() as uow:
        members = uow.trips.members(rome.ann.user_id, euro.trip_id)

    assert members.auto == ()


def test_candidates_without_a_search_are_the_non_members_inside_the_window(factory, rome):
    with factory.create() as uow:
        found = uow.trips.candidates(
            rome.ann.user_id, rome.trip.trip_id, (date(2026, 6, 1), date(2026, 7, 31)), None, 8
        )

    assert [c.transaction_id for c in found] == [rome.domestic]


def test_candidates_include_transactions_of_another_trip_and_excluded_ones(
    factory, rome, make_trip, tag, exclude
):
    paris = make_trip(rome.ann, "Paris", date(2026, 9, 1), date(2026, 9, 9))
    tag(rome.auto_early, paris.trip_id)
    exclude(rome.trip.trip_id, rome.auto_late)

    with factory.create() as uow:
        found = uow.trips.candidates(
            rome.ann.user_id, rome.trip.trip_id, (date(2026, 7, 1), date(2026, 7, 9)), None, 8
        )

    assert [c.transaction_id for c in found] == [rome.auto_late, rome.domestic, rome.auto_early]
    assert found[2].trip.name == "Paris"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("colosseum", ["cafe"]),
        ("COLOSSEUM", ["cafe"]),
        ("2025-01", ["old"]),
        ("2026-07-04", ["domestic", "cafe"]),
        ("fun", ["old"]),
        ("dining", ["cafe"]),
        ("%", []),
        ("nothing", []),
    ],
)
def test_a_search_matches_merchant_date_prefix_and_category_across_every_date(
    factory, seed_transaction, rome, query, expected
):
    cafe = seed_transaction(
        rome.card,
        transaction_date=date(2026, 7, 4),
        merchant_name_raw="Colosseum Cafe",
        bank_category_raw="Dining",
    )
    old = seed_transaction(
        rome.card,
        transaction_date=date(2025, 1, 1),
        merchant_name_raw="Old Shop",
        bank_category_raw="Fun",
    )
    names = {rome.domestic: "domestic", cafe: "cafe", old: "old"}

    with factory.create() as uow:
        found = uow.trips.candidates(rome.ann.user_id, rome.trip.trip_id, None, query, 8)

    assert [names[c.transaction_id] for c in found] == expected


def test_candidates_are_limited_and_never_include_another_users_transactions(factory, rome):
    with factory.create() as uow:
        limited = uow.trips.candidates(rome.ann.user_id, rome.trip.trip_id, None, None, 1)
        everything = uow.trips.candidates(rome.ann.user_id, rome.trip.trip_id, None, None, 50)

    assert len(limited) == 1
    assert rome.bens not in [c.transaction_id for c in everything]


def test_tagging_a_transaction_to_another_trip_moves_it(factory, rome, make_trip, tag, sql):
    paris = make_trip(rome.ann, "Paris", date(2026, 9, 1), date(2026, 9, 9))

    tag(rome.tagged, paris.trip_id)
    tag(rome.tagged, paris.trip_id)

    rows = sql("SELECT trip_id FROM trip_transaction_tags WHERE transaction_id = :t", t=rome.tagged)
    assert [row[0] for row in rows] == [paris.trip_id]


def test_removing_a_tag_of_another_trip_keeps_the_tag(factory, rome, make_trip, sql):
    paris = make_trip(rome.ann, "Paris", date(2026, 9, 1), date(2026, 9, 9))

    with factory.create() as uow:
        uow.tags.delete(rome.tagged, paris.trip_id)
        uow.commit()
    assert sql("SELECT 1 FROM trip_transaction_tags WHERE transaction_id = :t", t=rome.tagged)

    with factory.create() as uow:
        uow.tags.delete(rome.tagged, rome.trip.trip_id)
        uow.commit()
    assert not sql("SELECT 1 FROM trip_transaction_tags WHERE transaction_id = :t", t=rome.tagged)


def test_an_exclusion_can_be_repeated_checked_and_removed(factory, rome, exclude):
    exclude(rome.trip.trip_id, rome.auto_early)
    exclude(rome.trip.trip_id, rome.auto_early)

    with factory.create() as uow:
        assert uow.exclusions.exists(rome.trip.trip_id, rome.auto_early) is True
        assert uow.exclusions.exists(rome.trip.trip_id, rome.auto_late) is False
        uow.exclusions.delete(rome.trip.trip_id, rome.auto_early)
        uow.exclusions.delete(rome.trip.trip_id, rome.auto_early)
        uow.commit()

    with factory.create() as uow:
        assert uow.exclusions.exists(rome.trip.trip_id, rome.auto_early) is False


def new_service(factory: UnitOfWorkFactory) -> TripService:
    return TripService(factory, FakeCatalogue(), FakeClock())


def test_two_concurrent_creates_of_overlapping_trips_leave_exactly_one_trip(
    factory, seed_user, sql
):
    ann = seed_user()
    service = new_service(factory)
    start = threading.Barrier(2)
    outcomes: list[str] = []

    def create(name: str) -> None:
        start.wait()
        command = NewTripCommand(name, "USD", date(2026, 7, 1), date(2026, 7, 9))
        try:
            service.create(make_auth_context(user_id=ann.user_id), command)
            outcomes.append("created")
        except ConflictError as error:
            outcomes.append(error.code)

    threads = [threading.Thread(target=create, args=(name,)) for name in ("Rome", "Roma")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == [ConflictError.TRIP_DATE_OVERLAP, "created"]
    assert len(sql("SELECT 1 FROM trips WHERE user_id = :u", u=ann.user_id)) == 1


def test_a_create_waits_for_the_writer_holding_the_user_lock_and_then_sees_its_trip(
    factory, seed_user, sql
):
    ann = seed_user()
    service = new_service(factory)
    outcome: list[object] = []

    def create() -> None:
        command = NewTripCommand("Roma", "USD", date(2026, 7, 5), date(2026, 7, 12))
        try:
            outcome.append(service.create(make_auth_context(user_id=ann.user_id), command))
        except ConflictError as error:
            outcome.append(error)

    with factory.create() as holder:
        holder.users.lock(ann.user_id)
        thread = threading.Thread(target=create)
        thread.start()
        time.sleep(0.5)
        assert outcome == [] and thread.is_alive()
        holder.trips.create(NewTrip(ann.user_id, "Rome", "USD", date(2026, 7, 1), date(2026, 7, 9)))
        holder.commit()
    thread.join(timeout=30)

    assert len(outcome) == 1
    assert isinstance(outcome[0], ConflictError)
    assert outcome[0].code == ConflictError.TRIP_DATE_OVERLAP
    assert len(sql("SELECT 1 FROM trips WHERE user_id = :u", u=ann.user_id)) == 1


def test_creating_trips_of_different_users_does_not_wait(factory, seed_user):
    ann, ben = seed_user("ann@example.com", "Ann"), seed_user("ben@example.com", "Ben")
    service = new_service(factory)

    with factory.create() as holder:
        holder.users.lock(ann.user_id)
        command = NewTripCommand("Rome", "USD", date(2026, 7, 1), date(2026, 7, 9))
        detail = service.create(make_auth_context(user_id=ben.user_id), command)

    assert detail.trip.name == "Rome"
