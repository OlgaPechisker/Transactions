from decimal import Decimal

import pytest

from bank_transactions.domain.money import (
    ILS,
    USD,
    Currency,
    Money,
    round_amount,
    round_rate,
)


def test_currency_is_a_three_letter_uppercase_code():
    assert str(Currency("USD")) == "USD"
    assert Currency("ILS") == ILS


@pytest.mark.parametrize("code", ["", "il", "ils", "ILSX", "12A"])
def test_invalid_currency_codes_are_rejected(code):
    with pytest.raises(ValueError):
        Currency(code)


def test_adding_same_currency_sums_amounts():
    total = Money(Decimal("10.50"), ILS) + Money(Decimal("2.25"), ILS)

    assert total == Money(Decimal("12.75"), ILS)


def test_adding_different_currencies_is_rejected():
    with pytest.raises(ValueError):
        Money(Decimal("1"), ILS) + Money(Decimal("1"), USD)


def test_unknown_currency_only_adds_to_unknown_currency():
    unknown = Money(Decimal("660"), None)

    assert unknown + unknown == Money(Decimal("1320"), None)
    with pytest.raises(ValueError):
        unknown + Money(Decimal("1"), ILS)


def test_amounts_must_be_decimal_not_float():
    with pytest.raises(TypeError):
        Money(10.5, ILS)  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["NaN", "Infinity"])
def test_non_finite_amounts_are_rejected(raw):
    with pytest.raises(ValueError):
        Money(Decimal(raw), ILS)


def test_negation_and_sign_queries_preserve_source_sign():
    refund = -Money(Decimal("5.00"), ILS)

    assert refund == Money(Decimal("-5.00"), ILS)
    assert refund.is_negative()
    assert Money(Decimal("0.00"), ILS).is_zero()


def test_equality_ignores_decimal_scale():
    assert Money(Decimal("10.0"), ILS) == Money(Decimal("10.00"), ILS)
    assert hash(Money(Decimal("10.0"), ILS)) == hash(Money(Decimal("10.00"), ILS))


def test_round_amount_is_half_away_from_zero_with_two_places():
    assert round_amount(Decimal("1.005")) == Decimal("1.01")
    assert round_amount(Decimal("-1.005")) == Decimal("-1.01")
    assert Money(Decimal("2.345"), ILS).rounded() == Money(Decimal("2.35"), ILS)


def test_round_rate_keeps_six_places():
    assert round_rate(Decimal("0.0183456789")) == Decimal("0.018346")
