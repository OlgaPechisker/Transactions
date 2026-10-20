"""Alembic environment.

Programmatic callers (`bank_transactions.persistence.db.upgrade_database`) hand in a ready
connection through `config.attributes["connection"]`. The command line passes the database through
`alembic -x dsn=postgresql+psycopg://...`; the DSN is never stored in a tracked file.
"""

from alembic import context
from sqlalchemy import create_engine, pool

from bank_transactions.persistence.orm.models import Base

target_metadata = Base.metadata


def _run_with(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run_with(connection)
        return
    dsn = context.get_x_argument(as_dictionary=True).get("dsn")
    if not dsn:
        raise RuntimeError("pass the database with: alembic -x dsn=<sqlalchemy url> <command>")
    engine = create_engine(dsn, poolclass=pool.NullPool, hide_parameters=True)
    with engine.connect() as connection:
        _run_with(connection)
        connection.commit()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline (--sql) migrations are not supported")
run_migrations()
