from datetime import date
from decimal import Decimal

import pytest

from bank_transactions.domain.ids import CardId
from bank_transactions.domain.keys import ComparisonKey, IssuerReferenceKey, OwnerMatchKey
from bank_transactions.domain.money import ILS, USD

CARD = CardId(7)
DAY = date(2026, 2, 9)


def comparison(**overrides):
    values = {
        "card_id": CARD,
        "transaction_date": DAY,
        "merchant_name_norm": "cafe",
        "original_amount": Decimal("12.5"),
        "original_currency": ILS,
        "installment_number": None,
    }
    return ComparisonKey(**{**values, **overrides})


def test_amount_scale_does_not_change_the_comparison_key():
    assert comparison(original_amount=Decimal("12.5")) == comparison(
        original_amount=Decimal("12.50")
    )
    assert hash(comparison(original_amount=Decimal("12.5"))) == hash(
        comparison(original_amount=Decimal("12.50"))
    )


def test_amounts_are_compared_at_persisted_precision():
    assert comparison(original_amount=Decimal("12.345")) == comparison(
        original_amount=Decimal("12.35")
    )


def test_missing_amount_only_equals_missing_amount():
    assert comparison(original_amount=None) == comparison(original_amount=None)
    assert comparison(original_amount=None) != comparison(original_amount=Decimal("0"))


def test_missing_currency_only_equals_missing_currency():
    assert comparison(original_currency=None) == comparison(original_currency=None)
    assert comparison(original_currency=None) != comparison(original_currency=USD)


def test_installment_number_is_part_of_the_comparison_key():
    assert comparison(installment_number=4) != comparison(installment_number=5)
    assert comparison(installment_number=None) != comparison(installment_number=1)


def test_reference_key_includes_installment_number():
    first = IssuerReferenceKey(CARD, DAY, "V123", installment_number=1)
    second = IssuerReferenceKey(CARD, DAY, "V123", installment_number=2)

    assert first != second


def test_reference_key_without_installment_number_has_one_identity():
    assert IssuerReferenceKey(CARD, DAY, "V1", None) == IssuerReferenceKey(CARD, DAY, "V1", None)
    assert IssuerReferenceKey(CARD, DAY, "V1", None) != IssuerReferenceKey(CARD, DAY, "V1", 1)


def test_reference_key_is_a_usable_set_member():
    keys = {IssuerReferenceKey(CARD, DAY, "V1", None), IssuerReferenceKey(CARD, DAY, "V1", None)}

    assert len(keys) == 1


def test_blank_reference_is_rejected():
    with pytest.raises(ValueError):
        IssuerReferenceKey(CARD, DAY, "  ", None)


def test_owner_match_key_folds_the_display_name():
    assert OwnerMatchKey.from_display_name("  ישראל   ישראלי ") == OwnerMatchKey.from_display_name(
        "ישראל ישראלי"
    )
    assert OwnerMatchKey.from_display_name("DANA Levi") == OwnerMatchKey.from_display_name(
        "dana levi"
    )


def test_blank_owner_name_has_no_match_key():
    with pytest.raises(ValueError):
        OwnerMatchKey.from_display_name("   ")
