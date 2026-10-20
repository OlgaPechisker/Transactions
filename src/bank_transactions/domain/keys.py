"""Comparison keys shared by the mapper, the service and the repositories.

Equality is NULL-safe: a missing value equals only another missing value.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Self

from bank_transactions.domain.ids import CardId
from bank_transactions.domain.money import Currency, round_amount
from bank_transactions.domain.text import fold_text


@dataclass(frozen=True)
class IssuerReferenceKey:
    """Identity of a transaction that carries an issuer reference. Installment charges that
    share a voucher stay distinct through installment_number; None means "not an installment"."""

    card_id: CardId
    transaction_date: date
    issuer_reference: str
    installment_number: int | None

    def __post_init__(self) -> None:
        if not self.issuer_reference.strip():
            raise ValueError("an issuer reference cannot be blank")


@dataclass(frozen=True)
class ComparisonKey:
    """Group key for reference-less transactions (plan section 7). Amounts are compared at the
    persisted NUMERIC(14,2) precision so file and database values agree."""

    card_id: CardId
    transaction_date: date
    merchant_name_norm: str
    original_amount: Decimal | None
    original_currency: Currency | None
    installment_number: int | None

    def __post_init__(self) -> None:
        if self.original_amount is not None:
            object.__setattr__(self, "original_amount", round_amount(self.original_amount))


@dataclass(frozen=True)
class OwnerMatchKey:
    value: str

    @classmethod
    def from_display_name(cls, display_name: str) -> Self:
        return cls(fold_text(display_name))

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("an owner name cannot be blank")
