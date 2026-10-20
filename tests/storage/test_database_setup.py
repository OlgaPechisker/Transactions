import pytest
import sqlalchemy as sa

from bank_transactions.domain.errors import PersistenceError
from bank_transactions.persistence.db import create_database_engine, upgrade_database


def test_a_missing_dsn_fails_loudly_instead_of_skipping(dsn_reader, monkeypatch):
    monkeypatch.delenv("BT_TEST_DSN", raising=False)

    with pytest.raises(pytest.fail.Exception, match="BT_TEST_DSN"):
        dsn_reader()


@pytest.mark.postgres
def test_two_schemas_are_fully_isolated(postgres_dsn, migrated_schema, engine, sql):
    other_name = migrated_schema + "_b"
    admin = sa.create_engine(postgres_dsn, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{other_name}"'))
    try:
        upgrade_database(postgres_dsn, schema=other_name)
        other = create_database_engine(postgres_dsn, schema=other_name)
        with engine.begin() as connection:
            connection.execute(
                sa.text("insert into owners (display_name, match_key) values ('A', 'a')")
            )
        with other.connect() as connection:
            visible = connection.execute(sa.text("select count(*) from owners")).scalar_one()
        other.dispose()
    finally:
        with admin.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{other_name}" CASCADE'))
        admin.dispose()

    assert visible == 0
    assert sql("select count(*) from owners")[0][0] == 1


@pytest.mark.postgres
def test_the_engine_hides_statement_parameters_in_errors(engine):
    with pytest.raises(sa.exc.DBAPIError) as raised, engine.begin() as connection:
        connection.execute(
            sa.text("insert into owners (display_name, match_key) values (:n, :k), (:n, :k)"),
            {"n": "SECRET-NAME", "k": "secret-key"},
        )

    assert "[parameters:" not in str(raised.value)
    assert "SECRET-NAME" not in str(raised.value)


def test_invalid_schema_names_are_rejected_before_any_connection():
    with pytest.raises(ValueError, match="schema"):
        create_database_engine("postgresql+psycopg://u:p@127.0.0.1:1/db", schema='x"; drop')


def test_a_failed_migration_connection_reports_neutrally_without_the_credential():
    dsn = "postgresql+psycopg://bt_user:TOP-SECRET-PW@127.0.0.1:1/nodb?connect_timeout=1"

    with pytest.raises(PersistenceError) as raised:
        upgrade_database(dsn)

    assert "TOP-SECRET-PW" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
