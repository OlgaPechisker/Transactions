"""Pure identity data: what the resolver is asked, what it may answer, and the final mapping."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.ids import AccountId, CardId, OwnerId
from bank_transactions.domain.immutability import freeze_field
from bank_transactions.domain.models import CardHintKey, CardIdentityHint

__all__ = [
    "AccountChoice",
    "AccountSelection",
    "CardCandidate",
    "CardIdentityHint",
    "CreateAccount",
    "CreateCard",
    "CreateOwner",
    "IdentityIssue",
    "IdentityQuestion",
    "IdentitySelection",
    "OwnerChoice",
    "OwnerSelection",
    "ResolvedCardIdentity",
    "ResolvedCards",
    "UseAccount",
    "UseCard",
    "UseOwner",
]


class IdentityIssue(StrEnum):
    NO_MATCHING_CARD = "NO_MATCHING_CARD"
    AMBIGUOUS_CARDS = "AMBIGUOUS_CARDS"
    CONFLICTING_HINTS = "CONFLICTING_HINTS"


@dataclass(frozen=True)
class OwnerChoice:
    owner_id: OwnerId
    display_name: str


@dataclass(frozen=True)
class AccountChoice:
    account_id: AccountId
    owner_id: OwnerId
    bank_code: str | None
    display_name: str | None
    account_number: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CardCandidate:
    card_id: CardId
    owner_id: OwnerId
    account_id: AccountId
    issuer_code: IssuerCode
    display_name: str | None
    last4: str = field(repr=False)


@dataclass(frozen=True)
class IdentityQuestion:
    """One card the service could not bind on its own. `determined_*` is what the service has
    already settled (None = the resolver must choose). `owners`/`accounts` are the existing
    records that may be picked; `candidates` are the cards sharing issuer + last 4."""

    hint: CardIdentityHint
    issue: IdentityIssue
    owners: tuple[OwnerChoice, ...] = ()
    accounts: tuple[AccountChoice, ...] = ()
    candidates: tuple[CardCandidate, ...] = ()
    determined_owner: OwnerChoice | None = None
    determined_account: AccountChoice | None = None

    def __post_init__(self) -> None:
        for name in ("owners", "accounts", "candidates"):
            freeze_field(self, name, tuple)


def _require_name(value: str | None, what: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{what} cannot be blank")


@dataclass(frozen=True)
class UseOwner:
    owner_id: OwnerId


@dataclass(frozen=True)
class CreateOwner:
    display_name: str

    def __post_init__(self) -> None:
        _require_name(self.display_name, "owner name")


@dataclass(frozen=True)
class UseAccount:
    account_id: AccountId


@dataclass(frozen=True)
class CreateAccount:
    """The bank code and account number come from the file hint; only a label is chosen."""

    display_name: str | None = None

    def __post_init__(self) -> None:
        _require_name(self.display_name, "account label")


@dataclass(frozen=True)
class UseCard:
    card_id: CardId


@dataclass(frozen=True)
class CreateCard:
    """owner/account of None mean "keep what the question already determined"."""

    owner: "OwnerSelection | None" = None
    account: "AccountSelection | None" = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        _require_name(self.display_name, "card label")


OwnerSelection = UseOwner | CreateOwner
AccountSelection = UseAccount | CreateAccount
IdentitySelection = UseCard | CreateCard


@dataclass(frozen=True)
class ResolvedCardIdentity:
    card_id: CardId
    owner_id: OwnerId
    account_id: AccountId


@dataclass(frozen=True)
class ResolvedCards:
    """Source card key -> database card id. Parser output is never rewritten with ids."""

    cards: Mapping[CardHintKey, CardId]

    def __post_init__(self) -> None:
        freeze_field(self, "cards", lambda cards: MappingProxyType(dict(cards)))

    def card_id_for(self, key: CardHintKey) -> CardId:
        try:
            return self.cards[key]
        except KeyError:
            raise KeyError("source card was not resolved") from None

    def __contains__(self, key: object) -> bool:
        return key in self.cards

    def __len__(self) -> int:
        return len(self.cards)
