from decimal import Decimal

from bank_transactions.domain.models import StatementTotals
from bank_transactions.domain.money import EUR, ILS, USD, Money
from bank_transactions.services.totals_check import compare_totals


def totals(*amounts):
    return StatementTotals.of(*(Money(Decimal(value), currency) for value, currency in amounts))


def test_matching_totals_are_available_and_agree():
    comparison = compare_totals(
        totals(("1337.60", ILS), ("26.39", USD)), totals(("1337.60", ILS), ("26.39", USD))
    )

    assert comparison.available
    assert comparison.mismatched == ()


def test_totals_are_compared_at_two_decimal_places():
    comparison = compare_totals(totals(("10.00", ILS)), totals(("10.004", ILS)))

    assert comparison.mismatched == ()


def test_a_differing_currency_total_is_reported_by_currency():
    comparison = compare_totals(
        totals(("100.00", ILS), ("26.39", USD)), totals(("100.00", ILS), ("26.40", USD))
    )

    assert comparison.mismatched == (USD,)


def test_a_currency_missing_on_one_side_counts_as_zero():
    comparison = compare_totals(totals(("100.00", ILS)), totals(("100.00", ILS), ("5.00", EUR)))

    assert comparison.mismatched == (EUR,)


def test_a_zero_total_equals_a_missing_total():
    comparison = compare_totals(totals(("100.00", ILS), ("0.00", EUR)), totals(("100.00", ILS)))

    assert comparison.mismatched == ()


def test_the_unknown_currency_bucket_is_compared_like_any_other():
    comparison = compare_totals(totals(("100.00", None)), totals(("90.00", None)))

    assert comparison.mismatched == (None,)


def test_empty_declared_totals_mean_the_check_is_unavailable_not_a_mismatch():
    comparison = compare_totals(StatementTotals(), totals(("100.00", ILS)))

    assert not comparison.available
    assert comparison.mismatched == ()
