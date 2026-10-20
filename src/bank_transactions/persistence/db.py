"""Engine construction, migrations and sanitised translation of database errors."""

import functools
import re
from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from bank_transactions.domain.errors import PersistenceError

_SIMPLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")

_SQLSTATE_MESSAGES = {
    "23505": "a stored record already has the same unique values",
    "23503": "a referenced record is missing or belongs to a different owner",
    "23502": "a required value is missing",
    "23514": "a value violates a database check constraint",
    "22003": "a numeric value is out of range for its database column",
    "22001": "a text value is too long for its database column",
    "40001": "the database could not serialize the transaction",
    "40P01": "the database detected a deadlock",
}


def create_database_engine(dsn: str, schema: str | None = None) -> Engine:
    """A PostgreSQL engine that never echoes statement parameters. With `schema`, every
    connection resolves unqualified names there only (used to isolate tests)."""
    return _build_engine(dsn, schema)


def upgrade_database(dsn: str, schema: str | None = None) -> None:
    """Bring the database to the latest migration; a current database is left unchanged."""
    _run_migration(dsn, schema, lambda config: command.upgrade(config, "head"))


def downgrade_database(dsn: str, schema: str | None = None, revision: str = "base") -> None:
    _run_migration(dsn, schema, lambda config: command.downgrade(config, revision))


def describe_database_error(error: SQLAlchemyError) -> str:
    """A message built from the SQLSTATE and constraint name only; the driver's text can echo
    row values, so it is never copied."""
    original = error.orig if isinstance(error, DBAPIError) else None
    sqlstate = getattr(original, "sqlstate", None)
    message = _SQLSTATE_MESSAGES.get(sqlstate or "", "the database rejected the operation")
    if sqlstate is not None and sqlstate.startswith("08"):
        message = "the database connection failed"
    constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
    if constraint and _SIMPLE_NAME.fullmatch(constraint):
        return f"{message} (constraint {constraint})"
    return message


def translates_database_errors[**P, T](operation: Callable[P, T]) -> Callable[P, T]:
    """Turn database failures into a neutral PersistenceError. It is raised outside the except
    block so neither __cause__ nor __context__ keeps the driver exception (and its row values)."""

    @functools.wraps(operation)
    def translated(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return operation(*args, **kwargs)
        except SQLAlchemyError as error:
            message = describe_database_error(error)
        raise PersistenceError(message) from None

    return translated


def _build_engine(dsn: str, schema: str | None, **options: object) -> Engine:
    connect_args: dict[str, str] = {}
    if schema is not None:
        connect_args["options"] = f"-csearch_path={_validated_schema(schema)}"
    return sa.create_engine(
        dsn, hide_parameters=True, pool_pre_ping=True, connect_args=connect_args, **options
    )


def _validated_schema(schema: str) -> str:
    if _SIMPLE_NAME.fullmatch(schema) is None:
        raise ValueError("schema must be a simple identifier")
    return schema


@translates_database_errors
def _run_migration(dsn: str, schema: str | None, action: Callable[[Config], None]) -> None:
    engine = _build_engine(dsn, schema, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            action(_alembic_config(connection))
    finally:
        engine.dispose()


def _alembic_config(connection: Connection) -> Config:
    script_directory = Path(__file__).resolve().parent / "migrations"
    if not (script_directory / "env.py").is_file():
        raise PersistenceError("the database migrations are not available in this installation")
    config = Config()
    config.set_main_option("script_location", str(script_directory).replace("%", "%%"))
    config.attributes["connection"] = connection
    return config
