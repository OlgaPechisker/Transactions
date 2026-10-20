from decimal import Decimal

import pytest

from bank_transactions.domain.enums import ReviewReason
from bank_transactions.domain.money import ILS, USD, Money
from bank_transactions.parsing.shared.installment_fields import read_installment_fields


def ils(amount):
    return Money(Decimal(amount), ILS)


def test_the_monthly_charge_is_the_original_and_the_purchase_total_the_plan_total():
    fields = read_installment_fields("תשלום 4 מתוך 5", ils("1680"), ils("336"))

    assert fields.original == ils("336")
    assert fields.ref.installment_number == 4
    assert fields.ref.total_installments == 5
    assert fields.ref.plan_total == ils("1680")
    assert fields.review_reasons == frozenset()


def test_the_note_may_sit_inside_longer_text():
    fields = read_installment_fields('הוראה: תשלום 1 מתוך 12 (חו"ל)', ils("1200"), ils("100"))

    assert (fields.ref.installment_number, fields.ref.total_installments) == (1, 12)


@pytest.mark.parametrize("note", [None, "", "ללא פירוט", "תשלום 6 מתוך 5", "תשלום 0 מתוך 5"])
def test_an_unusable_note_leaves_the_charge_ambiguous_and_the_original_the_monthly_charge(note):
    fields = read_installment_fields(note, ils("1680"), ils("336"))

    assert fields.ref is None
    assert fields.original == ils("336")
    assert fields.review_reasons == {ReviewReason.AMBIGUOUS_INSTALLMENT}


def test_an_unusable_note_without_a_source_amount_has_no_original():
    fields = read_installment_fields("ללא פירוט", None, ils("336"))

    assert fields.ref is None
    assert fields.original is None
    assert fields.review_reasons == {ReviewReason.AMBIGUOUS_INSTALLMENT}


def test_an_unusable_note_in_another_currency_does_not_guess_the_original():
    fields = read_installment_fields("ללא פירוט", Money(Decimal("90"), USD), ils("100"))

    assert fields.ref is None
    assert fields.original is None
    assert fields.review_reasons == {ReviewReason.AMBIGUOUS_INSTALLMENT}


def test_an_unusable_note_with_an_unknown_source_currency_keeps_it_unknown():
    fields = read_installment_fields("ללא פירוט", Money(Decimal("90"), None), ils("30"))

    assert fields.original == Money(Decimal("30"), None)


def test_the_foreign_currency_plan_keeps_the_currency_of_the_source_amount():
    fields = read_installment_fields("תשלום 1 מתוך 3", Money(Decimal("90"), USD), ils("100"))

    assert fields.original is None
    assert fields.ref.plan_total == Money(Decimal("90"), USD)
    assert fields.review_reasons == {ReviewReason.AMBIGUOUS_INSTALLMENT}


def test_an_unknown_plan_currency_stays_unknown_instead_of_guessed():
    fields = read_installment_fields("תשלום 1 מתוך 3", Money(Decimal("90"), None), ils("30"))

    assert fields.original == Money(Decimal("30"), None)
    assert fields.ref.plan_total == Money(Decimal("90"), None)


def test_a_missing_source_amount_gives_a_reference_without_a_total():
    fields = read_installment_fields("תשלום 2 מתוך 3", None, ils("30"))

    assert fields.original is None
    assert fields.ref.plan_total is None
