"""The SQL analysis repository: signed billing sums per group and bucket in one billing currency,
over the visible transactions of the caller only."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from bank_transactions.domain.enums import ReviewReason, TransactionType
from bank_transactions.domain.money import USD
from bank_transactions.domain.queries import (
    AnalysisInclude,
    AnalysisQuery,
    Bucket,
    Dimension,
    TransactionFilter,
)
from bank_transactions.persistence.commands import NewForeignDetail
from bank_transactions.persistence.records import (
    AnalysisGroupRecord,
    MatrixCellRecord,
    SeriesPointRecord,
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
YEAR = {"date_from": date(2025, 1, 1), "date_to": date(2025, 12, 31)}


@dataclass
class Dataset:
    user: Any
    card: Any


def query(billing_currency: str = "ILS", filter: TransactionFilter | None = None, **overrides):
    values = {
        **YEAR,
        "filter": filter or TransactionFilter(),
        "billing_currency": billing_currency,
        "include": frozenset(AnalysisInclude),
    }
    return AnalysisQuery(**{**values, **overrides})


@pytest.fixture
def make_trip(sql: Callable[..., list[Any]]) -> Callable[..., str]:
    def make(user: Any, name: str, start: date, end: date) -> str:
        (row,) = sql(
            "INSERT INTO trips (public_id, user_id, name, currency, start_date, end_date)"
            " VALUES ('trp_' || md5(random()::text), :u, :n, 'USD', :s, :e) RETURNING public_id",
            u=user.user_id,
            n=name,
            s=start,
            e=end,
        )
        return str(row[0])

    return make


@pytest.fixture
def run(factory):
    """Run `action(repository, user_id)` on the analysis repository in a fresh UoW."""

    def execute(user, action):
        with factory.create() as uow:
            return action(uow.analysis, user.user_id)

    return execute


@pytest.fixture
def shop(seed_user, seed_claimed_card, seed_transaction) -> Dataset:
    """Seven ILS/USD/no-currency rows in January and March 2025; the refund is negative."""
    user = seed_user()
    card = seed_claimed_card(user=user, last4="1111", owner_name="Dana Levi")
    pharm = {"merchant_name_raw": "Super Pharm", "bank_category_raw": "Pharmacy"}
    seed_transaction(
        card,
        **pharm,
        transaction_date=date(2025, 1, 6),
        billing_amount=Decimal("100.00"),
        merchant_country="IL",
    )
    seed_transaction(
        card,
        **pharm,
        transaction_date=date(2025, 1, 12),
        billing_amount=Decimal("50.00"),
        merchant_country="IL",
    )
    seed_transaction(
        card,
        merchant_name_raw="Refund Shop",
        bank_category_raw="Pharmacy",
        transaction_date=date(2025, 1, 13),
        billing_amount=Decimal("-30.00"),
        merchant_country="IL",
    )
    seed_transaction(
        card,
        **FOREIGN,
        merchant_name_raw="Rome Cafe",
        bank_category_raw="Restaurants",
        transaction_date=date(2025, 3, 2),
        billing_amount=Decimal("36.50"),
        merchant_country="IT",
    )
    seed_transaction(
        card,
        **UNKNOWN_FOREIGN,
        merchant_name_raw="Mystery Shop",
        transaction_date=date(2025, 3, 10),
        billing_amount=Decimal("50.00"),
        merchant_country="FR",
    )
    seed_transaction(
        card,
        **{**FOREIGN, "original_currency": USD},
        merchant_name_raw="Amazon",
        bank_category_raw="Shopping",
        transaction_date=date(2025, 3, 15),
        billing_amount=Decimal("20.00"),
        billing_currency=USD,
        transaction_type=TransactionType.RECURRING,
    )
    seed_transaction(
        card,
        merchant_name_raw="No Currency Co",
        transaction_date=date(2025, 3, 20),
        billing_amount=Decimal("15.00"),
        billing_currency=None,
    )
    return Dataset(user, card)


@pytest.fixture
def stranger(seed_user, seed_claimed_card, seed_transaction):
    user = seed_user("stranger@example.com", "Stranger")
    card = seed_claimed_card(user=user, last4="9999", owner_name="Stranger S")
    seed_transaction(
        card,
        merchant_name_raw="Stranger Only",
        bank_category_raw="Secret",
        transaction_date=date(2025, 1, 6),
        billing_amount=Decimal("999.00"),
        merchant_country="DE",
    )
    return user


def series(run, user, bucket: Bucket, billing_currency="ILS", **overrides):
    return run(
        user,
        lambda repository, user_id: repository.series(
            user_id, query(billing_currency, **overrides), bucket, None
        ),
    )


def groups(run, user, dimension: Dimension, filter=None, **overrides):
    return run(
        user,
        lambda repository, user_id: repository.groups(
            user_id, query(filter=filter, **overrides), dimension
        ),
    )


def by_key(records):
    return {record.key: (record.count, record.net) for record in records}


def test_monthly_series_sums_signed_billing_and_lists_only_months_with_rows(run, shop):
    points = series(run, shop.user, Bucket.MONTH)

    assert points == (
        SeriesPointRecord(date(2025, 1, 1), None, 3, Decimal("120.00")),
        SeriesPointRecord(date(2025, 3, 1), None, 2, Decimal("86.50")),
    )


def test_only_rows_in_the_billing_currency_are_summed(run, shop):
    (dollars,) = series(run, shop.user, Bucket.MONTH, billing_currency="USD")

    assert (dollars.bucket_start, dollars.count, dollars.net) == (
        date(2025, 3, 1),
        1,
        Decimal("20.00"),
    )
    assert series(run, shop.user, Bucket.MONTH, billing_currency="EUR") == ()


def test_weeks_start_on_monday(run, shop):
    points = {p.bucket_start: (p.count, p.net) for p in series(run, shop.user, Bucket.WEEK)}

    assert points == {
        date(2025, 1, 6): (2, Decimal("150.00")),
        date(2025, 1, 13): (1, Decimal("-30.00")),
        date(2025, 2, 24): (1, Decimal("36.50")),
        date(2025, 3, 10): (1, Decimal("50.00")),
    }


def test_days_and_years_are_bucketed_by_the_transaction_date(run, shop):
    days = [p.bucket_start for p in series(run, shop.user, Bucket.DAY)]
    (year,) = series(run, shop.user, Bucket.YEAR)

    assert days == [
        date(2025, 1, 6),
        date(2025, 1, 12),
        date(2025, 1, 13),
        date(2025, 3, 2),
        date(2025, 3, 10),
    ]
    assert (year.bucket_start, year.count, year.net) == (date(2025, 1, 1), 5, Decimal("206.50"))


def test_the_range_is_inclusive_on_both_ends(run, shop):
    points = series(
        run, shop.user, Bucket.DAY, date_from=date(2025, 1, 12), date_to=date(2025, 3, 2)
    )

    assert [p.bucket_start for p in points] == [
        date(2025, 1, 12),
        date(2025, 1, 13),
        date(2025, 3, 2),
    ]


def test_the_range_of_the_query_replaces_the_dates_of_its_filter(run, shop):
    filtered = TransactionFilter(date_from=date(2030, 1, 1), date_to=date(2030, 1, 2))

    (january,) = series(run, shop.user, Bucket.MONTH, filter=filtered, date_to=date(2025, 1, 31))

    assert (january.bucket_start, january.count) == (date(2025, 1, 1), 3)


def test_the_series_splits_by_the_series_dimension(run, shop):
    points = run(
        shop.user,
        lambda repository, user_id: repository.series(
            user_id, query(), Bucket.MONTH, Dimension.CATEGORY
        ),
    )

    assert [(p.bucket_start.month, p.series_key, p.count, p.net) for p in points] == [
        (1, "Pharmacy", 3, Decimal("120.00")),
        (3, "Restaurants", 1, Decimal("36.50")),
        (3, None, 1, Decimal("50.00")),
    ]


def test_the_matrix_has_a_cell_per_row_key_and_bucket_with_rows(run, shop):
    cells = run(
        shop.user,
        lambda repository, user_id: repository.matrix(
            user_id, query(), Dimension.COUNTRY, Bucket.MONTH
        ),
    )

    assert cells == (
        MatrixCellRecord("FR", date(2025, 3, 1), 1, Decimal("50.00")),
        MatrixCellRecord("IL", date(2025, 1, 1), 3, Decimal("120.00")),
        MatrixCellRecord("IT", date(2025, 3, 1), 1, Decimal("36.50")),
    )


def test_groups_are_signed_sums_with_the_missing_group_last(run, shop):
    result = groups(run, shop.user, Dimension.CATEGORY)

    assert result == (
        AnalysisGroupRecord("Pharmacy", 3, Decimal("120.00")),
        AnalysisGroupRecord("Restaurants", 1, Decimal("36.50")),
        AnalysisGroupRecord(None, 1, Decimal("50.00")),
    )


@pytest.mark.parametrize(
    ("dimension", "expected"),
    [
        (Dimension.MONTH, {"2025-01": 3, "2025-03": 2}),
        (Dimension.YEAR, {"2025": 5}),
        (Dimension.COUNTRY, {"FR": 1, "IL": 3, "IT": 1}),
        (Dimension.CURRENCY, {"UNKNOWN": 1, "USD": 1, None: 3}),
        (Dimension.TYPE, {"REGULAR": 5}),
        (
            Dimension.MERCHANT,
            {"Mystery Shop": 1, "Refund Shop": 1, "Rome Cafe": 1, "Super Pharm": 2},
        ),
    ],
)
def test_group_keys_follow_the_transaction_list_groups(run, shop, dimension, expected):
    result = groups(run, shop.user, dimension)

    assert {record.key: record.count for record in result} == expected


def test_the_currency_group_keeps_pending_rows_apart_from_domestic_ones(run, shop):
    assert by_key(groups(run, shop.user, Dimension.CURRENCY)) == {
        "UNKNOWN": (1, Decimal("50.00")),
        "USD": (1, Decimal("36.50")),
        None: (3, Decimal("120.00")),
    }


def test_groups_follow_the_filters(run, shop):
    pharmacy = groups(
        run, shop.user, Dimension.MERCHANT, TransactionFilter(categories=("Pharmacy",))
    )
    large = groups(
        run, shop.user, Dimension.MERCHANT, TransactionFilter(min_amount=Decimal("50.00"))
    )

    assert by_key(pharmacy) == {
        "Refund Shop": (1, Decimal("-30.00")),
        "Super Pharm": (2, Decimal("150.00")),
    }
    assert set(by_key(large)) == {"Mystery Shop", "Super Pharm"}


def test_groups_by_trip_use_the_trip_id_and_keep_the_rest_under_a_null_key(run, shop, make_trip):
    # automatic members are the rows in the trip's currency (USD) dated inside it: Rome Cafe
    trip_id = make_trip(shop.user, "Rome", date(2025, 3, 1), date(2025, 3, 31))

    assert by_key(groups(run, shop.user, Dimension.TRIP)) == {
        trip_id: (1, Decimal("36.50")),
        None: (4, Decimal("170.00")),
    }


def test_groups_by_card_use_card_public_ids(run, shop):
    (card,) = groups(run, shop.user, Dimension.CARD)

    assert card.key is not None and card.key.startswith("crd_")
    assert (card.count, card.net) == (5, Decimal("206.50"))


def test_a_user_never_sees_the_rows_of_another(run, shop, stranger):
    mine = series(run, shop.user, Bucket.MONTH)
    theirs = series(run, stranger, Bucket.MONTH)

    assert sum(p.net for p in mine) == Decimal("206.50")
    assert [(p.count, p.net) for p in theirs] == [(1, Decimal("999.00"))]
    assert "Secret" not in by_key(groups(run, shop.user, Dimension.CATEGORY))
    assert by_key(groups(run, stranger, Dimension.CATEGORY)) == {"Secret": (1, Decimal("999.00"))}
    cells = run(
        shop.user,
        lambda repository, user_id: repository.matrix(
            user_id, query(), Dimension.MERCHANT, Bucket.MONTH
        ),
    )
    assert "Stranger Only" not in {cell.row_key for cell in cells}
