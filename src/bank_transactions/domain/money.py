import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Self

AMOUNT_PLACES = Decimal("0.01")
RATE_PLACES = Decimal("0.000001")
_CURRENCY_CODE = re.compile(r"[A-Z]{3}")


def round_amount(amount: Decimal) -> Decimal:
    """NUMERIC(14,2) rounding used wherever amounts are persisted or compared."""
    return amount.quantize(AMOUNT_PLACES, rounding=ROUND_HALF_UP)


def round_rate(rate: Decimal) -> Decimal:
    """NUMERIC(18,6) rounding; applied only when a rate is persisted."""
    return rate.quantize(RATE_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, order=True)
class Currency:
    code: str

    def __post_init__(self) -> None:
        if _CURRENCY_CODE.fullmatch(self.code) is None:
            raise ValueError("currency code must be three uppercase letters")

    def __str__(self) -> str:
        return self.code


ILS = Currency("ILS")
USD = Currency("USD")
EUR = Currency("EUR")


@dataclass(frozen=True)
class Money:
    """A signed Decimal amount; a currency of None means the source did not state one."""

    amount: Decimal
    currency: Currency | None

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("money amounts must be Decimal")
        if not self.amount.is_finite():
            raise ValueError("money amounts must be finite")

    def __add__(self, other: Self) -> Self:
        self._require_same_currency(other)
        return type(self)(self.amount + other.amount, self.currency)

    def __neg__(self) -> Self:
        return type(self)(-self.amount, self.currency)

    def is_negative(self) -> bool:
        return self.amount < 0

    def is_zero(self) -> bool:
        return self.amount == 0

    def rounded(self) -> Self:
        return type(self)(round_amount(self.amount), self.currency)

    def _require_same_currency(self, other: Self) -> None:
        if self.currency != other.currency:
            raise ValueError("cannot combine amounts in different currencies")
