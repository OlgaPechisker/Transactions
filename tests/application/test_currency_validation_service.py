"""The currency-validation service: batch rules, undo and move-back, against an in-memory world."""

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from tests.fakes.auth import make_auth_context
from tests.fakes.clock import FakeClock
from tests.fakes.identity import FakeCatalogue
from tests.fakes.unit_of_work import InMemoryUnitOfWorkFactory

from bank_transactions.domain.enums import ValidationStatus
from bank_transactions.domain.errors import (
    ConflictError,
    InvalidQueryError,
    NotFoundError,
    ValidationFailedError,
)
from bank_transactions.domain.ids import BatchId, PublicId, TransactionId
from bank_transactions.domain.queries import (
    Page,
    PageRequest,
    PendingValidationFilter,
    SortSpec,
)
from bank_transactions.persistence.interfaces import Isolation
from bank_transactions.persistence.records import (
    BatchMemberState,
    BatchRecord,
    CountryOptionRecord,
    TransactionRef,
)
from bank_transactions.services.currency_validation_service import (
    BatchCommand,
    CurrencyValidationService,
    ValidationItemsQuery,
)

ANN = make_auth_context(user_id=1)
BEN = make_auth_context(user_id=2)
EVERY_PENDING = PendingValidationFilter()


@dataclass
class Row:
    owner: int
    public_id: str
    country: str | None = None
    day: date = date(2026, 7, 1)
    merchant: str = "Shop"
    pending: bool = True
    currency: str | None = None
    validated_in: int | None = None


@dataclass
class World:
    """The state both fake repositories read and write, as the database would hold it."""

    rows: dict[int, Row] = field(default_factory=dict)
    batches: dict[int, BatchRecord] = field(default_factory=dict)
    members: dict[int, list[int]] = field(default_factory=dict)

    def add(self, owner: int, **fields: object) -> int:
        transaction_id = len(self.rows) + 1
        self.rows[transaction_id] = Row(owner, f"txn_{transaction_id}", **fields)  # type: ignore[arg-type]
        return transaction_id

    def public(self, *transaction_ids: int) -> list[PublicId]:
        return [PublicId(self.rows[i].public_id) for i in transaction_ids]


class FakeTransactionQuery:
    def __init__(self, world: World) -> None:
        self._world = world

    def _visible(self, user_id: int):
        return {i: row for i, row in self._world.rows.items() if row.owner == user_id}

    def lock_visible(self, user_id, public_ids):
        found = [
            TransactionRef(TransactionId(i), PublicId(row.public_id))
            for i, row in sorted(self._visible(user_id).items())
            if row.public_id in public_ids
        ]
        return tuple(found)

    def lock_pending_matching(self, user_id, filter: PendingValidationFilter):
        return tuple(
            TransactionRef(TransactionId(i), PublicId(row.public_id))
            for i, row in sorted(self._visible(user_id).items())
            if row.pending and _matches(row, filter)
        )

    def visible_id(self, user_id, public_id):
        return next(
            (
                TransactionId(i)
                for i, row in self._visible(user_id).items()
                if row.public_id == public_id
            ),
            None,
        )


def _matches(row: Row, filter: PendingValidationFilter) -> bool:
    return (
        (not filter.countries or row.country in filter.countries)
        and (filter.date_from is None or row.day >= filter.date_from)
        and (filter.date_to is None or row.day <= filter.date_to)
        and (
            not filter.merchants or any(m.lower() in row.merchant.lower() for m in filter.merchants)
        )
    )


class FakeCurrencyValidation:
    def __init__(self, world: World) -> None:
        self._world = world

    def count_pending(self, user_id):
        return sum(1 for r in self._world.rows.values() if r.owner == user_id and r.pending)

    def count_validated(self, user_id):
        return sum(
            1
            for r in self._world.rows.values()
            if r.owner == user_id and r.validated_in is not None
        )

    def pending_countries(self, user_id):
        counts: dict[str, int] = {}
        for row in self._world.rows.values():
            if row.owner == user_id and row.pending and row.country:
                counts[row.country] = counts.get(row.country, 0) + 1
        return tuple(CountryOptionRecord(code, n) for code, n in sorted(counts.items()))

    def page_items(self, user_id, status, filter, sort, page):
        return Page((), page.page, page.size, 0)

    def validated_among(self, user_id, transaction_ids):
        return frozenset(
            TransactionId(i)
            for i in transaction_ids
            if self._world.rows[i].validated_in is not None
        )

    def pending_among(self, user_id, transaction_ids):
        return frozenset(TransactionId(i) for i in transaction_ids if self._world.rows[i].pending)

    def create_batch(self, batch):
        batch_id = len(self._world.batches) + 1
        record = BatchRecord(
            BatchId(batch_id),
            PublicId(f"cvb_{batch_id}"),
            batch.user_id,
            batch.currency,
            batch.created_at,
            None,
            0,
        )
        self._world.batches[batch_id] = record
        self._world.members[batch_id] = []
        return record

    def add_members(self, batch_id, transaction_ids):
        self._world.members[batch_id].extend(transaction_ids)

    def apply(self, user_id, batch_id, transaction_ids, currency):
        for i in transaction_ids:
            row = self._world.rows[i]
            row.pending, row.currency, row.validated_in = False, currency, batch_id

    def lock_batch(self, user_id, public_id):
        return next(
            (
                b
                for b in self._world.batches.values()
                if b.public_id == public_id and b.user_id == user_id
            ),
            None,
        )

    def members_state(self, batch_id):
        return tuple(
            BatchMemberState(
                TransactionId(i),
                PublicId(self._world.rows[i].public_id),
                None
                if self._world.rows[i].validated_in is None
                else BatchId(self._world.rows[i].validated_in),
                self._world.rows[i].currency,
            )
            for i in self._world.members[batch_id]
        )

    def undo(self, batch_id, undone_at):
        for i in self._world.members[batch_id]:
            row = self._world.rows[i]
            row.pending, row.currency, row.validated_in = True, None, None
        self._world.batches[batch_id] = replace(self._world.batches[batch_id], undone_at=undone_at)

    def lock_validation(self, transaction_id):
        return self._world.rows[transaction_id].validated_in is not None

    def reset(self, transaction_id):
        row = self._world.rows[transaction_id]
        row.pending, row.currency, row.validated_in = True, None, None


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def factory(world: World) -> InMemoryUnitOfWorkFactory:
    factory = InMemoryUnitOfWorkFactory()
    factory.template.transaction_query = FakeTransactionQuery(world)
    factory.template.currency_validation = FakeCurrencyValidation(world)
    return factory


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 8, 1, 9, 0, tzinfo=UTC))


@pytest.fixture
def service(factory: InMemoryUnitOfWorkFactory, clock: FakeClock) -> CurrencyValidationService:
    return CurrencyValidationService(factory, FakeCatalogue(), clock)


def by_ids(world: World, *transaction_ids: int, currency: str = "EUR") -> BatchCommand:
    return BatchCommand(currency, tuple(world.public(*transaction_ids)), None)


def by_filter(selection: PendingValidationFilter, currency: str = "EUR") -> BatchCommand:
    return BatchCommand(currency, None, selection)


def commits(factory: InMemoryUnitOfWorkFactory) -> int:
    return sum(1 for uow, _ in factory.created if uow.committed)


def test_batch_by_ids_validates_each_row_once_in_request_order(service, world, clock):
    first, second, third = (world.add(1) for _ in range(3))
    command = BatchCommand("EUR", tuple(world.public(third, first, third, second)), None)

    result = service.create_batch(ANN, command)

    assert result.transaction_ids == tuple(world.public(third, first, second))
    assert result.batch.validated_count == 3
    assert result.batch.created_at == clock.now()
    assert all(world.rows[i].currency == "EUR" and not world.rows[i].pending for i in (1, 2, 3))


def test_batch_by_filter_validates_every_matching_pending_row(service, world):
    italy = world.add(1, country="IT")
    world.add(1, country="FR")
    world.add(1, country="IT", pending=False, currency="USD")
    world.add(2, country="IT")

    result = service.create_batch(ANN, by_filter(PendingValidationFilter(countries=("IT",))))

    assert result.transaction_ids == tuple(world.public(italy))
    assert result.batch.validated_count == 1
    assert world.rows[4].pending and world.rows[2].pending


def test_batch_by_filter_matching_nothing_succeeds_with_zero_validated(service, world):
    world.add(1, country="FR")

    result = service.create_batch(ANN, by_filter(PendingValidationFilter(countries=("IT",))))

    assert result.batch.validated_count == 0
    assert result.transaction_ids == ()
    assert world.rows[1].pending


@pytest.mark.parametrize("both", [False, True])
def test_batch_needs_exactly_one_target(service, world, both):
    world.add(1)
    command = BatchCommand(
        "EUR",
        tuple(world.public(1)) if both else None,
        EVERY_PENDING if both else None,
    )

    with pytest.raises(ValidationFailedError) as raised:
        service.create_batch(ANN, command)

    assert {e.field for e in raised.value.errors} <= {"transactionIds", "filter"}
    assert world.rows[1].pending and not world.batches


def test_batch_rejects_a_currency_outside_the_catalogue(service, world):
    world.add(1)

    with pytest.raises(ValidationFailedError) as raised:
        service.create_batch(ANN, by_ids(world, 1, currency="ZZZ"))

    assert [e.field for e in raised.value.errors] == ["currency"]
    assert world.rows[1].pending


def test_batch_rejects_a_reversed_date_range(service, world):
    world.add(1)
    reversed_range = PendingValidationFilter(date_from=date(2026, 8, 1), date_to=date(2026, 7, 1))

    with pytest.raises(ValidationFailedError):
        service.create_batch(ANN, by_filter(reversed_range))


def test_one_unknown_or_foreign_id_fails_the_whole_batch_with_not_found(service, world):
    mine = world.add(1)
    theirs = world.add(2)

    with pytest.raises(NotFoundError):
        service.create_batch(ANN, by_ids(world, mine, theirs))
    with pytest.raises(NotFoundError):
        service.create_batch(ANN, BatchCommand("EUR", (PublicId("txn_missing"),), None))

    assert world.rows[mine].pending and world.rows[theirs].pending and not world.batches


def test_an_already_validated_row_fails_the_whole_batch_with_conflict(service, world):
    pending = world.add(1)
    done = world.add(1, pending=False, currency="USD", validated_in=7)

    with pytest.raises(ConflictError) as raised:
        service.create_batch(ANN, by_ids(world, pending, done))

    assert raised.value.code == ConflictError.ALREADY_VALIDATED
    assert world.rows[pending].pending and not world.batches


def test_a_row_without_unknown_currency_is_not_validatable(service, world):
    known = world.add(1, pending=False, currency=None)

    with pytest.raises(ValidationFailedError) as raised:
        service.create_batch(ANN, by_ids(world, known))

    assert raised.value.errors[0].code == "NOT_PENDING"


def test_a_batch_is_written_in_one_committed_unit_of_work(service, world, factory):
    world.add(1)

    service.create_batch(ANN, by_ids(world, 1))

    assert commits(factory) == 1


def test_undo_restores_every_member_to_the_queue(service, world, clock):
    first, second = world.add(1), world.add(1)
    batch = service.create_batch(ANN, by_ids(world, first, second)).batch
    clock.advance(timedelta(minutes=5))

    service.undo_batch(ANN, batch.public_id)

    assert all(world.rows[i].pending and world.rows[i].currency is None for i in (first, second))
    assert world.batches[batch.batch_id].undone_at == clock.now()


def test_undoing_an_undone_batch_is_a_quiet_success(service, world, factory, clock):
    batch = service.create_batch(ANN, by_ids(world, world.add(1))).batch
    service.undo_batch(ANN, batch.public_id)
    undone_at = world.batches[batch.batch_id].undone_at
    clock.advance(timedelta(minutes=5))
    commits_before = commits(factory)

    service.undo_batch(ANN, batch.public_id)

    assert world.batches[batch.batch_id].undone_at == undone_at
    assert commits(factory) == commits_before


def test_undo_conflicts_after_a_member_was_moved_back(service, world):
    kept, moved = world.add(1), world.add(1)
    batch = service.create_batch(ANN, by_ids(world, kept, moved)).batch
    service.move_back(ANN, world.public(moved)[0])

    with pytest.raises(ConflictError) as raised:
        service.undo_batch(ANN, batch.public_id)

    assert raised.value.code == ConflictError.BATCH_CHANGED
    assert world.rows[kept].currency == "EUR"
    assert world.batches[batch.batch_id].undone_at is None


def test_undo_conflicts_after_a_member_was_validated_again_in_another_batch(service, world):
    row = world.add(1)
    first = service.create_batch(ANN, by_ids(world, row)).batch
    service.move_back(ANN, world.public(row)[0])
    service.create_batch(ANN, by_ids(world, row, currency="USD"))

    with pytest.raises(ConflictError) as raised:
        service.undo_batch(ANN, first.public_id)

    assert raised.value.code == ConflictError.BATCH_CHANGED
    assert world.rows[row].currency == "USD"


def test_undo_of_a_batch_of_another_user_or_unknown_is_not_found(service, world):
    batch = service.create_batch(ANN, by_ids(world, world.add(1))).batch

    with pytest.raises(NotFoundError):
        service.undo_batch(BEN, batch.public_id)
    with pytest.raises(NotFoundError):
        service.undo_batch(ANN, PublicId("cvb_missing"))

    assert world.rows[1].currency == "EUR"


def test_move_back_returns_a_validated_row_to_the_queue(service, world):
    row = world.add(1)
    service.create_batch(ANN, by_ids(world, row))

    service.move_back(ANN, world.public(row)[0])

    assert world.rows[row].pending and world.rows[row].currency is None


def test_move_back_of_a_row_that_is_not_validated_changes_nothing(service, world, factory):
    row = world.add(1)

    service.move_back(ANN, world.public(row)[0])

    assert world.rows[row].pending
    assert commits(factory) == 0


def test_move_back_of_a_foreign_or_unknown_row_is_not_found(service, world):
    theirs = world.add(2, pending=False, currency="EUR", validated_in=1)

    with pytest.raises(NotFoundError):
        service.move_back(ANN, world.public(theirs)[0])
    with pytest.raises(NotFoundError):
        service.move_back(ANN, PublicId("txn_missing"))

    assert world.rows[theirs].currency == "EUR"


def test_overview_reports_progress_countries_and_currencies(service, world, factory):
    world.add(1, country="IT")
    world.add(1, country="IT")
    world.add(1, country=None)
    world.add(1, pending=False, currency="USD", validated_in=1)
    world.add(2, country="FR")

    overview = service.overview(ANN)

    assert (overview.pending, overview.validated, overview.total) == (3, 1, 4)
    assert overview.progress_percent == Decimal("25.0")
    assert overview.countries == (CountryOptionRecord("IT", 2),)
    assert [entry.code for entry in overview.currencies] == list(FakeCatalogue().codes())
    assert factory.created[-1][1] is Isolation.REPEATABLE_READ


def test_progress_of_an_empty_queue_is_zero(service):
    assert service.overview(ANN).progress_percent == Decimal("0.0")


def test_items_reject_an_unsupported_sort_and_a_reversed_range(service):
    query = ValidationItemsQuery(
        ValidationStatus.PENDING, EVERY_PENDING, SortSpec((("city", False),)), PageRequest()
    )
    with pytest.raises(InvalidQueryError) as unsupported:
        service.items(ANN, query)
    assert unsupported.value.code == InvalidQueryError.INVALID_SORT

    reversed_range = PendingValidationFilter(date_from=date(2026, 8, 1), date_to=date(2026, 7, 1))
    with pytest.raises(InvalidQueryError) as bad_range:
        service.items(
            ANN, replace(query, filter=reversed_range, sort=SortSpec((("merchant", False),)))
        )
    assert bad_range.value.code == InvalidQueryError.INVALID_FILTER
