"""GET /transactions, /transactions/{id} and /filter-options against the real schema, in both auth
modes. User A owns the data; user B is a stranger who must never see any of it."""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.contracts.openapi_conformance import assert_operations_conform
from tests.integration.api.conftest import PgSession, PgSignIn
from tests.storage.seeds import SeededCard, create_claimed_card, create_transaction

from bank_transactions.api.app import create_app
from bank_transactions.domain.enums import ReviewReason
from bank_transactions.domain.ids import PublicId, TripId
from bank_transactions.domain.money import USD
from bank_transactions.persistence.commands import NewForeignDetail
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import TripRecord
from bank_transactions.services.composition import ApiComposition
from bank_transactions.services.transaction_service import TransactionService

LIST = "/api/v1/transactions"
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
ROME = TripRecord(
    TripId(1), PublicId("trp_rome"), "Rome", "EUR", date(2026, 7, 1), date(2026, 7, 9)
)


class WithTrips:
    """A unit of work whose trips and visible cards come from stand-ins: both repositories are
    other units' stubs in this branch. Everything else is the real unit of work."""

    def __init__(self, uow: Any) -> None:
        self._uow = uow

    def __enter__(self) -> "WithTrips":
        self._uow.__enter__()
        return self

    def __exit__(self, *exc: Any) -> Any:
        return self._uow.__exit__(*exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._uow, name)

    @property
    def trips(self) -> Any:
        class Trips:
            def list(self, user_id: int) -> tuple[TripRecord, ...]:
                return (ROME,)

        return Trips()

    @property
    def visible_cards(self) -> Any:
        query = self._uow.transaction_query

        class Cards:
            def list_visible(self, user_id: int) -> Any:
                return query.filter_options(user_id).cards

        return Cards()


class WithTripsFactory:
    def __init__(self, factory: UnitOfWorkFactory) -> None:
        self._factory = factory

    def create(self, *args: Any, **kwargs: Any) -> WithTrips:
        return WithTrips(self._factory.create(*args, **kwargs))


@pytest.fixture
def pg_app(pg_composition: ApiComposition, factory: UnitOfWorkFactory) -> FastAPI:
    transactions = TransactionService(WithTripsFactory(factory))  # type: ignore[arg-type]
    return create_app(replace(pg_composition, transactions=transactions))


@dataclass(frozen=True)
class Ledger:
    card: SeededCard
    pharmacy: str
    cafe: str
    unknown: str
    plain: str


def ids(response: Any) -> list[str]:
    return [item["id"] for item in response.json()["items"]]


@pytest.fixture
def ledger(pg_signed_in: PgSession, factory: UnitOfWorkFactory, cipher: Any) -> Ledger:
    """Four transactions of user A on one card: domestic, foreign USD, foreign with an unknown
    currency and one without a bank category."""
    card = create_claimed_card(factory, cipher, pg_signed_in.user, last4="1111")
    seeded = {
        "pharmacy": dict(
            merchant_name_raw="Super Pharm",
            merchant_name_norm="super pharm",
            transaction_date=date(2026, 7, 3),
            billing_amount=Decimal("100.00"),
            bank_category_raw="Pharmacy",
            merchant_country="IL",
        ),
        "cafe": dict(
            FOREIGN,
            merchant_name_raw="Rome Cafe",
            merchant_name_norm="rome cafe",
            transaction_date=date(2026, 7, 5),
            billing_amount=Decimal("36.50"),
            bank_category_raw="Restaurants",
            merchant_country="IT",
        ),
        "unknown": dict(
            UNKNOWN_FOREIGN,
            merchant_name_raw="Mystery Shop",
            merchant_name_norm="mystery shop",
            transaction_date=date(2026, 8, 10),
            billing_amount=Decimal("36.50"),
            bank_category_raw="Shopping",
        ),
        "plain": dict(
            merchant_name_raw="Corner Kiosk",
            merchant_name_norm="corner kiosk",
            transaction_date=date(2026, 8, 12),
            billing_amount=Decimal("20.00"),
        ),
    }
    for fields in seeded.values():
        create_transaction(factory, card, **fields)
    items = pg_signed_in.client.get(LIST, params={"sort": "transactionDate"}).json()["items"]
    by_merchant = {item["merchant"]: item["id"] for item in items}
    return Ledger(
        card,
        by_merchant["Super Pharm"],
        by_merchant["Rome Cafe"],
        by_merchant["Mystery Shop"],
        by_merchant["Corner Kiosk"],
    )


def test_the_list_is_newest_first_with_a_summary_by_default(pg_signed_in, ledger):
    response = pg_signed_in.client.get(LIST)

    body = response.json()
    assert response.status_code == 200
    assert ids(response) == [ledger.plain, ledger.unknown, ledger.cafe, ledger.pharmacy]
    assert body["meta"] == {"page": 1, "pageSize": 50, "totalItems": 4, "totalPages": 1}
    assert body["summary"]["count"] == 4
    assert body["summary"]["unknownCurrencyCount"] == 1
    assert "groups" not in body


def test_an_item_carries_the_card_the_category_and_the_foreign_amounts(pg_signed_in, ledger):
    response = pg_signed_in.client.get(LIST, params={"merchant": "rome"})

    (item,) = response.json()["items"]
    assert item["merchant"] == "Rome Cafe"
    assert item["country"] == "IT"
    assert item["category"]["effective"] == "Restaurants"
    assert item["card"]["last4"] == "1111"
    assert item["original"] == {"amount": "10.00", "currency": "USD"}
    assert item["billing"] == {"amount": "36.50", "currency": "ILS"}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"from": "2026-07-04", "to": "2026-08-10"}, ["unknown", "cafe"]),
        ({"month": "2026-08"}, ["plain", "unknown"]),
        ({"month": "2026-08", "from": "2026-08-11"}, ["plain"]),
        ({"bankCategory": "Pharmacy"}, ["pharmacy"]),
        ({"country": "IT"}, ["cafe"]),
        ({"currency": "USD"}, ["cafe"]),
        ({"currency": "UNKNOWN"}, ["unknown"]),
        ({"needsReview": "true"}, ["unknown"]),
        ({"reviewReason": "UNKNOWN_CURRENCY"}, ["unknown"]),
        ({"billingCurrency": "ILS", "minAmount": "50"}, ["pharmacy"]),
        ({"merchant": ["kiosk", "pharm"]}, ["plain", "pharmacy"]),
        ({"tripId": "none"}, ["plain", "unknown", "cafe", "pharmacy"]),
    ],
    ids=[
        "date range",
        "month",
        "month within from",
        "bank category",
        "country",
        "currency",
        "unknown currency",
        "needs review",
        "review reason",
        "amount in a billing currency",
        "merchants",
        "no trip",
    ],
)
def test_the_filters_narrow_the_list(pg_signed_in, ledger, params, expected):
    response = pg_signed_in.client.get(LIST, params=params)

    assert response.status_code == 200
    assert ids(response) == [getattr(ledger, name) for name in expected]


def test_sorting_by_billing_amount_ascending_breaks_ties_by_insertion_order(pg_signed_in, ledger):
    response = pg_signed_in.client.get(LIST, params={"sort": "billingAmount"})

    assert ids(response) == [ledger.plain, ledger.cafe, ledger.unknown, ledger.pharmacy]


def test_a_minus_prefix_sorts_descending(pg_signed_in, ledger):
    response = pg_signed_in.client.get(LIST, params={"sort": "-merchant"})

    assert ids(response) == [ledger.pharmacy, ledger.cafe, ledger.unknown, ledger.plain]


def test_paging_splits_the_list_and_reports_the_totals(pg_signed_in, ledger):
    second = pg_signed_in.client.get(LIST, params={"page": 2, "pageSize": 3})

    assert ids(second) == [ledger.pharmacy]
    assert second.json()["meta"] == {"page": 2, "pageSize": 3, "totalItems": 4, "totalPages": 2}


def test_the_summary_totals_per_billing_currency_and_counts_the_unknown_currency(
    pg_signed_in, ledger
):
    response = pg_signed_in.client.get(LIST, params={"include": "summary", "pageSize": 1})

    assert response.json()["summary"] == {
        "count": 4,
        "unknownBillingCurrencyCount": 0,
        "billingTotals": [{"count": 4, "net": {"amount": "193.00", "currency": "ILS"}}],
        "unknownCurrencyCount": 1,
    }


def test_include_decides_which_parts_are_in_the_response(pg_signed_in, ledger):
    client = pg_signed_in.client

    nothing = client.get(LIST, params={"include": ""}).json()
    both = client.get(LIST, params={"include": "summary,groups", "groupBy": "year"}).json()
    groups_only = client.get(LIST, params={"include": "groups", "groupBy": "year"}).json()

    assert "summary" not in nothing and "groups" not in nothing
    assert both["summary"]["count"] == 4
    assert [(g["key"], g["label"], g["count"]) for g in both["groups"]] == [("2026", "2026", 4)]
    assert "summary" not in groups_only and len(groups_only["groups"]) == 1


def test_groups_by_month_are_labelled_with_the_month_name_and_follow_the_group_order(
    pg_signed_in, ledger
):
    response = pg_signed_in.client.get(
        LIST, params={"include": "groups", "groupBy": "month", "sort": "billingAmount"}
    )

    body = response.json()
    assert [(g["key"], g["label"], g["count"]) for g in body["groups"]] == [
        ("2026-07", "Jul 2026", 2),
        ("2026-08", "Aug 2026", 2),
    ]
    assert ids(response) == [ledger.cafe, ledger.pharmacy, ledger.plain, ledger.unknown]


def test_the_group_without_a_category_has_the_uncategorized_label(pg_signed_in, ledger):
    response = pg_signed_in.client.get(LIST, params={"include": "groups", "groupBy": "category"})

    groups = {g["key"]: g["label"] for g in response.json()["groups"]}
    assert groups[None] == "Uncategorized"
    assert groups["Pharmacy"] == "Pharmacy"


@pytest.mark.parametrize(
    ("dimension", "label"),
    [
        ("customCategory", "No custom category"),
        ("trip", "No trip"),
    ],
)
def test_a_group_without_a_value_has_its_default_label(pg_signed_in, ledger, dimension, label):
    response = pg_signed_in.client.get(LIST, params={"include": "groups", "groupBy": dimension})

    (group,) = response.json()["groups"]
    assert (group["key"], group["label"], group["count"]) == (None, label, 4)


def test_the_country_and_currency_groups_label_a_missing_value(pg_signed_in, ledger):
    client = pg_signed_in.client

    countries = client.get(LIST, params={"include": "groups", "groupBy": "country"}).json()
    currencies = client.get(LIST, params={"include": "groups", "groupBy": "currency"}).json()

    assert {g["key"]: g["label"] for g in countries["groups"]}[None] == "Unknown country"
    assert {g["key"]: g["label"] for g in currencies["groups"]}[None] == "Unknown currency"


def test_a_card_group_is_labelled_with_the_card(pg_signed_in, ledger):
    response = pg_signed_in.client.get(LIST, params={"include": "groups", "groupBy": "card"})

    (group,) = response.json()["groups"]
    card = response.json()["items"][0]["card"]
    assert group["key"] == card["id"]
    assert group["label"] == card["label"]


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"sort": "amount"}, "INVALID_SORT"),
        ({"sort": "group:month"}, "INVALID_SORT"),
        ({"minAmount": "5"}, "INVALID_FILTER"),
        ({"maxAmount": "5"}, "INVALID_FILTER"),
        ({"reviewReason": "UNMATCHED_FOREIGN_FEE"}, "INVALID_FILTER"),
        ({"month": "2026-08", "from": "2026-09-01"}, "INVALID_FILTER"),
        ({"billingCurrency": "ILS", "minAmount": "9", "maxAmount": "1"}, "INVALID_FILTER"),
    ],
    ids=[
        "unknown sort field",
        "internal sort field",
        "min amount alone",
        "max amount alone",
        "internal review reason",
        "month outside from",
        "min above max",
    ],
)
def test_an_invalid_query_is_a_400_problem(pg_signed_in, ledger, params, code):
    response = pg_signed_in.client.get(LIST, params=params)

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == code


def test_a_malformed_parameter_is_a_400_validation_problem(pg_signed_in, ledger):
    response = pg_signed_in.client.get(LIST, params={"groupBy": "weekday"})

    assert response.status_code == 400


def test_the_detail_of_a_foreign_transaction_has_its_fx_block(pg_signed_in, ledger):
    response = pg_signed_in.client.get(f"{LIST}/{ledger.cafe}")

    body = response.json()
    assert response.status_code == 200
    assert body["id"] == ledger.cafe
    assert body["fx"]["statedRate"] == "3.500000"
    assert body["fx"]["commission"] == {"amount": "0.50", "currency": "ILS"}
    assert body["installmentPlan"] is None
    assert body["importedAt"]


def test_the_detail_of_a_domestic_transaction_has_no_fx_block(pg_signed_in, ledger):
    body = pg_signed_in.client.get(f"{LIST}/{ledger.pharmacy}").json()

    assert body["fx"] is None
    assert body["merchant"] == "Super Pharm"


def test_an_unknown_transaction_is_a_404_problem(pg_signed_in, ledger):
    response = pg_signed_in.client.get(f"{LIST}/txn_doesnotexist")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_the_filter_options_describe_the_data_of_the_user(pg_signed_in, ledger):
    response = pg_signed_in.client.get("/api/v1/filter-options")

    body = response.json()
    assert response.status_code == 200
    assert {o["bank"]: o["count"] for o in body["categories"]} == {
        "Pharmacy": 1,
        "Restaurants": 1,
        "Shopping": 1,
    }
    assert [c["last4"] for c in body["cards"]] == ["1111"]
    assert body["currencies"] == [{"code": "USD", "count": 1}]
    assert body["unknownCurrencyCount"] == 1
    assert body["billingCurrencies"] == [{"code": "ILS", "count": 4}]
    assert body["unknownBillingCurrencyCount"] == 0
    assert body["countries"] == [{"code": "IL", "count": 1}, {"code": "IT", "count": 1}]
    assert body["trips"] == [{"id": "trp_rome", "name": "Rome"}]
    assert body["dateRange"] == {"min": "2026-07-03", "max": "2026-08-12"}


def test_a_user_with_no_transactions_has_empty_options_and_an_empty_list(pg_sign_in: PgSignIn):
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")

    options = stranger.client.get("/api/v1/filter-options").json()
    listing = stranger.client.get(LIST).json()

    assert options["dateRange"] == {"min": None, "max": None}
    assert options["categories"] == [] and options["cards"] == []
    assert listing["items"] == []
    assert listing["meta"]["totalItems"] == 0
    assert listing["summary"]["count"] == 0


def test_another_user_cannot_see_the_transactions_of_user_a(pg_sign_in: PgSignIn, ledger):
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")

    detail = stranger.client.get(f"{LIST}/{ledger.pharmacy}")
    listing = stranger.client.get(LIST, params={"include": "summary,groups", "groupBy": "month"})
    options = stranger.client.get("/api/v1/filter-options")

    assert detail.status_code == 404
    assert listing.json()["items"] == []
    assert listing.json()["summary"]["count"] == 0
    assert listing.json()["groups"] == []
    assert options.json()["cards"] == []
    assert options.json()["currencies"] == []
    assert options.json()["unknownCurrencyCount"] == 0


def test_filtering_by_the_card_of_another_user_matches_nothing(
    pg_sign_in: PgSignIn, pg_signed_in, ledger, factory, cipher
):
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")
    own_card = create_claimed_card(factory, cipher, stranger.user, last4="9999")
    create_transaction(factory, own_card, merchant_name_raw="B Shop", merchant_name_norm="b shop")
    a_card = pg_signed_in.client.get(LIST).json()["items"][0]["card"]["id"]

    response = stranger.client.get(LIST, params={"cardId": a_card})

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert stranger.client.get(LIST).json()["meta"]["totalItems"] == 1
    assert pg_signed_in.client.get(LIST).json()["meta"]["totalItems"] == 4


@pytest.mark.parametrize(
    "url", [LIST, f"{LIST}/txn_1", "/api/v1/filter-options"], ids=["list", "detail", "options"]
)
def test_the_read_endpoints_require_a_session(pg_client: TestClient, url):
    response = pg_client.get(url)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_the_owned_operations_conform_to_the_contract(composition: ApiComposition):
    assert_operations_conform(
        create_app(composition), "listTransactions", "getTransaction", "getFilterOptions"
    )
