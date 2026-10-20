from datetime import UTC, datetime, timedelta, timezone

import pytest

from bank_transactions.domain.enums import ImportStatus, IssuerCode
from bank_transactions.domain.errors import PersistenceError
from bank_transactions.domain.ids import ImportRunId
from bank_transactions.persistence.commands import ImportRunOutcome, NewImportRun

pytestmark = pytest.mark.postgres

LABEL = "run-" + "ab" * 6
SHA = "f" * 64


def run_row(sql, run_id):
    return sql(
        "select file_path, file_name, file_sha256, issuer_code, started_at, finished_at, status,"
        " rows_total, rows_imported, rows_duplicate, rows_skipped, error_summary"
        " from import_runs where id = :id",
        id=run_id,
    )[0]


def outcome(status=ImportStatus.SUCCESS, finished_at=None, issuer=IssuerCode.MAX, **counts):
    return ImportRunOutcome(
        status,
        finished_at or datetime.now(UTC),
        issuer,
        counts.get("total", 10),
        counts.get("imported", 7),
        counts.get("duplicate", 2),
        counts.get("skipped", 1),
        counts.get("summary"),
    )


def test_a_started_run_is_running_with_unknown_issuer_and_zero_counts(factory, sql):
    started = datetime(2026, 7, 1, 8, 30, tzinfo=UTC)

    with factory.create() as uow:
        run_id = uow.import_runs.start_run(NewImportRun(LABEL, LABEL, SHA, started))
        uow.commit()

    assert run_id > 0
    assert tuple(run_row(sql, run_id)) == (
        LABEL, LABEL, SHA, None, started, None, "RUNNING", 0, 0, 0, 0, None,
    )  # fmt: skip


def test_finishing_a_run_records_status_issuer_counts_and_summary(factory, sql, start_run):
    run_id = start_run()
    finished = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)

    with factory.create() as uow:
        uow.import_runs.finish_run(
            run_id, outcome(ImportStatus.PARTIAL, finished, summary="2 rows need review")
        )
        uow.commit()

    row = run_row(sql, run_id)
    assert row.status == "PARTIAL"
    assert row.issuer_code == "MAX"
    assert row.finished_at == finished
    assert (row.rows_total, row.rows_imported, row.rows_duplicate, row.rows_skipped) == (
        10,
        7,
        2,
        1,
    )
    assert row.error_summary == "2 rows need review"


def test_a_run_may_finish_without_a_known_issuer(factory, sql, start_run):
    run_id = start_run()

    with factory.create() as uow:
        uow.import_runs.finish_run(run_id, outcome(ImportStatus.FAILED, issuer=None, summary="x"))
        uow.commit()

    assert run_row(sql, run_id).issuer_code is None


def test_a_run_cannot_be_finished_twice(factory, sql, start_run):
    run_id = start_run()
    with factory.create() as uow:
        uow.import_runs.finish_run(run_id, outcome(ImportStatus.SUCCESS))
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.import_runs.finish_run(run_id, outcome(ImportStatus.FAILED))

    assert run_row(sql, run_id).status == "SUCCESS"


def test_finishing_an_unknown_run_is_a_persistence_error(factory):
    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.import_runs.finish_run(ImportRunId(987654), outcome())


def test_a_committed_audit_run_survives_a_rolled_back_domain_unit_of_work(
    factory, seed_card, new_aggregate, sql, start_run
):
    card = seed_card()
    run_id = start_run()

    with factory.create() as domain:
        domain.transactions.insert_many([new_aggregate(card.card_id, run_id)])
        domain.rollback()

    with factory.create() as audit:
        audit.import_runs.finish_run(run_id, outcome(ImportStatus.FAILED, summary="parse failed"))
        audit.commit()

    assert sql("select count(*) from transactions")[0][0] == 0
    assert run_row(sql, run_id).status == "FAILED"


def test_the_audit_start_row_is_durable_before_domain_work_begins(factory, sql, start_run):
    run_id = start_run()

    assert run_row(sql, run_id).status == "RUNNING"


def test_sweep_fails_only_running_runs_started_before_the_utc_cutoff(factory, sql):
    cutoff = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    finished_at = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)
    with factory.create() as uow:
        stale = uow.import_runs.start_run(
            NewImportRun(LABEL, LABEL, SHA, cutoff - timedelta(hours=2))
        )
        boundary = uow.import_runs.start_run(NewImportRun(LABEL, LABEL, SHA, cutoff))
        fresh = uow.import_runs.start_run(
            NewImportRun(LABEL, LABEL, SHA, cutoff + timedelta(hours=1))
        )
        done = uow.import_runs.start_run(
            NewImportRun(LABEL, LABEL, SHA, cutoff - timedelta(hours=3))
        )
        uow.import_runs.finish_run(done, outcome(ImportStatus.SUCCESS))
        uow.commit()

    with factory.create() as uow:
        swept = uow.import_runs.sweep_stuck_runs(cutoff, finished_at, "interrupted run")
        uow.commit()

    assert swept == 1
    stale_row = run_row(sql, stale)
    assert (stale_row.status, stale_row.finished_at, stale_row.error_summary) == (
        "FAILED",
        finished_at,
        "interrupted run",
    )
    assert run_row(sql, boundary).status == "RUNNING"
    assert run_row(sql, fresh).status == "RUNNING"
    assert run_row(sql, done).status == "SUCCESS"


def test_sweep_compares_instants_not_wall_clock_offsets(factory, sql):
    plus_three = timezone(timedelta(hours=3))
    started = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
    cutoff_local = datetime(2026, 7, 1, 16, 0, tzinfo=plus_three)
    with factory.create() as uow:
        run_id = uow.import_runs.start_run(NewImportRun(LABEL, LABEL, SHA, started))
        uow.commit()

    with factory.create() as uow:
        assert uow.import_runs.sweep_stuck_runs(cutoff_local, datetime.now(UTC), "x") == 0
        later_local = cutoff_local + timedelta(hours=2)
        assert uow.import_runs.sweep_stuck_runs(later_local, datetime.now(UTC), "x") == 1
        uow.commit()

    assert run_row(sql, run_id).status == "FAILED"


def test_a_sweep_with_nothing_stuck_returns_zero(factory):
    with factory.create() as uow:
        assert uow.import_runs.sweep_stuck_runs(datetime.now(UTC), datetime.now(UTC), "x") == 0


def test_naive_timestamps_are_rejected_instead_of_guessing_a_zone(factory):
    naive = datetime(2026, 7, 1, 8, 30)

    with factory.create() as uow, pytest.raises(PersistenceError, match="timezone"):
        uow.import_runs.start_run(NewImportRun(LABEL, LABEL, SHA, naive))


def test_negative_counts_are_blocked_by_a_check_constraint(sql, start_run):
    run_id = start_run()

    with pytest.raises(Exception, match="ck_import_runs"):
        sql("update import_runs set rows_total = -1 where id = :id", id=run_id)
