"""Shared fixtures for the PostgreSQL-backed storage tests.

Every DB test gets its own freshly created schema (with its own enum types) on the isolated test
database named by BT_TEST_DSN, and only that schema is dropped afterwards.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from tests.storage.seeds import (
    DEFAULT_EMAIL,
    SeededCard,
    create_claimed_card,
    create_transaction,
    create_user,
    make_aggregate,
    start_import_run,
    utc_now,
)

from bank_transactions.domain.ids import CardId, ImportRunId, TransactionId
from bank_transactions.identity.crypto import IdentifierCipher
from bank_transactions.persistence.commands import NewTransactionAggregate
from bank_transactions.persistence.db import create_database_engine, upgrade_database
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import UserRecord
from bank_transactions.persistence.uow import SqlAlchemyUnitOfWorkFactory

DSN_VARIABLE = "BT_TEST_DSN"
CIPHER_KEY = bytes(range(64))


def read_test_dsn() -> str:
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        pytest.fail(
            f"{DSN_VARIABLE} is not set: PostgreSQL storage tests must not be skipped silently. "
            "Point it at the isolated PostgreSQL 15+ test database.",
            pytrace=False,
        )
    return dsn


@pytest.fixture
def dsn_reader() -> Callable[[], str]:
    return read_test_dsn


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    return read_test_dsn()


@pytest.fixture
def schema_name(postgres_dsn: str) -> Iterator[str]:
    name = f"bt_store_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(postgres_dsn, poolclass=NullPool, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{name}"'))
    try:
        yield name
    finally:
        with admin.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
        admin.dispose()


@pytest.fixture
def migrated_schema(postgres_dsn: str, schema_name: str) -> str:
    upgrade_database(postgres_dsn, schema=schema_name)
    return schema_name


@pytest.fixture
def engine(postgres_dsn: str, migrated_schema: str) -> Iterator[Engine]:
    engine = create_database_engine(postgres_dsn, schema=migrated_schema)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def factory(engine: Engine) -> UnitOfWorkFactory:
    return SqlAlchemyUnitOfWorkFactory(engine)


@pytest.fixture
def sql(engine: Engine) -> Callable[..., list[Any]]:
    """Run a raw statement on its own connection and return the rows (committed state only)."""

    def run(statement: str, **parameters: Any) -> list[Any]:
        with engine.begin() as connection:
            result = connection.execute(sa.text(statement), parameters)
            return list(result.all()) if result.returns_rows else []

    return run


@pytest.fixture
def cipher() -> IdentifierCipher:
    return IdentifierCipher(CIPHER_KEY)


@pytest.fixture
def seed_user(factory: UnitOfWorkFactory) -> Callable[..., UserRecord]:
    """Commit a user (or return the existing one with that email); defaults to the seed user."""

    def seed(email: str = DEFAULT_EMAIL, display_name: str = "Seed User") -> UserRecord:
        return create_user(factory, email, display_name)

    return seed


@pytest.fixture
def seed_claimed_card(
    factory: UnitOfWorkFactory, cipher: IdentifierCipher, seed_user: Callable[..., UserRecord]
) -> Callable[..., SeededCard]:
    """Commit the user's claim, owner, account and card; `user=` defaults to the seed user."""

    def seed(user: UserRecord | None = None, **card: Any) -> SeededCard:
        return create_claimed_card(factory, cipher, user or seed_user(), **card)

    return seed


@pytest.fixture
def seed_card(seed_claimed_card: Callable[..., SeededCard]) -> Callable[..., SeededCard]:
    """A claimed card of the default seed user."""
    return seed_claimed_card


@pytest.fixture
def seed_transaction(factory: UnitOfWorkFactory) -> Callable[..., TransactionId]:
    """Commit one transaction on a seeded card; overrides as for `make_aggregate`."""

    def seed(card: SeededCard | CardId, run_id: ImportRunId | None = None, **overrides: Any):
        return create_transaction(factory, card, run_id, **overrides)

    return seed


@pytest.fixture
def start_run(factory: UnitOfWorkFactory) -> Callable[[], ImportRunId]:
    def start() -> ImportRunId:
        return start_import_run(factory)

    return start


@pytest.fixture
def new_aggregate() -> Callable[..., NewTransactionAggregate]:
    return make_aggregate


@pytest.fixture
def now() -> Callable[[], datetime]:
    return utc_now
