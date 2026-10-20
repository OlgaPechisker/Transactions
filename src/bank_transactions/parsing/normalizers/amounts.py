"""Decimal amount, rate and money-text parsing. Floats go through their shortest repr, so a
cell printed as 245.83 becomes exactly Decimal("245.83")."""

import re
from decimal import Decimal, InvalidOperation

from bank_transactions.domain.money import Money
from bank_transactions.domain.workbook import CellValue
from bank_transactions.parsing.normalizers.currency import parse_currency
from bank_transactions.parsing.normalizers.text import strip_bidi_marks

_PLAIN_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)")
_NUMBER_IN_TEXT = re.compile(r"\d+(?:\.\d+)?")
_AMOUNT_SYMBOLS = re.compile(r"[₪$€£\s,]")
_MONEY_TEXT = re.compile(
    r"(?P<prefix>[^\d\-+.,()\s]+)?\s*(?P<sign>-)?\s*(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<suffix>[^\d\s]+)?"
)


class InvalidAmountError(ValueError):
    """A non-blank cell that cannot be read as an amount or rate."""


def parse_amount(value: CellValue) -> Decimal | None:
    """Decimal of a numeric or text amount; blank is None, anything unreadable raises."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidAmountError("a boolean is not an amount")
    if isinstance(value, str):
        return _parse_text_amount(value)
    if isinstance(value, int | float | Decimal):
        return _finite(Decimal(str(value)))
    raise InvalidAmountError("unsupported amount cell")


def parse_rate(value: CellValue) -> Decimal | None:
    """An FX rate cell: a number, possibly padded or wrapped in wording."""
    if not isinstance(value, str):
        return parse_amount(value)
    text = strip_bidi_marks(value).strip()
    if not text:
        return None
    numbers = _NUMBER_IN_TEXT.findall(text)
    if not numbers:
        raise InvalidAmountError("no rate in cell")
    return Decimal(numbers[-1])


def parse_money_text(value: CellValue) -> Money | None:
    """Money printed as text, e.g. `20349.01₪`, `₪ 1,337.60`, `$ 26.39`; None if not money."""
    if not isinstance(value, str):
        return None
    match = _MONEY_TEXT.fullmatch(strip_bidi_marks(value).strip())
    if match is None:
        return None
    amount = Decimal(match.group("number").replace(",", ""))
    if match.group("sign"):
        amount = -amount
    currency = parse_currency(match.group("prefix") or match.group("suffix"))
    return Money(amount, currency)


def _parse_text_amount(text: str) -> Decimal | None:
    cleaned = _AMOUNT_SYMBOLS.sub("", strip_bidi_marks(text)).strip()
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    plain = cleaned[1:-1] if negative else cleaned
    if _PLAIN_NUMBER.fullmatch(plain) is None:
        raise InvalidAmountError("cell is not a number")
    amount = Decimal(plain)
    return -amount if negative else amount


def _finite(amount: Decimal) -> Decimal:
    try:
        if amount.is_finite():
            return amount
    except InvalidOperation:
        pass
    raise InvalidAmountError("amount is not finite")
