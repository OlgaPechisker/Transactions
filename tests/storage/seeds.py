"""Plain seed functions for tests that need committed users, claimed cards and transactions.

Importable from any test package (`from tests.storage.seeds import ...`); the storage fixtures in
conftest.py are thin wrappers around them.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bank_transactions.domain.enums import IdentifierType, IssuerCode, TransactionType
from bank_transactions.domain.ids import (
    AccountId,
    CardId,
    ClaimId,
    ImportRunId,
    OwnerId,
    TransactionId,
    UserId,
)
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
    NewUser,
)
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import UserRecord

DEFAULT_EMAIL = "seed.user@example.com"


@dataclass(frozen=True)
class SeededCard:
    user_id: UserId
    owner_id: OwnerId
    account_id: AccountId
    card_id: CardId
    claim_id: ClaimId


def utc_now() -> datetime:
    return datetime.now(UTC)


class ServiceForUser:
    """The import service acting for one user, so a test reads `import_file(path)`."""

    def __init__(self, service: Any, user_id: UserId) -> None:
        self._service = service
        self._user_id = user_id

    def import_file(self, path: Path, **options: Any) -> Any:
        return self._service.import_file(path, user_id=self._user_id, **options)


def create_user(
    factory: UnitOfWorkFactory,
    email: str = DEFAULT_EMAIL,
    display_name: str = "Seed User",
) -> UserRecord:
    """The user with this email, created on first use."""
    key = email.strip().casefold()
    with factory.create() as uow:
        existing = uow.users.find_by_email_key(key)
        if existing is not None:
            return existing
        user = uow.users.create(NewUser(email, key, display_name, "not-a-real-hash"))
        uow.commit()
    return user


def create_claimed_card(
    factory: UnitOfWorkFactory,
    cipher: IdentifierCipher,
    user: UserRecord,
    last4: str = "4396",
    issuer: IssuerCode = IssuerCode.MAX,
    owner_name: str = "Dana Levi",
    account_number: str | None = "8917221038",
    bank_code: str | None = "LEUMI",
    same_account_as: SeededCard | None = None,
) -> SeededCard:
    """Commit the user's claim on the card key and the card, with its owner and account."""
    last4_enc = cipher.encrypt(IdentifierType.CARD_LAST4, last4)
    with factory.create() as uow:
        claim = uow.claims.create_or_get(user.user_id, issuer, last4_enc)
        if same_account_as is not None:
            owner_id, account_id = same_account_as.owner_id, same_account_as.account_id
        else:
            match_key = OwnerMatchKey.from_display_name(owner_name)
            owner = uow.owners.find_by_match_key(match_key, user.user_id) or uow.owners.create(
                NewOwner(owner_name, match_key, user.user_id)
            )
            number = (
                cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, account_number)
                if account_number
                else None
            )
            account = (
                uow.accounts.find_placeholder(owner.owner_id)
                if number is None and bank_code is None
                else None
            ) or uow.accounts.create(NewAccount(owner.owner_id, bank_code, number, None))
            owner_id, account_id = owner.owner_id, account.account_id
        card = uow.cards.create(
            NewCard(owner_id, account_id, issuer, last4_enc, None, claim.claim_id)
        )
        uow.commit()
    return SeededCard(user.user_id, owner_id, account_id, card.card_id, claim.claim_id)


def start_import_run(factory: UnitOfWorkFactory) -> ImportRunId:
    """A RUNNING import run with a file hash of its own (only one RUNNING run per hash exists)."""
    with factory.create() as uow:
        run_id = uow.import_runs.start_run(
            NewImportRun("file-" + uuid.uuid4().hex[:8], "name-1", uuid.uuid4().hex * 2, utc_now())
        )
        uow.commit()
    return run_id


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


def create_transaction(
    factory: UnitOfWorkFactory,
    card: SeededCard | CardId,
    run_id: ImportRunId | None = None,
    **overrides: Any,
) -> TransactionId:
    """Commit one transaction on the card; `overrides` as for `make_aggregate`."""
    card_id = card.card_id if isinstance(card, SeededCard) else card
    run = run_id if run_id is not None else start_import_run(factory)
    with factory.create() as uow:
        (transaction_id,) = uow.transactions.insert_many(
            [make_aggregate(card_id, run, **overrides)]
        )
        uow.commit()
    return transaction_id
