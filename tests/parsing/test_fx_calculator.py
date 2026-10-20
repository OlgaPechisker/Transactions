from decimal import Decimal

import pytest

from bank_transactions.domain.money import EUR, ILS, USD, Money, effective_fx_rate
from bank_transactions.parsing.shared.fx_calculator import (
    EffectiveRateCalculator,
    currencies_differ,
)

calculator = EffectiveRateCalculator()


@pytest.mark.parametrize(
    ("billing", "original", "expected"),
    [
        (ILS, EUR, True),
        (ILS, ILS, False),
        (ILS, None, False),
        (None, EUR, False),
        (None, None, False),
    ],
)
def test_only_two_known_and_different_currencies_count_as_foreign(billing, original, expected):
    assert currencies_differ(billing, original) is expected


def test_effective_rate_is_billing_over_original_at_full_precision():
    detail = calculator.detail(
        billing=Money(Decimal("23.54"), ILS),
        original=Money(Decimal("7.05"), EUR),
        commission=Decimal(0),
        stated_rate=Decimal("3.29"),
    )

    assert detail.effective_fx_rate == effective_fx_rate(Decimal("23.54"), Decimal("7.05"))
    assert len(str(detail.effective_fx_rate)) > len("3.339007")
    assert detail.stated_fx_rate == Decimal("3.29")
    assert detail.commission_amount == Decimal(0)


def test_same_currency_foreign_rows_have_a_rate_of_exactly_one():
    detail = calculator.detail(
        billing=Money(Decimal("32.90"), ILS),
        original=Money(Decimal("32.90"), ILS),
        commission=Decimal(0),
        stated_rate=None,
    )

    assert detail.effective_fx_rate == Decimal(1)
    assert detail.stated_fx_rate is None


def test_a_refund_with_a_positive_original_keeps_the_negative_signed_ratio():
    detail = calculator.detail(
        billing=Money(Decimal("-2092.21"), ILS),
        original=Money(Decimal("616.03"), USD),
        commission=Decimal(0),
        stated_rate=Decimal("3.448"),
    )

    assert detail.effective_fx_rate < 0
    assert detail.effective_fx_rate == effective_fx_rate(Decimal("-2092.21"), Decimal("616.03"))


def test_a_folded_commission_is_reported_and_included_in_the_rate_through_billing():
    detail = calculator.detail(
        billing=Money(Decimal("102.50"), ILS),
        original=Money(Decimal("30"), USD),
        commission=Decimal("2.50"),
        stated_rate=None,
    )

    assert detail.commission_amount == Decimal("2.50")
    assert detail.effective_fx_rate == effective_fx_rate(Decimal("102.50"), Decimal("30"))


def test_an_unknown_original_currency_still_yields_the_numeric_rate():
    detail = calculator.detail(
        billing=Money(Decimal("12.08"), ILS),
        original=Money(Decimal("660"), None),
        commission=Decimal(0),
        stated_rate=Decimal("0.0183"),
    )

    assert detail.effective_fx_rate == effective_fx_rate(Decimal("12.08"), Decimal("660"))


def test_the_rate_does_not_depend_on_the_ambient_decimal_context():
    import decimal

    with decimal.localcontext() as context:
        context.prec = 4
        detail = calculator.detail(
            billing=Money(Decimal("23.54"), ILS),
            original=Money(Decimal("7.05"), EUR),
            commission=Decimal(0),
            stated_rate=None,
        )

    assert detail.effective_fx_rate == effective_fx_rate(Decimal("23.54"), Decimal("7.05"))


@pytest.mark.parametrize("original", [Money(Decimal(0), USD), None])
def test_a_zero_or_missing_original_is_a_caller_error(original):
    with pytest.raises(ValueError, match="original"):
        calculator.detail(
            billing=Money(Decimal("5"), ILS),
            original=original,
            commission=Decimal(0),
            stated_rate=None,
        )


def test_an_unknown_billing_currency_is_a_caller_error():
    with pytest.raises(ValueError, match="billing"):
        calculator.detail(
            billing=Money(Decimal("5"), None),
            original=Money(Decimal("2"), USD),
            commission=Decimal(0),
            stated_rate=None,
        )
