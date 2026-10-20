"""Immutable parser-facing data. Plaintext identifiers appear only in memory and never in repr."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Self

from bank_transactions.domain.enums import (
    Channel,
    IssuerCode,
    ReviewReason,
    TransactionType,
)
from bank_transactions.domain.immutability import freeze_field
from bank_transactions.domain.issues import RowIssue, SourceLocation
from bank_transactions.domain.money import Currency, Money, effective_fx_rate
from bank_transactions.domain.text import canonical_account_number


def _require_date(value: object, name: str) -> None:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{name} must be a date")


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} cannot be blank")


@dataclass(frozen=True)
class CardHintKey:
    """Identifies a card within one source file (issuer + last 4 digits); not a database id."""

    issuer_code: IssuerCode
    last4: str = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.last4) != 4 or not all(c in "0123456789" for c in self.last4):
            raise ValueError("last4 must be exactly four ASCII digits")


@dataclass(frozen=True)
class OwnerHint:
    display_name: str

    def __post_init__(self) -> None:
        _require_text(self.display_name, "owner name")


@dataclass(frozen=True)
class AccountHint:
    bank_code: str | None = None
    account_number: str | None = field(default=None, repr=False)
    display_name: str | None = None

    def __post_init__(self) -> None:
        if self.account_number is not None:
            object.__setattr__(
                self, "account_number", canonical_account_number(self.account_number)
            )


@dataclass(frozen=True)
class CardHint:
    """`label` is product text only (e.g. a card product name); it never carries an identifier."""

    key: CardHintKey
    label: str | None = None

    def __post_init__(self) -> None:
        if self.label is not None and self.key.last4 in self.label:
            raise ValueError("card label must not contain the card identifier")


@dataclass(frozen=True)
class CardIdentityHint:
    """Everything the file says about one card: its own label plus statement-level owner/account."""

    card: CardHint
    owner: OwnerHint | None = None
    account: AccountHint | None = None


@dataclass(frozen=True)
class StatementPeriod:
    start: date | None = None
    end: date | None = None

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("period start cannot follow its end")


@dataclass(frozen=True)
class StatementTotals:
    """Per-currency charged totals; a None currency is the unknown-currency bucket."""

    amounts: tuple[Money, ...] = ()

    def __post_init__(self) -> None:
        freeze_field(self, "amounts", self._ordered)

    @classmethod
    def of(cls, *amounts: Money) -> Self:
        summed: dict[Currency | None, Decimal] = {}
        for money in amounts:
            summed[money.currency] = summed.get(money.currency, Decimal(0)) + money.amount
        return cls(tuple(Money(total, currency) for currency, total in summed.items()))

    @property
    def by_currency(self) -> Mapping[Currency | None, Decimal]:
        return MappingProxyType({money.currency: money.amount for money in self.amounts})

    @staticmethod
    def _ordered(amounts: tuple[Money, ...]) -> tuple[Money, ...]:
        ordered = tuple(
            sorted(amounts, key=lambda money: money.currency.code if money.currency else "")
        )
        if len({money.currency for money in ordered}) != len(ordered):
            raise ValueError("totals must have one amount per currency")
        return ordered


@dataclass(frozen=True)
class StatementContext:
    issuer_code: IssuerCode
    cards: tuple[CardHint, ...]
    owner: OwnerHint | None = None
    account: AccountHint | None = field(default=None, repr=False)
    period: StatementPeriod | None = None
    charge_date: date | None = None
    declared_totals: StatementTotals = field(default_factory=StatementTotals)

    def __post_init__(self) -> None:
        freeze_field(self, "cards", tuple)
        if len({card.key for card in self.cards}) != len(self.cards):
            raise ValueError("a statement cannot repeat a card")

    def identity_hints(self) -> tuple[CardIdentityHint, ...]:
        return tuple(CardIdentityHint(card, self.owner, self.account) for card in self.cards)


@dataclass(frozen=True)
class ParsedFxDetail:
    """FX facts of a foreign transaction. commission_amount is in the billing currency; the
    effective rate is billing/original at full precision and keeps the source sign."""

    commission_amount: Decimal
    stated_fx_rate: Decimal | None
    effective_fx_rate: Decimal

    def __post_init__(self) -> None:
        rates = (self.commission_amount, self.stated_fx_rate, self.effective_fx_rate)
        if any(rate is not None and not rate.is_finite() for rate in rates):
            raise ValueError("FX amounts must be finite")


@dataclass(frozen=True)
class ParsedInstallmentRef:
    """The "charge N of M" facts of one row; plan_total is the whole purchase, not this charge."""

    installment_number: int
    total_installments: int
    plan_total: Money | None = None
    issuer_reference: str | None = None

    def __post_init__(self) -> None:
        if (
            self.total_installments < 1
            or not 1 <= self.installment_number <= self.total_installments
        ):
            raise ValueError("installment number must be within 1..total")


@dataclass(frozen=True)
class ParsedInstallmentPlan:
    card_key: CardHintKey
    merchant_name_norm: str
    purchase_date: date
    total: Money
    total_installments: int
    issuer_reference: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.merchant_name_norm, "plan merchant")
        _require_date(self.purchase_date, "purchase_date")
        if self.total.currency is None:
            raise ValueError("an installment plan needs a known currency")
        if self.total_installments < 1:
            raise ValueError("a plan has at least one installment")


@dataclass(frozen=True)
class FeeCandidate:
    """A foreign-commission row the parser recognised; the shared folder matches it to a parent."""

    card_key: CardHintKey
    location: SourceLocation
    transaction_date: date
    charge_date: date
    amount: Money


@dataclass(frozen=True)
class ParsedTransaction:
    card_key: CardHintKey
    location: SourceLocation
    transaction_date: date
    charge_date: date
    billing: Money
    merchant_name_raw: str
    merchant_name_norm: str
    transaction_type: TransactionType
    original: Money | None = None
    merchant_city: str | None = None
    merchant_country: str | None = None
    bank_category_raw: str | None = None
    channel: Channel | None = None
    notes: str | None = None
    issuer_reference: str | None = None
    installment: ParsedInstallmentRef | None = None
    installment_plan: ParsedInstallmentPlan | None = None
    fx: ParsedFxDetail | None = None
    review_reasons: frozenset[ReviewReason] = field(default=frozenset())

    def __post_init__(self) -> None:
        freeze_field(self, "review_reasons", frozenset)
        _require_date(self.transaction_date, "transaction_date")
        _require_date(self.charge_date, "charge_date")
        _require_text(self.merchant_name_raw, "merchant name")
        _require_text(self.merchant_name_norm, "normalised merchant name")
        self._check_currency_review()
        self._check_installment()
        self._check_fx()

    @property
    def is_foreign(self) -> bool:
        return self.fx is not None

    def _check_currency_review(self) -> None:
        unknown = self.billing.currency is None or (
            self.original is not None and self.original.currency is None
        )
        if unknown and ReviewReason.UNKNOWN_CURRENCY not in self.review_reasons:
            raise ValueError("an unknown currency needs the UNKNOWN_CURRENCY review reason")

    def _check_installment(self) -> None:
        plan = self.installment_plan
        if plan is None:
            return
        if self.installment is None:
            raise ValueError("an installment plan needs the installment reference")
        if plan.total_installments != self.installment.total_installments:
            raise ValueError("plan and installment reference disagree on the count")
        if plan.card_key != self.card_key:
            raise ValueError("plan belongs to a different card")

    def _check_fx(self) -> None:
        if self.fx is None:
            return
        if self.original is None or self.original.is_zero() or self.billing.currency is None:
            raise ValueError("a foreign transaction needs an original amount and billing currency")
        if self.fx.effective_fx_rate != effective_fx_rate(
            self.billing.amount, self.original.amount
        ):
            raise ValueError("effective FX rate disagrees with the billing and original amounts")


@dataclass(frozen=True)
class StatementParseResult:
    transactions: tuple[ParsedTransaction, ...]
    issues: tuple[RowIssue, ...]
    rows_seen: int
    calculated_totals: StatementTotals
    installment_plans: tuple[ParsedInstallmentPlan, ...] = ()

    def __post_init__(self) -> None:
        freeze_field(self, "transactions", tuple)
        freeze_field(self, "issues", tuple)
        freeze_field(self, "installment_plans", tuple)
        if self.rows_seen < 0:
            raise ValueError("rows_seen cannot be negative")
