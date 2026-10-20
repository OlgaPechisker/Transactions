from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import ReviewReason
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.models import FeeCandidate
from bank_transactions.domain.money import EUR, ILS, USD, Money
from bank_transactions.parsing.shared.draft import FeeMarker
from bank_transactions.parsing.shared.fee_folder import (
    DEFAULT_FEE_KEYWORDS,
    DEFAULT_FEE_WINDOW_DAYS,
    ForeignFeeFolder,
    ForeignFeeMatcher,
)


def foreign(make_draft, row, *, billing="100.00", days=0, **overrides):
    return make_draft(
        row=row,
        billing=Money(Decimal(billing), ILS),
        original=Money(Decimal("30"), USD),
        is_foreign=True,
        transaction_date=date(2026, 6, 1) + timedelta(days=days),
        **overrides,
    )


def fee(make_draft, row, *, amount="2.50", days=0, **overrides):
    draft = make_draft(
        row=row,
        billing=Money(Decimal(amount), overrides.pop("currency", ILS)),
        merchant_name_raw='עמלת מט"ח',
        transaction_date=date(2026, 6, 1) + timedelta(days=days),
        **overrides,
    )
    return FeeMarker.of(draft)


def test_the_defaults_are_the_documented_three_days_and_hebrew_fee_wording():
    assert DEFAULT_FEE_WINDOW_DAYS == 3
    assert 'עמלת מט"ח' in DEFAULT_FEE_KEYWORDS


def test_a_fee_is_folded_into_its_parent_and_not_kept_as_a_transaction(make_draft):
    parent = foreign(make_draft, 5)

    folded = ForeignFeeFolder().fold([parent], [fee(make_draft, 6)])

    assert len(folded) == 1
    assert folded[0].billing == Money(Decimal("102.50"), ILS)
    assert folded[0].commission == Decimal("2.50")
    assert folded[0].location == parent.location
    assert folded[0].original == parent.original


def test_the_nearest_parent_in_time_takes_the_fee(make_draft):
    far = foreign(make_draft, 5, days=-3)
    near = foreign(make_draft, 6, days=-1)

    folded = ForeignFeeFolder().fold([far, near], [fee(make_draft, 7)])

    by_row = {draft.location.row: draft for draft in folded}
    assert by_row[5].commission == Decimal(0)
    assert by_row[6].commission == Decimal("2.50")


def test_equal_distance_prefers_the_same_charge_date_then_the_closer_row(make_draft):
    other_charge = foreign(make_draft, 5, days=-1, charge_date=date(2026, 8, 2))
    same_charge = foreign(make_draft, 6, days=-1)

    folded = ForeignFeeFolder().fold([other_charge, same_charge], [fee(make_draft, 20)])

    assert {draft.location.row: draft.commission for draft in folded} == {
        5: Decimal(0),
        6: Decimal("2.50"),
    }


def test_a_parent_absorbs_at_most_one_fee_the_other_becomes_an_ordinary_transaction(make_draft):
    parent = foreign(make_draft, 5)

    folded = ForeignFeeFolder().fold(
        [parent], [fee(make_draft, 6, amount="1.00"), fee(make_draft, 7, amount="2.00")]
    )

    assert [draft.location.row for draft in folded] == [5, 7]
    assert folded[0].commission == Decimal("1.00")
    leftover = folded[1]
    assert leftover.review_reasons == {ReviewReason.UNMATCHED_FOREIGN_FEE}
    assert leftover.billing == Money(Decimal("2.00"), ILS)
    assert not leftover.is_foreign


def test_each_of_two_fees_finds_its_own_parent(make_draft):
    first = foreign(make_draft, 5, days=0)
    second = foreign(make_draft, 6, days=0)

    folded = ForeignFeeFolder().fold(
        [first, second], [fee(make_draft, 7, amount="1.00"), fee(make_draft, 8, amount="2.00")]
    )

    assert sorted(draft.commission for draft in folded) == [Decimal("1.00"), Decimal("2.00")]


@pytest.mark.parametrize(
    "make_parent",
    [
        lambda make_draft, other: foreign(make_draft, 5, card_key=other),
        lambda make_draft, other: foreign(make_draft, 5, days=4),
        lambda make_draft, other: foreign(make_draft, 5, days=-4),
        lambda make_draft, other: make_draft(row=5),
        lambda make_draft, other: replace(
            foreign(make_draft, 5), billing=Money(Decimal("100"), EUR)
        ),
    ],
    ids=["other card", "after window", "before window", "not foreign", "other billing currency"],
)
def test_incompatible_parents_never_take_the_fee(make_draft, other_card_key, make_parent):
    parent = make_parent(make_draft, other_card_key)

    folded = ForeignFeeFolder().fold([parent], [fee(make_draft, 6)])

    assert len(folded) == 2
    assert folded[0].commission == Decimal(0)
    assert folded[1].review_reasons == {ReviewReason.UNMATCHED_FOREIGN_FEE}


def test_the_window_edge_is_inclusive_and_configurable(make_draft):
    parent = foreign(make_draft, 5, days=3)

    assert len(ForeignFeeFolder(date_window_days=3).fold([parent], [fee(make_draft, 6)])) == 1
    assert len(ForeignFeeFolder(date_window_days=2).fold([parent], [fee(make_draft, 6)])) == 2
    assert len(ForeignFeeFolder(date_window_days=0).fold([parent], [fee(make_draft, 6)])) == 2


def test_a_negative_window_is_rejected():
    with pytest.raises(ValueError, match="window"):
        ForeignFeeFolder(date_window_days=-1)


def test_a_fee_in_an_unknown_currency_is_kept_as_an_unmatched_ordinary_transaction(make_draft):
    parent = foreign(make_draft, 5)
    marker = fee(make_draft, 6, currency=None)

    folded = ForeignFeeFolder().fold([parent], [marker])

    assert folded[1].review_reasons >= {ReviewReason.UNMATCHED_FOREIGN_FEE}


def test_folding_never_changes_the_per_currency_sum_of_the_statement(make_draft):
    drafts = [foreign(make_draft, 5), foreign(make_draft, 6, billing="40.00", days=1)]
    fees = [fee(make_draft, 7, amount="1.50"), fee(make_draft, 8, amount="0.75", days=1)]
    before = sum(d.billing.amount for d in drafts) + sum(f.fallback.billing.amount for f in fees)

    folded = ForeignFeeFolder().fold(drafts, fees)

    assert sum(draft.billing.amount for draft in folded) == before


def test_output_is_in_source_order_across_sheets(make_draft):
    first = foreign(make_draft, 9, sheet=1)
    second = foreign(make_draft, 2, sheet=2)

    folded = ForeignFeeFolder().fold([second, first], [])

    assert [draft.location for draft in folded] == [
        SourceLocation(1, 9),
        SourceLocation(2, 2),
    ]


def test_the_candidate_view_of_a_fee_row_carries_its_card_dates_and_amount(make_draft):
    marker = fee(make_draft, 6, amount="2.50")

    assert marker.candidate == FeeCandidate(
        card_key=marker.fallback.card_key,
        location=SourceLocation(1, 6),
        transaction_date=date(2026, 6, 1),
        charge_date=date(2026, 7, 2),
        amount=Money(Decimal("2.50"), ILS),
    )


@pytest.mark.parametrize(
    "merchant", ['עמלת מט"ח', "עמלת מט״ח", '  עמלת  מט"ח  ', 'עמלת מט"ח - ויזה']
)
def test_default_matcher_recognises_hebrew_fee_wording_despite_quote_and_space_variants(merchant):
    assert ForeignFeeMatcher(DEFAULT_FEE_KEYWORDS).is_fee(merchant)


def test_ordinary_merchants_are_not_fees():
    matcher = ForeignFeeMatcher(DEFAULT_FEE_KEYWORDS)

    assert not matcher.is_fee("NETFLIX.COM")
    assert not matcher.is_fee("סופרפארם")


def test_custom_keywords_replace_the_defaults_and_ignore_case():
    matcher = ForeignFeeMatcher(["FX Fee"])

    assert matcher.is_fee("visa fx fee 3.1%")
    assert not matcher.is_fee('עמלת מט"ח')


def test_blank_keywords_are_rejected_because_they_would_match_everything():
    with pytest.raises(ValueError, match="keyword"):
        ForeignFeeMatcher(["  "])
