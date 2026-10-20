"""Shared fixtures for the PostgreSQL-backed storage tests.

Every DB test gets its own freshly created schema (with its own enum types) on the isolated test
database named by BT_TEST_DSN, and only that schema is dropped afterwards.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from bank_transactions.domain.enums import (
    IdentifierType,
    IssuerCode,
    TransactionType,
)
from bank_transactions.domain.ids import AccountId, CardId, ImportRunId, OwnerId
from bank_transactions.domain.keys import OwnerMatchKey
from bank_transactions.domain.money import ILS
from bank_transactions.identity.crypto import IdentifierCipher
from bank_transactions.persistence.commands import (
    NewAccount,
    NewCard,
    NewImportRun,
    NewOwner,
    NewTransaction,
    NewTransactionAggregate,
)
from bank_transactions.persistence.db import create_database_engine, upgrade_database
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
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


@dataclass(frozen=True)
class SeededCard:
    owner_id: OwnerId
    account_id: AccountId
    card_id: CardId


@pytest.fixture
def seed_card(factory: UnitOfWorkFactory, cipher: IdentifierCipher) -> Callable[..., SeededCard]:
    """Create and commit an owner, an account and a card."""

    def seed(
        last4: str = "4396",
        issuer: IssuerCode = IssuerCode.MAX,
        owner_name: str = "Dana Levi",
        account_number: str | None = "8917221038",
        bank_code: str | None = "LEUMI",
    ) -> SeededCard:
        with factory.create() as uow:
            match_key = OwnerMatchKey.from_display_name(owner_name)
            owner = uow.owners.find_by_match_key(match_key) or uow.owners.create(
                NewOwner(owner_name, match_key)
            )
            number = (
                cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, account_number)
                if account_number
                else None
            )
            account = uow.accounts.create(NewAccount(owner.owner_id, bank_code, number, None))
            card = uow.cards.create(
                NewCard(
                    owner.owner_id,
                    account.account_id,
                    issuer,
                    cipher.encrypt(IdentifierType.CARD_LAST4, last4),
                    None,
                )
            )
            uow.commit()
        return SeededCard(owner.owner_id, account.account_id, card.card_id)

    return seed


@pytest.fixture
def start_run(factory: UnitOfWorkFactory) -> Callable[[], ImportRunId]:
    def start() -> ImportRunId:
        with factory.create() as uow:
            run_id = uow.import_runs.start_run(
                NewImportRun("file-" + uuid.uuid4().hex[:8], "name-1", "a" * 64, utc_now())
            )
            uow.commit()
        return run_id

    return start


def utc_now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def new_aggregate() -> Callable[..., NewTransactionAggregate]:
    return make_aggregate


@pytest.fixture
def now() -> Callable[[], datetime]:
    return utc_now


def make_aggregate(
    card_id: CardId, run_id: ImportRunId, **overrides: Any
) -> NewTransactionAggregate:
    """A committed-shape aggregate; `aggregate_*` overrides target the aggregate itself."""
    aggregate_fields = {
        key.removeprefix("aggregate_"): overrides.pop(key)
        for key in list(overrides)
        if key.startswith("aggregate_")
    }
    values: dict[str, Any] = {
        "card_id": card_id,
        "import_run_id": run_id,
        "issuer_reference": None,
        "transaction_date": date(2026, 7, 3),
        "charge_date": date(2026, 8, 2),
        "billing_amount": Decimal("120.00"),
        "billing_currency": ILS,
        "original_amount": None,
        "original_currency": None,
        "merchant_name_raw": "Super  Pharm",
        "merchant_name_norm": "super pharm",
        "merchant_city": None,
        "merchant_country": None,
        "bank_category_raw": None,
        "transaction_type": TransactionType.REGULAR,
        "channel": None,
        "notes": None,
        "installment_plan_id": None,
        "installment_number": None,
    }
    return NewTransactionAggregate(NewTransaction(**{**values, **overrides}), **aggregate_fields)
