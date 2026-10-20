"""Fixtures for the end-to-end tests: real example workbooks, real adapters, real PostgreSQL.

Every test gets its own freshly migrated schema on the isolated database named by BT_TEST_DSN;
only that schema is ever dropped. Nothing here is a mock of the library under test.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from bank_transactions.domain.enums import IdentifierType, IssuerCode
from bank_transactions.domain.errors import IdentityUnresolvedError
from bank_transactions.domain.ids import AccountId, CardId, OwnerId
from bank_transactions.domain.keys import OwnerMatchKey
from bank_transactions.identity.crypto import IdentifierCipher
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import (
    CreateAccount,
    CreateCard,
    CreateOwner,
    IdentityQuestion,
    IdentitySelection,
)
from bank_transactions.parsing.default_registry import build_default_registry
from bank_transactions.parsing.workbook import ExcelWorkbookReader
from bank_transactions.persistence.commands import NewAccount, NewCard, NewOwner
from bank_transactions.persistence.db import create_database_engine, upgrade_database
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.uow import SqlAlchemyUnitOfWorkFactory
from bank_transactions.services.import_service import StatementImportService

DSN_VARIABLE = "BT_TEST_DSN"
CIPHER_KEY = bytes(range(64))
EXAMPLES_DIRECTORY = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        pytest.fail(
            f"{DSN_VARIABLE} is not set: PostgreSQL end-to-end tests must not be skipped silently. "
            "Point it at the isolated PostgreSQL 15+ test database.",
            pytrace=False,
        )
    return dsn


@pytest.fixture
def schema_name(postgres_dsn: str) -> Iterator[str]:
    name = f"bt_e2e_{uuid.uuid4().hex[:12]}"
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
    """Run a raw statement on its own connection and return the committed rows."""

    def run(statement: str, **parameters: Any) -> list[Any]:
        with engine.begin() as connection:
            result = connection.execute(sa.text(statement), parameters)
            return list(result.all()) if result.returns_rows else []

    return run


@pytest.fixture
def scalar(sql: Callable[..., list[Any]]) -> Callable[..., Any]:
    def run(statement: str, **parameters: Any) -> Any:
        return sql(statement, **parameters)[0][0]

    return run


@pytest.fixture
def cipher() -> IdentifierCipher:
    return IdentifierCipher(CIPHER_KEY)


@pytest.fixture
def examples() -> dict[str, Path]:
    def only(prefix: str) -> Path:
        (path,) = [p for p in EXAMPLES_DIRECTORY.glob("*.xlsx") if p.name.startswith(prefix)]
        return path

    return {
        "isracard": only("9462"),
        "max_monthly": only("transaction-details_export_1785931743433"),
        "max_yearly": only("transaction-details_export_1785938963329"),
        "cal": only("\u05e4\u05d9\u05e8\u05d5\u05d8"),
    }


class AnsweringResolver(IdentityResolver):
    """Stands in for a person: records each question and creates what it was not given."""

    def __init__(self) -> None:
        self.questions: list[IdentityQuestion] = []

    def complete(self, question: IdentityQuestion) -> IdentitySelection:
        self.questions.append(question)
        named = question.hint.owner.display_name if question.hint.owner else "E2E Owner"
        owner = None if question.determined_owner else CreateOwner(named)
        account = None if question.determined_account else CreateAccount("E2E Account")
        return CreateCard(owner, account, "E2E Card")


class RefusingResolver(IdentityResolver):
    """What a non-interactive run does, but recording what it was asked."""

    def __init__(self) -> None:
        self.questions: list[IdentityQuestion] = []

    def complete(self, question: IdentityQuestion) -> IdentitySelection:
        self.questions.append(question)
        raise IdentityUnresolvedError([question.hint.card.key])


@pytest.fixture
def answering_resolver() -> AnsweringResolver:
    return AnsweringResolver()


@pytest.fixture
def refusing_resolver() -> RefusingResolver:
    return RefusingResolver()


@pytest.fixture
def make_service(
    factory: UnitOfWorkFactory,
    cipher: IdentifierCipher,
    answering_resolver: AnsweringResolver,
) -> Callable[..., StatementImportService]:
    def make(resolver: IdentityResolver | None = None) -> StatementImportService:
        return StatementImportService(
            ExcelWorkbookReader(),
            build_default_registry(),
            cipher,
            resolver or answering_resolver,
            factory,
        )

    return make


@pytest.fixture
def service(make_service: Callable[..., StatementImportService]) -> StatementImportService:
    return make_service()


@dataclass(frozen=True)
class SeededCard:
    owner_id: OwnerId
    account_id: AccountId
    card_id: CardId


@pytest.fixture
def seed_card(factory: UnitOfWorkFactory, cipher: IdentifierCipher) -> Callable[..., SeededCard]:
    """Create and commit an owner, an account and a card, as an earlier import would have."""

    def seed(
        last4: str,
        issuer: IssuerCode,
        owner_name: str = "Seeded Owner",
        account_number: str | None = None,
        bank_code: str | None = None,
        same_account_as: SeededCard | None = None,
    ) -> SeededCard:
        with factory.create() as uow:
            if same_account_as is not None:
                card = uow.cards.create(
                    NewCard(
                        same_account_as.owner_id,
                        same_account_as.account_id,
                        issuer,
                        cipher.encrypt(IdentifierType.CARD_LAST4, last4),
                        None,
                    )
                )
                uow.commit()
                return SeededCard(
                    same_account_as.owner_id, same_account_as.account_id, card.card_id
                )
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
def stored_text(sql: Callable[..., list[Any]], migrated_schema: str) -> Callable[[], str]:
    """Every stored value of every table, rendered as one string, for privacy scans."""

    def dump() -> str:
        tables = [
            row[0]
            for row in sql(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name <> 'alembic_version'",
                schema=migrated_schema,
            )
        ]
        parts = []
        for table in tables:
            parts.extend(row[0] for row in sql(f'SELECT row_to_json(t)::text FROM "{table}" t'))
        return "\n".join(parts)

    return dump
