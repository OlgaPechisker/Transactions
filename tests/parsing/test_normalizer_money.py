from decimal import Decimal

import pytest

from bank_transactions.domain.money import EUR, ILS, USD, Currency, Money
from bank_transactions.parsing.normalizers.amounts import (
    InvalidAmountError,
    parse_amount,
    parse_money_text,
    parse_rate,
)
from bank_transactions.parsing.normalizers.currency import parse_currency


def test_numeric_cells_keep_their_printed_decimal_digits():
    assert parse_amount(245.83) == Decimal("245.83")
    assert parse_amount(2950) == Decimal("2950")
    assert parse_amount(2.3000000000000003) == Decimal("2.3000000000000003")
    assert parse_amount(Decimal("1.50")) == Decimal("1.50")


def test_text_amounts_may_carry_symbols_separators_and_signs():
    assert parse_amount("1,337.60") == Decimal("1337.60")
    assert parse_amount("₪ 1,337.60") == Decimal("1337.60")
    assert parse_amount("-24.00") == Decimal("-24.00")
    assert parse_amount("(24.00)") == Decimal("-24.00")
    assert parse_amount("\u202b12.5\u202c") == Decimal("12.5")


def test_zero_and_negative_amounts_are_real_values():
    assert parse_amount(0) == Decimal(0)
    assert parse_amount(-2092.21) == Decimal("-2092.21")


def test_blank_cells_are_missing_not_zero():
    assert parse_amount(None) is None
    assert parse_amount("  ") is None


@pytest.mark.parametrize("bad", ["abc", "12.3.4", float("nan"), float("inf"), "NaN", True])
def test_unreadable_amounts_raise(bad):
    with pytest.raises(InvalidAmountError):
        parse_amount(bad)


def test_rate_cells_with_padding_or_wording():
    assert parse_rate(" 3.2900") == Decimal("3.29")
    assert parse_rate('שער המרה מדולר לש"ח 2.9950') == Decimal("2.995")
    assert parse_rate(0.0183) == Decimal("0.0183")
    assert parse_rate(None) is None
    assert parse_rate("") is None


def test_rate_without_a_number_raises():
    with pytest.raises(InvalidAmountError):
        parse_rate("שער")


def test_footer_and_banner_money_text():
    assert parse_money_text("20349.01₪") == Money(Decimal("20349.01"), ILS)
    assert parse_money_text("₪ 1,337.60") == Money(Decimal("1337.60"), ILS)
    assert parse_money_text("$ 26.39") == Money(Decimal("26.39"), USD)
    assert parse_money_text("645.13 ₪") == Money(Decimal("645.13"), ILS)
    assert parse_money_text("-5.5₪") == Money(Decimal("-5.5"), ILS)


def test_money_text_without_a_currency_symbol_has_an_unknown_currency():
    assert parse_money_text("12.50") == Money(Decimal("12.50"), None)


def test_money_text_that_is_not_money_is_none():
    assert parse_money_text("סך הכל") is None
    assert parse_money_text(None) is None
    assert parse_money_text(12.5) is None


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("₪", ILS),
        ('ש"ח', ILS),
        ("ש״ח", ILS),
        ("ILS", ILS),
        ("nis", ILS),
        ("$", USD),
        ("USD", USD),
        ("דולר", USD),
        ("€", EUR),
        ("אירו", EUR),
        ("£", Currency("GBP")),
        ("JPY", Currency("JPY")),
        (" ₪ ", ILS),
    ],
)
def test_known_currency_spellings(cell, expected):
    assert parse_currency(cell) == expected


@pytest.mark.parametrize("cell", [None, "", "  ", "???", "ש", 5])
def test_blank_or_unrecognised_currency_is_unknown(cell):
    assert parse_currency(cell) is None
