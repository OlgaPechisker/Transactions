"""Immutable read models returned by repositories."""

from dataclasses import dataclass

from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.ids import AccountId, CardId, IdentifierToken, OwnerId
from bank_transactions.domain.keys import OwnerMatchKey


@dataclass(frozen=True)
class OwnerRecord:
    owner_id: OwnerId
    display_name: str
    match_key: OwnerMatchKey


@dataclass(frozen=True)
class AccountRecord:
    account_id: AccountId
    owner_id: OwnerId
    bank_code: str | None
    account_number_enc: IdentifierToken | None
    display_name: str | None


@dataclass(frozen=True)
class CardRecord:
    card_id: CardId
    owner_id: OwnerId
    account_id: AccountId
    issuer_code: IssuerCode
    last4_enc: IdentifierToken
    display_name: str | None


@dataclass(frozen=True)
class CardLookup:
    """Candidate search: issuer + last4 ciphertext, optionally narrowed by account or owner."""

    issuer_code: IssuerCode
    last4_enc: IdentifierToken
    account_id: AccountId | None = None
    owner_id: OwnerId | None = None
