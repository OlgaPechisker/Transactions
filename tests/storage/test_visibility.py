"""What each user can see: scoping, the visible review predicate, effective category and trips."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from bank_transactions.domain.enums import CategorySource, ReviewReason, TripMembership
from bank_transactions.domain.ids import TransactionId, UserId
from bank_transactions.domain.money import USD
from bank_transactions.persistence.commands import NewForeignDetail
from bank_transactions.persistence.records import TransactionViewRecord
from bank_transactions.persistence.visibility import (
    to_view_record,
    transaction_view_select,
    visible_transactions,
)

pytestmark = pytest.mark.postgres

FOREIGN = {
    "original_amount": Decimal("10.00"),
    "original_currency": USD,
    "aggregate_foreign_detail": NewForeignDetail(Decimal("0.50"), Decimal("3.5"), Decimal("3.65")),
}
UNKNOWN_FOREIGN = {
    "original_amount": Decimal("10.00"),
    "original_currency": None,
    "aggregate_review_reasons": {ReviewReason.UNKNOWN_CURRENCY},
    "aggregate_foreign_detail": NewForeignDetail(Decimal("0.50"), None, Decimal("3.65")),
}


@pytest.fixture
def views(engine: Engine) -> Callable[[Any], dict[TransactionId, TransactionViewRecord]]:
    def read(user: Any) -> dict[TransactionId, TransactionViewRecord]:
        with Session(engine) as session:
            rows = session.execute(transaction_view_select(UserId(user.user_id))).all()
        records = [to_view_record(row) for row in rows]
        return {record.transaction_id: record for record in records}

    return read


@pytest.fixture
def make_category(sql: Callable[..., list[Any]]) -> Callable[[Any, str], int]:
    def make(user: Any, name: str) -> int:
        (row,) = sql(
            "INSERT INTO custom_categories (user_id, name, name_key) VALUES (:u, :n, lower(:n))"
            " RETURNING id",
            u=user.user_id,
            n=name,
        )
        return int(row[0])

    return make


@pytest.fixture
def make_rule(sql: Callable[..., list[Any]]) -> Callable[..., int]:
    def make(user: Any, category_id: int, *terms: str) -> int:
        (row,) = sql(
            "INSERT INTO category_rules (public_id, user_id, custom_category_id)"
            " VALUES ('rul_' || md5(random()::text), :u, :c) RETURNING id",
            u=user.user_id,
            c=category_id,
        )
        for term in terms:
            sql(
                "INSERT INTO category_rule_terms (rule_id, term_norm) VALUES (:r, :t)",
                r=row[0],
                t=term,
            )
        return int(row[0])

    return make


@pytest.fixture
def make_trip(sql: Callable[..., list[Any]]) -> Callable[..., int]:
    def make(user: Any, name: str, start: date, end: date, currency: str = USD.code) -> int:
        (row,) = sql(
            "INSERT INTO trips (public_id, user_id, name, currency, start_date, end_date)"
            " VALUES ('trp_' || md5(random()::text), :u, :n, :c, :s, :e) RETURNING id",
            u=user.user_id,
            n=name,
            c=currency,
            s=start,
            e=end,
        )
        return int(row[0])

    return make


def test_a_user_sees_only_the_transactions_of_the_cards_they_claimed(
    seed_claimed_card, seed_user, seed_transaction, views, engine
):
    ann, ben = seed_user("ann@example.com", "Ann"), seed_user("ben@example.com", "Ben")
    ann_card = seed_claimed_card(user=ann, last4="1111", owner_name="Ann Levi")
    ben_card = seed_claimed_card(user=ben, last4="2222", owner_name="Ben Katz")
    mine = seed_transaction(ann_card)
    theirs = seed_transaction(ben_card, merchant_name_raw="Ben Only")

    assert set(views(ann)) == {mine}
    assert set(views(ben)) == {theirs}
    with Session(engine) as session:
        visible = session.execute(visible_transactions(UserId(ann.user_id))).all()
    assert [row.id for row in visible] == [mine]


def test_the_review_counter_counts_only_visible_pending_currencies_of_the_user(
    seed_claimed_card, seed_user, seed_transaction, factory, sql
):
    ann, ben = seed_user("ann@example.com", "Ann"), seed_user("ben@example.com", "Ben")
    ann_card = seed_claimed_card(user=ann, last4="1111", owner_name="Ann Levi")
    ben_card = seed_claimed_card(user=ben, last4="2222", owner_name="Ben Katz")
    seed_transaction(ann_card, **UNKNOWN_FOREIGN)
    seed_transaction(ann_card, **UNKNOWN_FOREIGN)
    seed_transaction(ben_card, **UNKNOWN_FOREIGN)
    seed_transaction(ann_card, **FOREIGN)
    seed_transaction(ann_card, aggregate_review_reasons={ReviewReason.UNKNOWN_CURRENCY})
    sql(
        "UPDATE transactions SET original_currency = 'EUR' WHERE id = :t",
        t=seed_transaction(ann_card, **UNKNOWN_FOREIGN),
    )

    with factory.create() as uow:
        counts = (
            uow.review_counter.count_visible_unknown_currency(ann.user_id),
            uow.review_counter.count_visible_unknown_currency(ben.user_id),
        )

    assert counts == (2, 1)


def test_a_domestic_transaction_never_shows_an_unknown_currency_review(
    seed_card, seed_transaction, seed_user, views
):
    card = seed_card()
    domestic = seed_transaction(card, aggregate_review_reasons={ReviewReason.UNKNOWN_CURRENCY})
    pending = seed_transaction(card, **UNKNOWN_FOREIGN)

    seen = views(seed_user())

    assert seen[domestic].visible_review_reasons == ()
    assert seen[domestic].original_currency is None
    assert seen[pending].visible_review_reasons == (ReviewReason.UNKNOWN_CURRENCY,)


def test_internal_review_reasons_are_never_exposed(seed_card, seed_transaction, seed_user, views):
    card = seed_card()
    internal_only = seed_transaction(
        card,
        aggregate_review_reasons={
            ReviewReason.UNMATCHED_FOREIGN_FEE,
            ReviewReason.AMBIGUOUS_INSTALLMENT,
        },
    )
    mixed = seed_transaction(
        card,
        **{
            **UNKNOWN_FOREIGN,
            "aggregate_review_reasons": {
                ReviewReason.UNKNOWN_CURRENCY,
                ReviewReason.UNMATCHED_FOREIGN_FEE,
            },
        },
    )

    seen = views(seed_user())

    assert seen[internal_only].visible_review_reasons == ()
    assert seen[mixed].visible_review_reasons == (ReviewReason.UNKNOWN_CURRENCY,)


def test_a_foreign_transaction_reports_its_original_side_rates_and_card(
    seed_card, seed_transaction, seed_user, views
):
    card = seed_card(last4="4396")
    foreign = seed_transaction(card, **FOREIGN, bank_category_raw="Travel", merchant_city="Rome")

    record = views(seed_user())[foreign]

    assert (record.original_amount, record.original_currency) == (Decimal("10.00"), "USD")
    assert (record.billing_amount, record.billing_currency) == (Decimal("120.00"), "ILS")
    assert (record.effective_rate, record.commission) == (Decimal("3.65"), Decimal("0.50"))
    assert record.stated_fx_rate == Decimal("3.5")
    assert (record.bank_category, record.effective_category) == ("Travel", "Travel")
    assert record.category_source is CategorySource.BANK
    assert record.city == "Rome"
    assert record.card.public_id.startswith("crd_")
    assert record.card.owner_display_name == "Dana Levi"
    assert record.trip is None


def test_the_effective_category_prefers_override_then_rule_then_mapping_then_bank(
    seed_card, seed_transaction, seed_user, views, make_category, make_rule, sql
):
    user = seed_user()
    card = seed_card()
    groceries = make_category(user, "Groceries")
    health = make_category(user, "Health")
    fun = make_category(user, "Fun")
    sql(
        "INSERT INTO category_mappings (user_id, bank_category_raw, custom_category_id)"
        " VALUES (:u, 'Pharmacy', :c)",
        u=user.user_id,
        c=groceries,
    )
    make_rule(user, health, "pharm")
    plain = seed_transaction(card, merchant_name_norm="kiosk", bank_category_raw="Misc")
    mapped = seed_transaction(card, merchant_name_norm="kiosk", bank_category_raw="Pharmacy")
    ruled = seed_transaction(card, merchant_name_norm="super pharm", bank_category_raw="Pharmacy")
    overridden = seed_transaction(card, merchant_name_norm="super pharm")
    sql(
        "INSERT INTO transaction_category_overrides (user_id, transaction_id, custom_category_id)"
        " VALUES (:u, :t, :c)",
        u=user.user_id,
        t=overridden,
        c=fun,
    )

    seen = views(user)

    assert [
        (seen[t].effective_category, seen[t].category_source, seen[t].custom_category)
        for t in (plain, mapped, ruled, overridden)
    ] == [
        ("Misc", CategorySource.BANK, None),
        ("Groceries", CategorySource.MAPPING, "Groceries"),
        ("Health", CategorySource.RULE, "Health"),
        ("Fun", CategorySource.OVERRIDE, "Fun"),
    ]


def test_the_newest_matching_rule_wins_and_other_users_rules_do_not_apply(
    seed_card, seed_transaction, seed_user, views, make_category, make_rule
):
    user, stranger = seed_user(), seed_user("stranger@example.com", "Stranger")
    card = seed_card()
    old, new = make_category(user, "Old"), make_category(user, "New")
    foreign_category = make_category(stranger, "Theirs")
    make_rule(user, old, "pharm")
    make_rule(user, new, "super")
    make_rule(stranger, foreign_category, "pharm")
    transaction = seed_transaction(card, merchant_name_norm="super pharm")

    record = views(user)[transaction]

    assert (record.effective_category, record.category_source) == ("New", CategorySource.RULE)


def test_a_tag_wins_over_automatic_membership_and_a_transaction_has_one_trip(
    seed_card, seed_transaction, seed_user, views, make_trip, sql
):
    user = seed_user()
    card = seed_card()
    make_trip(user, "Rome", date(2026, 7, 1), date(2026, 7, 10))
    second = make_trip(user, "Rome again", date(2026, 7, 1), date(2026, 7, 31))
    inside = seed_transaction(card, **FOREIGN, transaction_date=date(2026, 7, 5))
    tagged = seed_transaction(card, **FOREIGN, transaction_date=date(2026, 7, 5))
    outside = seed_transaction(card, **FOREIGN, transaction_date=date(2026, 9, 5))
    domestic = seed_transaction(card, transaction_date=date(2026, 7, 5))
    sql(
        "INSERT INTO trip_transaction_tags (transaction_id, trip_id) VALUES (:t, :r)",
        t=tagged,
        r=second,
    )

    seen = views(user)

    assert len(seen) == 4
    assert (seen[inside].trip.name, seen[inside].trip.membership) == (
        "Rome",
        TripMembership.AUTO,
    )
    assert (seen[tagged].trip.name, seen[tagged].trip.membership) == (
        "Rome again",
        TripMembership.TAGGED,
    )
    assert seen[outside].trip is None
    assert seen[domestic].trip is None


def test_an_excluded_transaction_leaves_the_trip_and_pending_currencies_do_not_join_one(
    seed_card, seed_transaction, seed_user, views, make_trip, sql
):
    user = seed_user()
    card = seed_card()
    trip = make_trip(user, "Rome", date(2026, 7, 1), date(2026, 7, 10))
    excluded = seed_transaction(card, **FOREIGN, transaction_date=date(2026, 7, 5))
    pending = seed_transaction(card, **UNKNOWN_FOREIGN, transaction_date=date(2026, 7, 5))
    sql(
        "INSERT INTO trip_transaction_exclusions (trip_id, transaction_id) VALUES (:r, :t)",
        r=trip,
        t=excluded,
    )

    seen = views(user)

    assert seen[excluded].trip is None
    assert seen[pending].trip is None


def test_trips_of_another_user_never_attach_to_my_transactions(
    seed_card, seed_transaction, seed_user, views, make_trip
):
    user, stranger = seed_user(), seed_user("stranger@example.com", "Stranger")
    card = seed_card()
    make_trip(stranger, "Their trip", date(2026, 7, 1), date(2026, 7, 10))
    transaction = seed_transaction(card, **FOREIGN, transaction_date=date(2026, 7, 5))

    assert views(user)[transaction].trip is None
