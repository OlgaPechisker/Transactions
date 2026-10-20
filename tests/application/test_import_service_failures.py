"""Every failure after the run starts ends in a committed FAILED audit row and a re-raise, with the
domain work rolled back and no untrusted text copied into the audit summary."""

from datetime import timedelta

import pytest

from bank_transactions.domain.enums import ImportStatus
from bank_transactions.domain.errors import (
    BankTransactionsError,
    ParseError,
    PersistenceError,
    UnknownFormatError,
)
from bank_transactions.identity.models import CreateAccount, CreateCard, CreateOwner


@pytest.fixture
def new_identity(world, make_tx):
    """A statement whose card is unknown, answered by creating owner, account and card."""
    world.statement(world.context(), world.result([make_tx()]))
    world.resolver.decide = lambda question: CreateCard(CreateOwner("Dana Levi"), CreateAccount())
    return world


def only_run(world):
    (run,) = world.runs
    return run


def test_an_unrecognised_workbook_fails_the_run(world):
    world.selector.unknown = True

    with pytest.raises(UnknownFormatError):
        world.service().import_file(world.statement_file())

    run = only_run(world)
    assert run.status is ImportStatus.FAILED
    assert run.error_summary.startswith("UnknownFormatError")
    assert run.outcome.issuer_code is None


def test_an_unreadable_workbook_fails_the_run(world):
    world.reader.failure = ParseError("the workbook could not be opened")

    with pytest.raises(ParseError):
        world.service().import_file(world.statement_file())

    assert only_run(world).status is ImportStatus.FAILED


def test_a_structural_error_while_reading_the_context_fails_the_run(world):
    world.parser.context_failure = ParseError("the header row is missing")

    with pytest.raises(ParseError):
        world.service().import_file(world.statement_file())

    run = only_run(world)
    assert run.status is ImportStatus.FAILED
    assert run.error_summary == "ParseError: the header row is missing"


def test_a_parse_error_after_identity_creation_rolls_the_identities_back(new_identity):
    new_identity.parser.result_failure = ParseError("the transactions sheet is broken")

    with pytest.raises(ParseError):
        new_identity.service().import_file(new_identity.statement_file())

    assert only_run(new_identity).status is ImportStatus.FAILED
    assert (new_identity.db.domain.owners, new_identity.db.domain.cards) == ({}, {})
    assert new_identity.stored == []


def test_the_issuer_is_recorded_once_it_is_known(new_identity):
    new_identity.parser.result_failure = ParseError("broken")

    with pytest.raises(ParseError):
        new_identity.service().import_file(new_identity.statement_file())

    assert only_run(new_identity).outcome.issuer_code is new_identity.parser.issuer_code


def test_an_unexpected_error_is_summarised_by_type_only_and_reraised(world):
    secret = "C:\\Users\\dana\\statements\\4396.xlsx postgresql://user:pw@host/db"
    world.parser.context_failure = RuntimeError(secret)

    with pytest.raises(RuntimeError):
        world.service().import_file(world.statement_file())

    run = only_run(world)
    assert run.error_summary == "unexpected error (RuntimeError)"
    assert "pw@host" not in repr(world.runs) and "4396" not in repr(world.runs)


def test_a_domain_error_message_naming_an_identifier_is_reduced_to_its_type(world):
    world.seed_card()
    world.parser.result_failure = ParseError("card 4396 could not be read")

    with pytest.raises(ParseError):
        world.service().import_file(world.statement_file())

    assert only_run(world).error_summary == "ParseError"


def test_a_failing_commit_leaves_no_domain_data_and_a_failed_audit_row(new_identity):
    new_identity.db.fail_domain_commit = PersistenceError("the database rejected the change")

    with pytest.raises(PersistenceError):
        new_identity.service().import_file(new_identity.statement_file())

    run = only_run(new_identity)
    assert run.status is ImportStatus.FAILED
    assert (new_identity.db.domain.owners, new_identity.stored) == ({}, [])


def test_a_failing_insert_fails_the_run_and_discards_identity_and_plans(new_identity):
    new_identity.db.fail_insert = PersistenceError("the database rejected the change")

    with pytest.raises(PersistenceError):
        new_identity.service().import_file(new_identity.statement_file())

    assert only_run(new_identity).status is ImportStatus.FAILED
    assert new_identity.db.domain.cards == {}


def test_a_database_that_cannot_start_the_run_stops_before_the_workbook_is_opened(world):
    world.db.fail_start_run = PersistenceError("the database is unavailable")

    with pytest.raises(PersistenceError):
        world.service().import_file(world.statement_file())

    assert world.reader.opened == []
    assert world.runs == []


def sweep_everything(world):
    with world.factory.create() as uow:
        now = world.clock.now
        uow.import_runs.sweep_stuck_runs(now + timedelta(days=1), now, "swept by another process")
        uow.commit()


def test_a_run_swept_during_the_import_keeps_its_terminal_audit_record(world, make_tx):
    world.seed_card()
    world.statement(world.context(), world.result([make_tx()]))
    world.parser.on_read_transactions = lambda: sweep_everything(world)

    with pytest.raises(PersistenceError):
        world.service().import_file(world.statement_file())

    run = only_run(world)
    assert (run.status, run.error_summary) == (ImportStatus.FAILED, "swept by another process")
    assert world.db.finish_calls == 1


def test_a_failure_after_the_run_was_swept_is_not_retried_or_overwritten(world):
    world.parser.on_read_transactions = lambda: sweep_everything(world)
    world.parser.context = world.context()
    world.reader.on_open = lambda: sweep_everything(world)
    world.parser.context_failure = ParseError("the header row is missing")

    with pytest.raises(ParseError) as raised:
        world.service().import_file(world.statement_file())

    assert "the import audit record could not be finalized" in raised.value.__notes__
    assert world.db.finish_calls == 1
    assert only_run(world).error_summary == "swept by another process"


def test_audit_finalization_failure_never_masks_the_original_error_type(world):
    world.reader.failure = RuntimeError("boom")
    world.reader.on_open = lambda: sweep_everything(world)

    with pytest.raises(RuntimeError) as raised:
        world.service().import_file(world.statement_file())

    assert isinstance(raised.value, RuntimeError)
    assert not isinstance(raised.value, BankTransactionsError)
