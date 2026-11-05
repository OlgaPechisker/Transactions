"""The import history under real PostgreSQL: runs are visible through the caller's own
transactions only, and only committed SUCCESS/PARTIAL runs appear."""

from datetime import UTC, datetime, timedelta

import pytest

from bank_transactions.domain.enums import ImportStatus
from bank_transactions.domain.queries import PageRequest
from bank_transactions.persistence.commands import ImportRunOutcome, NewImportRun

pytestmark = pytest.mark.postgres

BASE = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)


@pytest.fixture
def finished_run(factory):
    """A finished run started `hours` after BASE; its public id is returned."""

    def finish(number, status=ImportStatus.SUCCESS, hours=0):
        started = BASE + timedelta(hours=hours)
        with factory.create() as uow:
            run_id = uow.import_runs.start_run(
                NewImportRun(f"run-{number}", f"run-{number}", f"{number:064x}", started)
            )
            uow.import_runs.finish_run(
                run_id, ImportRunOutcome(status, started, None, 3, 3, 0, 0, None)
            )
            public_id = uow.import_runs.public_id(run_id)
            uow.commit()
        return run_id, public_id

    return finish


def history(factory, user, descending=True, page=1, size=50):
    with factory.create() as uow:
        return uow.import_history.page(user.user_id, PageRequest(page, size), descending)


def test_a_run_appears_with_the_number_of_transactions_the_caller_can_see(
    factory, seed_card, seed_transaction, finished_run, seed_user
):
    card = seed_card()
    run_id, public_id = finished_run(1)
    seed_transaction(card, run_id)
    seed_transaction(card, run_id, merchant_name_norm="other")
    owner = seed_user()

    result = history(factory, owner)

    (item,) = result.items
    assert (item.public_id, item.status, item.rows_imported) == (
        public_id,
        ImportStatus.SUCCESS,
        2,
    )
    assert item.file_sha256 == f"{1:064x}"
    assert item.started_at == BASE
    assert (result.page, result.size, result.total) == (1, 50, 1)


def test_another_users_runs_are_invisible_and_a_shared_run_counts_only_own_rows(
    factory, seed_claimed_card, seed_user, seed_transaction, finished_run
):
    dana = seed_claimed_card(last4="1111")
    omer_user = seed_user("omer@example.com", "Omer")
    omer = seed_claimed_card(user=omer_user, last4="2222", owner_name="Omer Cohen")
    shared, _ = finished_run(1)
    only_dana, _ = finished_run(2, hours=1)
    seed_transaction(dana, shared)
    seed_transaction(omer, shared)
    seed_transaction(omer, shared, merchant_name_norm="other")
    seed_transaction(dana, only_dana)

    dana_runs = history(factory, seed_user())
    omer_runs = history(factory, omer_user)

    assert [i.rows_imported for i in dana_runs.items] == [1, 1]
    assert [i.rows_imported for i in omer_runs.items] == [2]
    assert omer_runs.total == 1


def test_runs_without_visible_transactions_or_a_successful_status_never_appear(
    factory, seed_card, seed_transaction, finished_run, seed_user
):
    card = seed_card()
    visible, _ = finished_run(1)
    duplicates_only, _ = finished_run(2, hours=1)
    failed, _ = finished_run(3, ImportStatus.FAILED, hours=2)
    needs_input, _ = finished_run(4, ImportStatus.NEEDS_INPUT, hours=3)
    seed_transaction(card, visible)
    seed_transaction(card, failed)
    seed_transaction(card, needs_input)

    result = history(factory, seed_user())

    assert [i.file_sha256 for i in result.items] == [f"{1:064x}"]
    assert duplicates_only is not None


def test_partial_runs_are_listed_too(factory, seed_card, seed_transaction, finished_run, seed_user):
    card = seed_card()
    run_id, _ = finished_run(1, ImportStatus.PARTIAL)
    seed_transaction(card, run_id)

    (item,) = history(factory, seed_user()).items

    assert item.status is ImportStatus.PARTIAL


def test_runs_are_sorted_by_start_time_in_either_direction_and_paged(
    factory, seed_card, seed_transaction, finished_run, seed_user
):
    card = seed_card()
    for number in range(1, 6):
        run_id, _ = finished_run(number, hours=number)
        seed_transaction(card, run_id)
    user = seed_user()

    newest_first = history(factory, user, descending=True, page=1, size=2)
    oldest_first = history(factory, user, descending=False, page=3, size=2)

    assert [i.file_sha256 for i in newest_first.items] == [f"{5:064x}", f"{4:064x}"]
    assert newest_first.total == 5
    assert [i.file_sha256 for i in oldest_first.items] == [f"{5:064x}"]
    assert (oldest_first.page, oldest_first.size, oldest_first.total_pages) == (3, 2, 3)


def test_a_user_without_imports_has_an_empty_history(factory, seed_user):
    result = history(factory, seed_user())

    assert (result.items, result.total) == ((), 0)
