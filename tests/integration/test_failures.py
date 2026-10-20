"""Failures against a real PostgreSQL schema: rollback of domain data, durable audit records."""

from datetime import UTC, datetime, timedelta

import pytest

from bank_transactions.domain.enums import ImportStatus
from bank_transactions.domain.errors import PersistenceError, UnknownFormatError
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import CreateAccount, CreateCard, CreateOwner
from bank_transactions.persistence.interfaces import (
    TransactionRepository,
    UnitOfWork,
    UnitOfWorkFactory,
)
from bank_transactions.services.import_service import StatementImportService

DOMAIN_TABLES = ("owners", "accounts", "cards", "installment_plans", "transactions")


class InsertFailsRepository(TransactionRepository):
    def __init__(self, inner: TransactionRepository) -> None:
        self._inner = inner

    def find_known_references(self, keys):
        return self._inner.find_known_references(keys)

    def count_matching_groups(self, keys):
        return self._inner.count_matching_groups(keys)

    def insert_many(self, aggregates):
        raise PersistenceError("the database rejected the operation")


class InsertFailsUnitOfWork(UnitOfWork):
    """Delegates to a real unit of work, but inserting transactions fails."""

    def __init__(self, inner: UnitOfWork) -> None:
        self._inner = inner

    import_runs = property(lambda self: self._inner.import_runs)
    owners = property(lambda self: self._inner.owners)
    accounts = property(lambda self: self._inner.accounts)
    cards = property(lambda self: self._inner.cards)
    installment_plans = property(lambda self: self._inner.installment_plans)
    transactions = property(lambda self: InsertFailsRepository(self._inner.transactions))

    def commit(self) -> None:
        self._inner.commit()

    def rollback(self) -> None:
        self._inner.rollback()


class InsertFailsFactory(UnitOfWorkFactory):
    def __init__(self, inner: UnitOfWorkFactory) -> None:
        self._inner = inner

    def create(self) -> UnitOfWork:
        return InsertFailsUnitOfWork(self._inner.create())


def test_a_late_database_failure_rolls_back_the_identities_and_records_a_failed_audit(
    factory, cipher, answering_resolver, examples, scalar, sql
):
    from bank_transactions.parsing.default_registry import build_default_registry
    from bank_transactions.parsing.workbook import ExcelWorkbookReader

    service = StatementImportService(
        ExcelWorkbookReader(),
        build_default_registry(),
        cipher,
        answering_resolver,
        InsertFailsFactory(factory),
    )

    with pytest.raises(PersistenceError):
        service.import_file(examples["max_monthly"])

    assert len(answering_resolver.questions) == 2
    for table in DOMAIN_TABLES:
        assert scalar(f"SELECT count(*) FROM {table}") == 0, table
    (run,) = sql("SELECT status, rows_imported, finished_at IS NOT NULL FROM import_runs")
    assert run == ("FAILED", 0, True)


def test_an_unexpected_resolver_failure_leaves_no_partial_identities(
    make_service, examples, scalar, sql
):
    class FailsOnSecondCard(IdentityResolver):
        def __init__(self):
            self.calls = 0

        def complete(self, question):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("secret detail 4396 must not be recorded")
            return CreateCard(CreateOwner("E2E Owner"), CreateAccount())

    with pytest.raises(RuntimeError):
        make_service(FailsOnSecondCard()).import_file(examples["max_monthly"])

    for table in DOMAIN_TABLES:
        assert scalar(f"SELECT count(*) FROM {table}") == 0, table
    (run,) = sql("SELECT status, error_summary FROM import_runs")
    assert run[0] == "FAILED"
    assert "secret detail" not in run[1]
    assert "4396" not in run[1]


def test_a_workbook_in_no_known_format_fails_with_a_durable_audit_record(
    service, tmp_path, scalar, sql
):
    from openpyxl import Workbook

    path = tmp_path / "private-name-1234.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "not a statement"
    workbook.save(path)

    with pytest.raises(UnknownFormatError):
        service.import_file(path)

    (run,) = sql("SELECT status, file_path, file_name, error_summary FROM import_runs")
    assert run[0] == "FAILED"
    assert "private-name" not in " ".join(run[1:3])
    assert scalar("SELECT count(*) FROM transactions") == 0


def test_a_run_left_running_for_over_an_hour_is_marked_failed_by_the_next_import(
    service, examples, scalar, sql
):
    stale = datetime.now(UTC) - timedelta(hours=3)
    sql(
        "INSERT INTO import_runs (file_path, file_name, file_sha256, started_at, status) "
        "VALUES ('sha256/x', 'x', :sha, :started, 'RUNNING')",
        sha="a" * 64,
        started=stale,
    )
    fresh = datetime.now(UTC) - timedelta(minutes=5)
    sql(
        "INSERT INTO import_runs (file_path, file_name, file_sha256, started_at, status) "
        "VALUES ('sha256/y', 'y', :sha, :started, 'RUNNING')",
        sha="b" * 64,
        started=fresh,
    )

    report = service.import_file(examples["isracard"])

    statuses = dict(
        sql("SELECT file_path, status FROM import_runs WHERE file_path LIKE 'sha256/_'")
    )
    assert statuses == {"sha256/x": "FAILED", "sha256/y": "RUNNING"}
    assert report.status is ImportStatus.SUCCESS
    assert any("interrupted" in warning for warning in report.warnings)
    assert scalar("SELECT count(*) FROM transactions") == 4
