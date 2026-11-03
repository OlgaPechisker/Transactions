"""Currency validation on PostgreSQL: the queue, batches, undo and move-back, scoped to the claimed
cards of one user, and concurrent batches over the same rows."""

import threading
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from tests.fakes.auth import make_auth_context
from tests.fakes.clock import FakeClock
from tests.fakes.identity import FakeCatalogue

from bank_transactions.domain.enums import ReviewReason, ValidationStatus
from bank_transactions.domain.errors import ConflictError
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.money import USD
from bank_transactions.domain.queries import PageRequest, PendingValidationFilter, SortSpec
from bank_transactions.persistence.commands import NewForeignDetail, NewValidationBatch
from bank_transactions.services.currency_validation_service import (
    BatchCommand,
    CurrencyValidationService,
)

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
EVERYTHING = PageRequest(1, 200)
EVERY_PENDING = PendingValidationFilter()
NEWEST_FIRST = SortSpec((("transactionDate", True),))


def pending(**overrides: Any) -> dict[str, Any]:
    return {
        "original_amount": Decimal("10.00"),
        "original_currency": None,
        "aggregate_review_reasons": {ReviewReason.UNKNOWN_CURRENCY},
        "aggregate_foreign_detail": NewForeignDetail(Decimal("0.50"), None, Decimal("3.65")),
        **overrides,
    }


class Queue:
    """Ann's pending rows, one validated-looking foreign row, one domestic row and Ben's row."""

    def __init__(self, seed_user, seed_claimed_card, seed_transaction, sql) -> None:
        self.sql = sql
        self.ann = seed_user("ann@example.com", "Ann")
        self.ben = seed_user("ben@example.com", "Ben")
        card = seed_claimed_card(user=self.ann, last4="1111")
        self.rome = seed_transaction(
            card,
            **pending(
                merchant_name_raw="Rome Cafe",
                merchant_country="IT",
                transaction_date=date(2026, 7, 5),
                original_amount=Decimal("40.00"),
                billing_amount=Decimal("140.00"),
            ),
        )
        self.paris = seed_transaction(
            card,
            **pending(
                merchant_name_raw="Paris Bakery",
                merchant_country="FR",
                transaction_date=date(2026, 7, 9),
                original_amount=Decimal("10.00"),
                billing_amount=Decimal("37.00"),
            ),
        )
        self.milan = seed_transaction(
            card,
            **pending(
                merchant_name_raw="Milan Cafe",
                merchant_country="IT",
                transaction_date=date(2026, 7, 20),
                original_amount=Decimal("25.00"),
                billing_amount=Decimal("90.00"),
            ),
        )
        self.nowhere = seed_transaction(
            card,
            **pending(merchant_name_raw="Web Shop", merchant_country=None),
        )
        self.known = seed_transaction(
            card,
            original_amount=Decimal("5.00"),
            original_currency=USD,
            aggregate_foreign_detail=NewForeignDetail(Decimal("0"), Decimal("3.5"), Decimal("3.5")),
        )
        self.domestic = seed_transaction(
            card, aggregate_review_reasons={ReviewReason.UNKNOWN_CURRENCY}
        )
        ben_card = seed_claimed_card(user=self.ben, last4="2222")
        self.stranger = seed_transaction(ben_card, **pending(merchant_name_raw="Ben Only"))

    def public(self, transaction_id: int) -> PublicId:
        (row,) = self.sql("SELECT public_id FROM transactions WHERE id = :i", i=transaction_id)
        return PublicId(row[0])

    def state(self, transaction_id: int) -> tuple[Any, ...]:
        (row,) = self.sql(
            "SELECT t.original_currency, t.source_original_currency,"
            " (SELECT count(*) FROM transaction_review_reasons r"
            "   WHERE r.transaction_id = t.id AND r.reason = 'UNKNOWN_CURRENCY'),"
            " (SELECT currency FROM currency_validations v WHERE v.transaction_id = t.id)"
            " FROM transactions t WHERE t.id = :i",
            i=transaction_id,
        )
        return tuple(row)


@pytest.fixture
def queue(seed_user, seed_claimed_card, seed_transaction, sql) -> Queue:
    return Queue(seed_user, seed_claimed_card, seed_transaction, sql)


@pytest.fixture
def service(factory) -> CurrencyValidationService:
    return CurrencyValidationService(factory, FakeCatalogue(), FakeClock(NOW))


def merchants(page) -> list[str]:
    return [item.transaction.merchant for item in page.items]


def test_queue_counts_only_the_visible_pending_rows_and_their_countries(factory, queue):
    with factory.create() as uow:
        repository = uow.currency_validation
        assert repository.count_pending(queue.ann.user_id) == 4
        assert repository.count_validated(queue.ann.user_id) == 0
        assert [(c.code, c.count) for c in repository.pending_countries(queue.ann.user_id)] == [
            ("FR", 1),
            ("IT", 2),
        ]
        assert repository.count_pending(queue.ben.user_id) == 1


def test_items_narrow_by_country_date_and_merchant_and_sort_by_any_field(factory, queue):
    def items(selection: PendingValidationFilter = EVERY_PENDING, sort=NEWEST_FIRST):
        with factory.create() as uow:
            return uow.currency_validation.page_items(
                queue.ann.user_id, ValidationStatus.PENDING, selection, sort, EVERYTHING
            )

    assert merchants(items()) == ["Milan Cafe", "Paris Bakery", "Rome Cafe", "Web Shop"]
    assert merchants(items(PendingValidationFilter(countries=("IT",)))) == [
        "Milan Cafe",
        "Rome Cafe",
    ]
    assert merchants(
        items(PendingValidationFilter(date_from=date(2026, 7, 6), date_to=date(2026, 7, 10)))
    ) == ["Paris Bakery"]
    assert sorted(merchants(items(PendingValidationFilter(merchants=("cafe",))))) == [
        "Milan Cafe",
        "Rome Cafe",
    ]
    by_original = items(sort=SortSpec((("originalAmount", False),)))
    assert [item.transaction.original_amount for item in by_original.items][:3] == [
        Decimal("10.00"),
        Decimal("10.00"),
        Decimal("25.00"),
    ]
    assert items().total == 4


def test_items_page_reports_the_total_and_the_requested_slice(factory, queue):
    with factory.create() as uow:
        page = uow.currency_validation.page_items(
            queue.ann.user_id,
            ValidationStatus.PENDING,
            PendingValidationFilter(),
            SortSpec((("merchant", False),)),
            PageRequest(2, 3),
        )

    assert (page.total, page.total_pages, merchants(page)) == (4, 2, ["Web Shop"])


def apply_batch(factory, queue, transaction_ids, currency="EUR"):
    with factory.create() as uow:
        repository = uow.currency_validation
        batch = repository.create_batch(NewValidationBatch(queue.ann.user_id, currency, NOW))
        repository.add_members(batch.batch_id, transaction_ids)
        repository.apply(queue.ann.user_id, batch.batch_id, transaction_ids, currency)
        uow.commit()
    return batch


def test_applying_a_batch_sets_the_currency_and_keeps_the_source_value(factory, queue, sql):
    sql(
        "UPDATE transactions SET source_original_currency = 'GBP' WHERE id = :i",
        i=queue.rome,
    )

    batch = apply_batch(factory, queue, [queue.rome, queue.paris])

    assert queue.state(queue.rome) == ("EUR", "GBP", 0, "EUR")
    assert queue.state(queue.paris) == ("EUR", None, 0, "EUR")
    assert queue.state(queue.milan)[0] is None
    with factory.create() as uow:
        repository = uow.currency_validation
        assert repository.count_pending(queue.ann.user_id) == 2
        assert repository.count_validated(queue.ann.user_id) == 2
        stored = repository.lock_batch(queue.ann.user_id, batch.public_id)
        assert stored is not None
        assert (stored.validated_count, stored.currency, stored.undone_at) == (2, "EUR", None)


def test_validated_items_carry_their_validation_and_leave_the_pending_queue(factory, queue):
    batch = apply_batch(factory, queue, [queue.rome])

    with factory.create() as uow:
        repository = uow.currency_validation
        done = repository.page_items(
            queue.ann.user_id,
            ValidationStatus.VALIDATED,
            PendingValidationFilter(),
            NEWEST_FIRST,
            EVERYTHING,
        )
        left = repository.page_items(
            queue.ann.user_id,
            ValidationStatus.PENDING,
            PendingValidationFilter(),
            NEWEST_FIRST,
            EVERYTHING,
        )
        narrowed = repository.page_items(
            queue.ann.user_id,
            ValidationStatus.VALIDATED,
            PendingValidationFilter(countries=("FR",)),
            NEWEST_FIRST,
            EVERYTHING,
        )

    (item,) = done.items
    assert item.transaction.merchant == "Rome Cafe"
    assert item.transaction.original_currency == "EUR"
    assert (item.validated_currency, item.validated_at, item.batch_public_id) == (
        "EUR",
        NOW,
        batch.public_id,
    )
    assert "Rome Cafe" not in merchants(left)
    assert narrowed.items == ()


def test_among_helpers_split_pending_and_validated_rows_and_hide_other_users(factory, queue):
    apply_batch(factory, queue, [queue.rome])
    ids = [queue.rome, queue.paris, queue.known, queue.stranger]

    with factory.create() as uow:
        repository = uow.currency_validation
        assert repository.validated_among(queue.ann.user_id, ids) == {queue.rome}
        assert repository.pending_among(queue.ann.user_id, ids) == {queue.paris}


def test_undo_restores_currency_review_reason_and_queue_membership(factory, queue):
    batch = apply_batch(factory, queue, [queue.rome, queue.paris])

    with factory.create() as uow:
        repository = uow.currency_validation
        members = repository.members_state(batch.batch_id)
        assert {
            m.transaction_id: (m.validation_batch_id, m.original_currency) for m in members
        } == {
            queue.rome: (batch.batch_id, "EUR"),
            queue.paris: (batch.batch_id, "EUR"),
        }
        repository.undo(batch.batch_id, NOW + timedelta(minutes=1))
        uow.commit()

    assert queue.state(queue.rome) == (None, None, 1, None)
    assert queue.state(queue.paris) == (None, None, 1, None)
    with factory.create() as uow:
        repository = uow.currency_validation
        assert repository.count_pending(queue.ann.user_id) == 4
        stored = repository.lock_batch(queue.ann.user_id, batch.public_id)
        assert stored is not None and stored.undone_at == NOW + timedelta(minutes=1)


def test_move_back_returns_one_row_and_the_batch_keeps_its_member(factory, queue):
    batch = apply_batch(factory, queue, [queue.rome, queue.paris])

    with factory.create() as uow:
        repository = uow.currency_validation
        assert repository.lock_validation(queue.milan) is False
        assert repository.lock_validation(queue.rome) is True
        repository.reset(queue.rome)
        uow.commit()
        states = {m.transaction_id: m for m in repository.members_state(batch.batch_id)}

    assert queue.state(queue.rome) == (None, None, 1, None)
    assert queue.state(queue.paris) == ("EUR", None, 0, "EUR")
    assert states[queue.rome].validation_batch_id is None
    assert states[queue.paris].validation_batch_id == batch.batch_id


def test_a_row_validated_in_a_newer_batch_shows_the_newer_batch_in_the_old_members(factory, queue):
    first = apply_batch(factory, queue, [queue.rome])
    with factory.create() as uow:
        uow.currency_validation.reset(queue.rome)
        uow.commit()
    second = apply_batch(factory, queue, [queue.rome], currency="USD")

    with factory.create() as uow:
        (member,) = uow.currency_validation.members_state(first.batch_id)

    assert (member.validation_batch_id, member.original_currency) == (second.batch_id, "USD")


def test_no_operation_reaches_the_rows_of_another_user(factory, queue):
    with factory.create() as uow:
        repository = uow.currency_validation
        assert repository.pending_among(queue.ann.user_id, [queue.stranger]) == frozenset()
        assert repository.lock_batch(queue.ben.user_id, PublicId("cvb_unknown")) is None
    batch = apply_batch(factory, queue, [queue.rome])
    with factory.create() as uow:
        assert uow.currency_validation.lock_batch(queue.ben.user_id, batch.public_id) is None


def wait_until_blocked(sql) -> None:
    for _ in range(100):
        if sql("SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock' LIMIT 1"):
            return
        time.sleep(0.05)
    raise AssertionError("the concurrent batch never waited for the row locks")


def race(service, factory, queue, sql, command: BatchCommand):
    """Ann's first batch holds the row locks while a second batch starts; the first then commits."""
    outcome: dict[str, Any] = {}
    context = make_auth_context(user_id=int(queue.ann.user_id))

    def second() -> None:
        try:
            outcome["result"] = service.create_batch(context, command)
        except Exception as error:
            outcome["error"] = error

    with factory.create() as uow:
        locked = uow.transaction_query.lock_visible(
            queue.ann.user_id, [queue.public(queue.rome), queue.public(queue.paris)]
        )
        thread = threading.Thread(target=second)
        thread.start()
        wait_until_blocked(sql)
        ids = [ref.transaction_id for ref in locked]
        repository = uow.currency_validation
        batch = repository.create_batch(NewValidationBatch(queue.ann.user_id, "EUR", NOW))
        repository.add_members(batch.batch_id, ids)
        repository.apply(queue.ann.user_id, batch.batch_id, ids, "EUR")
        uow.commit()
    thread.join(timeout=30)
    assert not thread.is_alive()
    return outcome


def test_a_second_batch_on_the_same_ids_conflicts_and_changes_nothing(service, factory, queue, sql):
    command = BatchCommand(
        "USD",
        (queue.public(queue.rome), queue.public(queue.paris), queue.public(queue.milan)),
        None,
    )

    outcome = race(service, factory, queue, sql, command)

    assert isinstance(outcome.get("error"), ConflictError)
    assert outcome["error"].code == ConflictError.ALREADY_VALIDATED
    assert queue.state(queue.rome)[3] == "EUR" and queue.state(queue.paris)[3] == "EUR"
    assert queue.state(queue.milan) == (None, None, 1, None)
    assert sql("SELECT count(*) FROM currency_validation_batches")[0][0] == 1


def test_a_second_batch_by_filter_validates_only_the_rows_still_pending(
    service, factory, queue, sql
):
    outcome = race(
        service, factory, queue, sql, BatchCommand("USD", None, PendingValidationFilter())
    )

    result = outcome["result"]
    assert set(result.transaction_ids) == {queue.public(queue.milan), queue.public(queue.nowhere)}
    assert result.batch.validated_count == 2
    assert queue.state(queue.rome)[3] == "EUR"
    assert queue.state(queue.milan)[3] == "USD" and queue.state(queue.nowhere)[3] == "USD"
    assert sql("SELECT count(*) FROM currency_validations")[0][0] == 4


def test_concurrent_batches_over_every_row_leave_each_row_validated_exactly_once(
    service, queue, sql
):
    context = make_auth_context(user_id=int(queue.ann.user_id))
    outcomes: list[Any] = []
    barrier = threading.Barrier(2)

    def run(currency: str) -> None:
        barrier.wait()
        try:
            outcomes.append(
                service.create_batch(
                    context, BatchCommand(currency, None, PendingValidationFilter())
                )
            )
        except Exception as error:
            outcomes.append(error)

    threads = [threading.Thread(target=run, args=(code,)) for code in ("EUR", "USD")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not isinstance(outcome, Exception) for outcome in outcomes)
    assert sorted(o.batch.validated_count for o in outcomes) == [0, 4]
    assert sql("SELECT count(*) FROM currency_validations")[0][0] == 4
    assert sql("SELECT sum(applied_count) FROM currency_validation_batches")[0][0] == 4
    assert sql("SELECT count(*) FROM currency_validation_batch_members")[0][0] == 4


def test_the_undo_of_a_batch_waits_for_a_concurrent_move_back_and_then_conflicts(
    service, factory, queue, sql
):
    batch = apply_batch(factory, queue, [queue.rome, queue.paris])
    context = make_auth_context(user_id=int(queue.ann.user_id))
    outcome: dict[str, Any] = {}

    def undo() -> None:
        try:
            service.undo_batch(context, batch.public_id)
        except Exception as error:
            outcome["error"] = error

    with factory.create() as uow:
        assert uow.currency_validation.lock_validation(queue.rome)
        thread = threading.Thread(target=undo)
        thread.start()
        wait_until_blocked(sql)
        uow.currency_validation.reset(queue.rome)
        uow.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), ConflictError)
    assert outcome["error"].code == ConflictError.BATCH_CHANGED
    assert queue.state(queue.paris) == ("EUR", None, 0, "EUR")
    assert queue.state(queue.rome) == (None, None, 1, None)


def test_a_large_batch_is_applied_in_chunks(factory, queue, sql):
    sql(
        "INSERT INTO transactions (public_id, card_id, import_run_id, transaction_date,"
        " charge_date, billing_amount, billing_currency, original_amount, merchant_name_raw,"
        " merchant_name_norm,"
        " transaction_type)"
        " SELECT 'txn_bulk' || g, t.card_id, t.import_run_id, t.transaction_date, t.charge_date,"
        " 1, 'ILS', 2, 'Bulk', 'bulk', t.transaction_type FROM transactions t,"
        " generate_series(1, 10500) g WHERE t.id = :base",
        base=queue.rome,
    )
    sql(
        "INSERT INTO foreign_transaction_details (transaction_id, commission_amount,"
        " stated_fx_rate, effective_fx_rate) SELECT id, 0, 1, 1 FROM transactions"
        " WHERE public_id LIKE 'txn_bulk%'"
    )
    sql(
        "INSERT INTO transaction_review_reasons (transaction_id, reason) SELECT id,"
        " 'UNKNOWN_CURRENCY' FROM transactions WHERE public_id LIKE 'txn_bulk%'"
    )
    ids = [row[0] for row in sql("SELECT id FROM transactions WHERE public_id LIKE 'txn_bulk%'")]

    apply_batch(factory, queue, ids)

    assert sql("SELECT count(*) FROM currency_validations")[0][0] == 10500
    assert sql("SELECT applied_count FROM currency_validation_batches")[0][0] == 10500
    assert not sql(
        "SELECT 1 FROM transactions WHERE public_id LIKE 'txn_bulk%' AND original_currency IS NULL"
    )
