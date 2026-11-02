"""The transaction read side on PostgreSQL: filters, sorting, grouping, summaries, filter options,
point lookups and row locks, always scoped to the claimed cards of one user."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from bank_transactions.domain.enums import (
    CategorySource,
    IssuerCode,
    ReviewReason,
    TransactionType,
)
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.money import EUR, USD
from bank_transactions.domain.queries import (
    Dimension,
    PageRequest,
    PendingValidationFilter,
    SortSpec,
    TransactionFilter,
)
from bank_transactions.persistence.commands import NewForeignDetail
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    BillingTotalRecord,
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
EVERYTHING = PageRequest(1, 200)
NEWEST_FIRST = SortSpec((("transactionDate", True),))


@dataclass
class Dataset:
    user: Any
    pharm_card: Any
    travel_card: Any


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
def make_trip(sql: Callable[..., list[Any]]) -> Callable[..., int]:
    def make(user: Any, name: str, start: date, end: date, currency: str = "USD") -> int:
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


@pytest.fixture
def run(factory):
    """Run `action(repository, user_id)` on the transaction query repository in a fresh UoW."""

    def execute(user, action):
        with factory.create() as uow:
            return action(uow.transaction_query, user.user_id)

    return execute


@pytest.fixture
def shop(seed_user, seed_claimed_card, seed_transaction) -> Dataset:
    """Six transactions of one user on two cards: ILS and USD billing, a domestic, a foreign, a
    pending-currency and a refund row, and one without a billing currency."""
    user = seed_user()
    card = seed_claimed_card(user=user, last4="1111", owner_name="Dana Levi")
    travel = seed_claimed_card(
        user=user,
        last4="2222",
        issuer=IssuerCode.ISRACARD,
        owner_name="Dana Levi",
        bank_code="OTHER",
    )
    seed_transaction(
        card,
        merchant_name_raw="Super Pharm",
        transaction_date=date(2026, 7, 3),
        billing_amount=Decimal("100.00"),
        bank_category_raw="Pharmacy",
        merchant_country="IL",
        merchant_city="Tel Aviv",
    )
    seed_transaction(
        card,
        **FOREIGN,
        merchant_name_raw="Rome Cafe",
        transaction_date=date(2026, 7, 5),
        billing_amount=Decimal("36.50"),
        bank_category_raw="Restaurants",
        merchant_country="IT",
    )
    seed_transaction(
        card,
        merchant_name_raw="Refund Shop",
        transaction_date=date(2026, 7, 20),
        billing_amount=Decimal("-30.00"),
        bank_category_raw="Pharmacy",
        merchant_country="IL",
    )
    seed_transaction(
        card,
        **UNKNOWN_FOREIGN,
        merchant_name_raw="Mystery Shop",
        transaction_date=date(2026, 8, 1),
        billing_amount=Decimal("50.00"),
        merchant_country="FR",
    )
    seed_transaction(
        travel,
        **{**FOREIGN, "original_currency": EUR},
        merchant_name_raw="Amazon",
        transaction_date=date(2026, 8, 10),
        billing_amount=Decimal("20.00"),
        billing_currency=USD,
        bank_category_raw="Shopping",
        transaction_type=TransactionType.RECURRING,
    )
    seed_transaction(
        travel,
        merchant_name_raw="No Currency Co",
        transaction_date=date(2026, 8, 20),
        billing_amount=Decimal("15.00"),
        billing_currency=None,
        transaction_type=TransactionType.IMMEDIATE_CHARGE,
    )
    return Dataset(user, card, travel)


def merchants(records: Any) -> list[str]:
    items = records.items if hasattr(records, "items") else records
    return [record.merchant for record in items]


def listed(run, user, filter: TransactionFilter | None = None, sort: SortSpec = NEWEST_FIRST):
    return run(
        user,
        lambda repository, user_id: repository.page(
            user_id, filter or TransactionFilter(), sort, EVERYTHING
        ),
    )


def test_an_unfiltered_page_lists_the_users_transactions_newest_first(run, shop):
    page = listed(run, shop.user)

    assert merchants(page) == [
        "No Currency Co",
        "Amazon",
        "Mystery Shop",
        "Refund Shop",
        "Rome Cafe",
        "Super Pharm",
    ]
    assert (page.page, page.size, page.total) == (1, 200, 6)


def test_a_page_reports_the_total_of_the_filtered_set_and_is_empty_past_the_end(run, shop):
    filter = TransactionFilter(bank_categories=("Pharmacy",))

    second = run(
        shop.user,
        lambda repository, user_id: repository.page(
            user_id, filter, SortSpec((("merchant", False),)), PageRequest(2, 1)
        ),
    )
    beyond = run(
        shop.user,
        lambda repository, user_id: repository.page(
            user_id, filter, NEWEST_FIRST, PageRequest(3, 1)
        ),
    )

    assert merchants(second) == ["Super Pharm"]
    assert (second.total, second.total_pages) == (2, 2)
    assert (beyond.items, beyond.total) == ((), 2)


def test_a_listed_transaction_carries_the_view_data_of_its_card_and_amounts(run, shop):
    (rome,) = listed(run, shop.user, TransactionFilter(merchants=("rome",))).items

    assert rome.transaction_date == date(2026, 7, 5)
    assert (rome.original_amount, rome.original_currency) == (Decimal("10.00"), "USD")
    assert (rome.billing_amount, rome.billing_currency) == (Decimal("36.50"), "ILS")
    assert (rome.effective_rate, rome.commission) == (Decimal("3.65"), Decimal("0.50"))
    assert (rome.bank_category, rome.category_source) == ("Restaurants", CategorySource.BANK)
    assert rome.card.card_id == shop.pharm_card.card_id
    assert rome.public_id.startswith("txn_")


@pytest.mark.parametrize(
    ("filter", "expected"),
    [
        (
            TransactionFilter(date_from=date(2026, 7, 5), date_to=date(2026, 7, 20)),
            ["Refund Shop", "Rome Cafe"],
        ),
        (TransactionFilter(date_from=date(2026, 8, 10)), ["No Currency Co", "Amazon"]),
        (TransactionFilter(date_to=date(2026, 7, 3)), ["Super Pharm"]),
        (TransactionFilter(categories=("Pharmacy",)), ["Refund Shop", "Super Pharm"]),
        (TransactionFilter(bank_categories=("Shopping", "Restaurants")), ["Amazon", "Rome Cafe"]),
        (TransactionFilter(types=(TransactionType.RECURRING,)), ["Amazon"]),
        (
            TransactionFilter(types=(TransactionType.IMMEDIATE_CHARGE, TransactionType.RECURRING)),
            ["No Currency Co", "Amazon"],
        ),
        (TransactionFilter(countries=("IT", "FR")), ["Mystery Shop", "Rome Cafe"]),
        (TransactionFilter(merchants=("PHARM", "amaz")), ["Amazon", "Super Pharm"]),
        (TransactionFilter(merchants=("%",)), []),
        (TransactionFilter(merchants=("super_pharm",)), []),
        (TransactionFilter(billing_currency="USD"), ["Amazon"]),
        (
            TransactionFilter(billing_currency="ILS", min_amount=Decimal("36.50")),
            ["Mystery Shop", "Rome Cafe", "Super Pharm"],
        ),
        (
            TransactionFilter(
                billing_currency="ILS", min_amount=Decimal("-30.00"), max_amount=Decimal("36.50")
            ),
            ["Refund Shop", "Rome Cafe"],
        ),
        (TransactionFilter(needs_review=True), ["Mystery Shop"]),
        (
            TransactionFilter(needs_review=False),
            ["No Currency Co", "Amazon", "Refund Shop", "Rome Cafe", "Super Pharm"],
        ),
        (TransactionFilter(review_reasons=(ReviewReason.UNKNOWN_CURRENCY,)), ["Mystery Shop"]),
        (TransactionFilter(review_reasons=(ReviewReason.UNMATCHED_FOREIGN_FEE,)), []),
    ],
)
def test_each_filter_narrows_the_list(run, shop, filter, expected):
    assert sorted(merchants(listed(run, shop.user, filter))) == sorted(expected)


def test_the_currency_filter_matches_original_currencies_and_unknown_means_pending_only(run, shop):
    usd = listed(run, shop.user, TransactionFilter(currencies=("USD",)))
    unknown = listed(run, shop.user, TransactionFilter(currencies=("UNKNOWN",)))
    either = listed(run, shop.user, TransactionFilter(currencies=("EUR", "UNKNOWN")))

    assert merchants(usd) == ["Rome Cafe"]
    assert merchants(unknown) == ["Mystery Shop"]
    assert sorted(merchants(either)) == ["Amazon", "Mystery Shop"]


def test_the_card_filter_takes_card_public_ids(run, shop):
    everything = listed(run, shop.user)
    travel_id = next(r.card.public_id for r in everything.items if r.merchant == "Amazon")

    page = listed(run, shop.user, TransactionFilter(card_ids=(travel_id,)))

    assert merchants(page) == ["No Currency Co", "Amazon"]


def test_custom_category_filters_cover_the_effective_custom_category_and_none(
    run, shop, make_category, sql
):
    health = make_category(shop.user, "Health")
    sql(
        "INSERT INTO category_mappings (user_id, bank_category_raw, custom_category_id)"
        " VALUES (:u, 'Pharmacy', :c)",
        u=shop.user.user_id,
        c=health,
    )

    named = listed(run, shop.user, TransactionFilter(custom_categories=("Health",)))
    none = listed(run, shop.user, TransactionFilter(custom_categories=("none",)))
    by_effective = listed(run, shop.user, TransactionFilter(categories=("Health",)))
    by_source = listed(
        run, shop.user, TransactionFilter(category_sources=(CategorySource.MAPPING,))
    )

    assert sorted(merchants(named)) == ["Refund Shop", "Super Pharm"]
    assert sorted(merchants(by_effective)) == ["Refund Shop", "Super Pharm"]
    assert sorted(merchants(by_source)) == ["Refund Shop", "Super Pharm"]
    assert "Super Pharm" not in merchants(none)
    assert len(none.items) == 4


def test_the_trip_filter_takes_trip_ids_and_none(run, shop, make_trip):
    make_trip(shop.user, "Rome", date(2026, 7, 1), date(2026, 7, 10))
    everything = listed(run, shop.user)
    (rome,) = (r for r in everything.items if r.merchant == "Rome Cafe")
    assert rome.trip is not None

    in_trip = listed(run, shop.user, TransactionFilter(trip_ids=(rome.trip.public_id,)))
    outside = listed(run, shop.user, TransactionFilter(trip_ids=("none",)))
    either = listed(run, shop.user, TransactionFilter(trip_ids=("none", rome.trip.public_id)))

    assert merchants(in_trip) == ["Rome Cafe"]
    assert "Rome Cafe" not in merchants(outside)
    assert len(outside.items) == 5
    assert len(either.items) == 6


def test_combined_filters_all_apply(run, shop):
    filter = TransactionFilter(
        date_from=date(2026, 7, 1),
        date_to=date(2026, 7, 31),
        bank_categories=("Pharmacy",),
        billing_currency="ILS",
        min_amount=Decimal("0"),
    )

    assert merchants(listed(run, shop.user, filter)) == ["Super Pharm"]


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        (
            (("merchant", False),),
            ["Amazon", "Mystery Shop", "No Currency Co", "Refund Shop", "Rome Cafe", "Super Pharm"],
        ),
        (
            (("billingAmount", True),),
            ["Super Pharm", "Mystery Shop", "Rome Cafe", "Amazon", "No Currency Co", "Refund Shop"],
        ),
        (
            (("billingAmount", False),),
            ["Refund Shop", "No Currency Co", "Amazon", "Rome Cafe", "Mystery Shop", "Super Pharm"],
        ),
        (
            (("transactionDate", False),),
            ["Super Pharm", "Rome Cafe", "Refund Shop", "Mystery Shop", "Amazon", "No Currency Co"],
        ),
        (
            (("category", False),),
            ["Super Pharm", "Refund Shop", "Rome Cafe", "Amazon", "Mystery Shop", "No Currency Co"],
        ),
        (
            (("country", False),),
            ["Mystery Shop", "Super Pharm", "Refund Shop", "Rome Cafe", "Amazon", "No Currency Co"],
        ),
        (
            (("country", False), ("merchant", True)),
            ["Mystery Shop", "Super Pharm", "Refund Shop", "Rome Cafe", "No Currency Co", "Amazon"],
        ),
        (
            (("originalCurrency", False),),
            ["Amazon", "Rome Cafe", "Super Pharm", "Refund Shop", "Mystery Shop", "No Currency Co"],
        ),
        (
            (("originalCurrency", True),),
            ["Rome Cafe", "Amazon", "Super Pharm", "Refund Shop", "Mystery Shop", "No Currency Co"],
        ),
        (
            (("type", False),),
            ["No Currency Co", "Amazon", "Super Pharm", "Rome Cafe", "Refund Shop", "Mystery Shop"],
        ),
        (
            (("effectiveRate", True),),
            ["Rome Cafe", "Mystery Shop", "Amazon", "Super Pharm", "Refund Shop", "No Currency Co"],
        ),
        (
            (("reviewCount", True),),
            ["Mystery Shop", "Super Pharm", "Rome Cafe", "Refund Shop", "Amazon", "No Currency Co"],
        ),
        (
            (("card", False),),
            ["Amazon", "No Currency Co", "Super Pharm", "Rome Cafe", "Refund Shop", "Mystery Shop"],
        ),
    ],
)
def test_sorting_follows_the_fields_with_nulls_last_and_the_id_as_tiebreaker(
    run, shop, sort, expected
):
    assert merchants(listed(run, shop.user, sort=SortSpec(sort))) == expected


def test_a_group_sort_orders_the_list_by_group_first(run, shop):
    sort = SortSpec((("group:month", False), ("billingAmount", True)))

    assert merchants(listed(run, shop.user, sort=sort)) == [
        "Super Pharm",
        "Rome Cafe",
        "Refund Shop",
        "Mystery Shop",
        "Amazon",
        "No Currency Co",
    ]


def test_the_summary_has_one_total_per_known_billing_currency_and_counts_the_unknown_ones(
    run, shop
):
    summary = run(
        shop.user, lambda repository, user_id: repository.summary(user_id, TransactionFilter())
    )

    assert summary == BillingSummaryRecord(
        count=6,
        unknown_billing_currency_count=1,
        totals=(
            BillingTotalRecord("ILS", 4, Decimal("156.50")),
            BillingTotalRecord("USD", 1, Decimal("20.00")),
        ),
        unknown_currency_count=1,
    )


def test_the_summary_covers_the_whole_filtered_set_and_matches_the_page_total(run, shop):
    filter = TransactionFilter(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))

    summary = run(shop.user, lambda repository, user_id: repository.summary(user_id, filter))
    page = listed(run, shop.user, filter)

    assert summary.count == page.total == 3
    assert summary.totals == (BillingTotalRecord("ILS", 3, Decimal("106.50")),)
    assert (summary.unknown_billing_currency_count, summary.unknown_currency_count) == (0, 0)


def test_the_summary_of_an_empty_set_has_no_totals(run, shop):
    filter = TransactionFilter(merchants=("nothing like this",))

    summary = run(shop.user, lambda repository, user_id: repository.summary(user_id, filter))

    assert summary == BillingSummaryRecord(0, 0, (), 0)


def grouped(run, user, dimension: Dimension, filter: TransactionFilter | None = None):
    groups = run(
        user,
        lambda repository, user_id: repository.groups(
            user_id, filter or TransactionFilter(), dimension
        ),
    )
    return {group.key: group for group in groups}, [group.key for group in groups]


def test_groups_by_month_split_each_group_by_billing_currency(run, shop):
    groups, keys = grouped(run, shop.user, Dimension.MONTH)

    assert keys == ["2026-07", "2026-08"]
    assert groups["2026-07"].summary == BillingSummaryRecord(
        3, 0, (BillingTotalRecord("ILS", 3, Decimal("106.50")),), 0
    )
    assert groups["2026-08"].summary == BillingSummaryRecord(
        3,
        1,
        (
            BillingTotalRecord("ILS", 1, Decimal("50.00")),
            BillingTotalRecord("USD", 1, Decimal("20.00")),
        ),
        1,
    )


@pytest.mark.parametrize(
    ("dimension", "keys"),
    [
        (Dimension.YEAR, ["2026"]),
        (
            Dimension.DAY,
            ["2026-07-03", "2026-07-05", "2026-07-20", "2026-08-01", "2026-08-10", "2026-08-20"],
        ),
        (Dimension.WEEK, ["2026-06-29", "2026-07-20", "2026-07-27", "2026-08-10", "2026-08-17"]),
        (Dimension.CATEGORY, ["Pharmacy", "Restaurants", "Shopping", None]),
        (Dimension.COUNTRY, ["FR", "IL", "IT", None]),
        (Dimension.CURRENCY, ["EUR", "USD", None]),
        (Dimension.TYPE, ["IMMEDIATE_CHARGE", "RECURRING", "REGULAR"]),
        (Dimension.CUSTOM_CATEGORY, [None]),
        (Dimension.TRIP, [None]),
        (
            Dimension.MERCHANT,
            ["Amazon", "Mystery Shop", "No Currency Co", "Refund Shop", "Rome Cafe", "Super Pharm"],
        ),
    ],
)
def test_groups_are_keyed_by_the_dimension_value_with_a_null_key_last(run, shop, dimension, keys):
    groups, ordered = grouped(run, shop.user, dimension)

    assert ordered == keys
    assert sum(group.summary.count for group in groups.values()) == 6


def test_the_category_group_without_a_value_keeps_its_rows(run, shop):
    groups, _ = grouped(run, shop.user, Dimension.CATEGORY)

    assert groups[None].summary.count == 2
    assert groups["Pharmacy"].summary.totals == (BillingTotalRecord("ILS", 2, Decimal("70.00")),)


def test_groups_by_card_use_card_public_ids(run, shop):
    groups, keys = grouped(run, shop.user, Dimension.CARD)
    cards = {r.card.public_id for r in listed(run, shop.user).items}

    assert set(keys) == cards
    assert sorted(group.summary.count for group in groups.values()) == [2, 4]


def test_groups_by_trip_count_each_transaction_once_and_keep_the_rest_under_a_null_key(
    run, shop, make_trip
):
    make_trip(shop.user, "Rome", date(2026, 7, 1), date(2026, 7, 10))
    make_trip(shop.user, "Rome again", date(2026, 7, 1), date(2026, 7, 31))

    groups, keys = grouped(run, shop.user, Dimension.TRIP)

    assert len(keys) == 2 and keys[-1] is None
    assert groups[None].summary.count == 5
    assert groups[keys[0]].summary.count == 1


def test_groups_follow_the_filter(run, shop):
    groups, keys = grouped(
        run, shop.user, Dimension.MONTH, TransactionFilter(billing_currency="ILS")
    )

    assert keys == ["2026-07", "2026-08"]
    assert groups["2026-08"].summary == BillingSummaryRecord(
        1, 0, (BillingTotalRecord("ILS", 1, Decimal("50.00")),), 1
    )


def test_filter_options_describe_the_whole_dataset_of_the_user(run, shop, make_category, sql):
    health = make_category(shop.user, "Health")
    sql(
        "INSERT INTO category_mappings (user_id, bank_category_raw, custom_category_id)"
        " VALUES (:u, 'Pharmacy', :c)",
        u=shop.user.user_id,
        c=health,
    )

    options = run(shop.user, lambda repository, user_id: repository.filter_options(user_id))

    assert [(o.name, o.count) for o in options.bank_categories] == [
        ("Pharmacy", 2),
        ("Restaurants", 1),
        ("Shopping", 1),
    ]
    assert [(o.name, o.count) for o in options.custom_categories] == [("Health", 2)]
    assert [card.card_id for card in options.cards] == [
        shop.pharm_card.card_id,
        shop.travel_card.card_id,
    ]
    assert [(o.code, o.count) for o in options.original_currencies] == [("EUR", 1), ("USD", 1)]
    assert [(o.code, o.count) for o in options.billing_currencies] == [("ILS", 4), ("USD", 1)]
    assert [(o.code, o.count) for o in options.countries] == [("FR", 1), ("IL", 2), ("IT", 1)]
    assert options.types == (
        TransactionType.REGULAR,
        TransactionType.RECURRING,
        TransactionType.IMMEDIATE_CHARGE,
    )
    assert options.unknown_currency_count == 1
    assert options.unknown_billing_currency_count == 1
    assert (options.date_min, options.date_max) == (date(2026, 7, 3), date(2026, 8, 20))


def test_filter_options_list_a_claimed_card_without_transactions_and_an_empty_user_has_none(
    run, seed_user, seed_claimed_card
):
    owner = seed_user("owner@example.com", "Owner")
    card = seed_claimed_card(user=owner, last4="3333", owner_name="Owner O")
    nobody = seed_user("nobody@example.com", "Nobody")

    mine = run(owner, lambda repository, user_id: repository.filter_options(user_id))
    empty = run(nobody, lambda repository, user_id: repository.filter_options(user_id))

    assert [c.card_id for c in mine.cards] == [card.card_id]
    assert mine.date_min is None and mine.date_max is None
    assert (empty.cards, empty.bank_categories, empty.original_currencies, empty.types) == ((),) * 4
    assert (empty.unknown_currency_count, empty.unknown_billing_currency_count) == (0, 0)
    assert (empty.date_min, empty.date_max) == (None, None)


def test_a_transaction_is_found_by_its_public_id(run, shop):
    listed_record = next(r for r in listed(run, shop.user).items if r.merchant == "Rome Cafe")

    found = run(
        shop.user, lambda repository, user_id: repository.get(user_id, listed_record.public_id)
    )
    missing = run(shop.user, lambda repository, user_id: repository.get(user_id, PublicId("txn_x")))

    assert found == listed_record
    assert missing is None


@pytest.fixture
def stranger(seed_user, seed_claimed_card, seed_transaction):
    user = seed_user("stranger@example.com", "Stranger")
    card = seed_claimed_card(user=user, last4="9999", owner_name="Stranger S")
    seed_transaction(
        card,
        **UNKNOWN_FOREIGN,
        merchant_name_raw="Stranger Only",
        bank_category_raw="Secret",
        merchant_country="DE",
        transaction_date=date(2020, 1, 1),
    )
    return user


def test_one_user_never_sees_the_transactions_or_options_of_another(run, shop, stranger):
    mine = listed(run, shop.user)
    theirs = listed(run, stranger)

    assert "Stranger Only" not in merchants(mine)
    assert merchants(theirs) == ["Stranger Only"]
    ann_summary = run(
        shop.user, lambda repository, user_id: repository.summary(user_id, TransactionFilter())
    )
    assert ann_summary.count == 6
    assert ann_summary.unknown_currency_count == 1
    options = run(shop.user, lambda repository, user_id: repository.filter_options(user_id))
    assert "Secret" not in [o.name for o in options.bank_categories]
    assert "DE" not in [o.code for o in options.countries]
    assert options.date_min == date(2026, 7, 3)
    _, keys = grouped(run, shop.user, Dimension.CATEGORY)
    assert "Secret" not in keys


def test_another_users_transaction_cannot_be_read_resolved_or_locked(run, shop, stranger):
    (other,) = listed(run, stranger).items

    def probe(repository, user_id):
        return (
            repository.get(user_id, other.public_id),
            repository.visible_id(user_id, other.public_id),
            repository.lock_visible(user_id, [other.public_id]),
        )

    assert run(shop.user, probe) == (None, None, ())
    assert run(stranger, probe)[1] == other.transaction_id


def test_visible_id_resolves_the_public_id_of_a_visible_transaction(run, shop):
    record = listed(run, shop.user).items[0]

    resolved = run(
        shop.user, lambda repository, user_id: repository.visible_id(user_id, record.public_id)
    )

    assert resolved == record.transaction_id


def test_lock_visible_returns_only_visible_rows_in_id_order_and_holds_their_locks(
    factory, run, shop, stranger, sql
):
    ids = {r.merchant: r for r in [*listed(run, shop.user).items, *listed(run, stranger).items]}
    wanted = [
        ids["Mystery Shop"].public_id,
        ids["Super Pharm"].public_id,
        ids["Stranger Only"].public_id,
    ]

    with factory.create() as uow:
        locked = uow.transaction_query.lock_visible(shop.user.user_id, wanted)
        with pytest.raises(Exception, match="lock"):
            sql(
                "SELECT id FROM transactions WHERE id = :t FOR UPDATE NOWAIT",
                t=ids["Super Pharm"].transaction_id,
            )
        nothing = uow.transaction_query.lock_visible(shop.user.user_id, [])

    assert [ref.public_id for ref in locked] == [
        ids["Super Pharm"].public_id,
        ids["Mystery Shop"].public_id,
    ]
    assert nothing == ()
    assert (
        len(
            sql(
                "SELECT id FROM transactions WHERE id = :t FOR UPDATE NOWAIT",
                t=ids["Super Pharm"].transaction_id,
            )
        )
        == 1
    )


def test_lock_pending_matching_locks_only_visible_pending_currency_rows_that_match(
    run, shop, stranger, seed_transaction
):
    seed_transaction(
        shop.pharm_card,
        aggregate_review_reasons={ReviewReason.UNKNOWN_CURRENCY},
        merchant_name_raw="Domestic Reviewed",
    )
    seed_transaction(
        shop.pharm_card,
        **UNKNOWN_FOREIGN,
        merchant_name_raw="Old Mystery",
        transaction_date=date(2025, 1, 1),
        merchant_country="DE",
    )

    def pending(**filter):
        return [
            ref.public_id
            for ref in run(
                shop.user,
                lambda repository, user_id: repository.lock_pending_matching(
                    user_id, PendingValidationFilter(**filter)
                ),
            )
        ]

    everyone = pending()
    names = {r.public_id: r.merchant for r in listed(run, shop.user).items}

    assert sorted(names[p] for p in everyone) == ["Mystery Shop", "Old Mystery"]
    assert [names[p] for p in pending(countries=("FR",))] == ["Mystery Shop"]
    assert [names[p] for p in pending(date_to=date(2025, 12, 31))] == ["Old Mystery"]
    assert [names[p] for p in pending(date_from=date(2026, 1, 1), merchants=("mystery",))] == [
        "Mystery Shop"
    ]
    assert pending(countries=("US",)) == []


def test_bank_categories_are_counted_and_checked_for_the_user_only(run, shop, stranger):
    counts = run(shop.user, lambda repository, user_id: repository.bank_category_counts(user_id))

    assert [(c.name, c.count) for c in counts] == [
        ("Pharmacy", 2),
        ("Restaurants", 1),
        ("Shopping", 1),
    ]
    assert run(
        shop.user, lambda repository, user_id: repository.has_bank_category(user_id, "Pharmacy")
    )
    assert not run(
        shop.user, lambda repository, user_id: repository.has_bank_category(user_id, "Secret")
    )
    assert run(
        stranger, lambda repository, user_id: repository.has_bank_category(user_id, "Secret")
    )
