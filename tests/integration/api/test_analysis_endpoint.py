"""GET /spending-analysis against the real schema, in both auth modes. User A owns the data; user B
is a stranger who must never see any of it. The data lies in the past, so its buckets are complete
whatever today is."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.contracts.openapi_conformance import assert_operations_conform
from tests.integration.api.conftest import PgSession, PgSignIn
from tests.storage.seeds import SeededCard, create_claimed_card, create_transaction

from bank_transactions.api.app import create_app
from bank_transactions.domain.enums import ReviewReason
from bank_transactions.domain.money import USD
from bank_transactions.persistence.commands import NewForeignDetail
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.services.composition import ApiComposition

URL = "/api/v1/spending-analysis"
FIRST_QUARTER = {"from": "2024-01-01", "to": "2024-03-31", "billingCurrency": "ILS"}
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


def money(amount: str, currency: str = "ILS") -> dict[str, str]:
    return {"amount": amount, "currency": currency}


def analyze(session: PgSession, **params: Any) -> Any:
    return session.client.get(URL, params={**FIRST_QUARTER, **params})


@pytest.fixture
def card(pg_signed_in: PgSession, factory: UnitOfWorkFactory, cipher: Any) -> SeededCard:
    """Five transactions of user A on one card: pharmacy 100.00 and a -30.00 refund in January,
    a foreign cafe and an uncategorised pending-currency row in March, and a row in December
    2023 that only a comparison range reaches."""
    card = create_claimed_card(factory, cipher, pg_signed_in.user, last4="1111")
    rows = [
        dict(
            merchant_name_raw="Super Pharm",
            merchant_name_norm="super pharm",
            transaction_date=date(2024, 1, 10),
            billing_amount=Decimal("100.00"),
            bank_category_raw="Pharmacy",
            merchant_country="IL",
        ),
        dict(
            merchant_name_raw="Refund Shop",
            merchant_name_norm="refund shop",
            transaction_date=date(2024, 1, 20),
            billing_amount=Decimal("-30.00"),
            bank_category_raw="Pharmacy",
            merchant_country="IL",
        ),
        dict(
            FOREIGN,
            merchant_name_raw="Rome Cafe",
            merchant_name_norm="rome cafe",
            transaction_date=date(2024, 3, 5),
            billing_amount=Decimal("36.50"),
            bank_category_raw="Restaurants",
            merchant_country="IT",
        ),
        dict(
            UNKNOWN_FOREIGN,
            merchant_name_raw="Mystery Shop",
            merchant_name_norm="mystery shop",
            transaction_date=date(2024, 3, 12),
            billing_amount=Decimal("50.00"),
            merchant_country="FR",
        ),
        dict(
            merchant_name_raw="Old Pharm",
            merchant_name_norm="old pharm",
            transaction_date=date(2023, 12, 15),
            billing_amount=Decimal("25.00"),
            bank_category_raw="Pharmacy",
            merchant_country="IL",
        ),
    ]
    for fields in rows:
        create_transaction(factory, card, **fields)
    return card


def test_the_default_analysis_has_totals_groups_a_zero_filled_series_and_a_matrix(
    pg_signed_in, card
):
    response = analyze(pg_signed_in)

    body = response.json()
    assert response.status_code == 200
    assert body["range"] == {"from": "2024-01-01", "to": "2024-03-31"}
    assert body["comparisonRange"] is None
    assert body["comparisonTotals"] is None
    assert body["totals"] == {
        "net": money("156.50"),
        "count": 4,
        "monthlyAverage": {"value": money("78.25"), "completeBuckets": 2, "partialBuckets": []},
    }
    assert body["groups"] == [
        {
            "key": "Pharmacy",
            "label": "Pharmacy",
            "net": money("70.00"),
            "count": 2,
            "previous": None,
        },
        {
            "key": "Restaurants",
            "label": "Restaurants",
            "net": money("36.50"),
            "count": 1,
            "previous": None,
        },
        {
            "key": None,
            "label": "Uncategorized",
            "net": money("50.00"),
            "count": 1,
            "previous": None,
        },
    ]
    assert body["timeSeries"] == {
        "bucket": "month",
        "average": money("78.25"),
        "points": [
            {
                "key": "2024-01",
                "start": "2024-01-01",
                "end": "2024-01-31",
                "net": money("70.00"),
                "complete": True,
            },
            {
                "key": "2024-02",
                "start": "2024-02-01",
                "end": "2024-02-29",
                "net": money("0.00"),
                "complete": True,
            },
            {
                "key": "2024-03",
                "start": "2024-03-01",
                "end": "2024-03-31",
                "net": money("86.50"),
                "complete": True,
            },
        ],
        "previous": None,
    }
    assert body["matrix"] == {
        "rowDimension": "category",
        "bucket": "month",
        "columns": [
            {"key": "2024-01", "complete": True},
            {"key": "2024-02", "complete": True},
            {"key": "2024-03", "complete": True},
        ],
        "rows": [
            {
                "key": "Pharmacy",
                "label": "Pharmacy",
                "cells": [{"net": money("70.00")}, {"net": money("0.00")}, {"net": money("0.00")}],
                "total": money("70.00"),
            },
            {
                "key": "Restaurants",
                "label": "Restaurants",
                "cells": [{"net": money("0.00")}, {"net": money("0.00")}, {"net": money("36.50")}],
                "total": money("36.50"),
            },
            {
                "key": None,
                "label": "Uncategorized",
                "cells": [{"net": money("0.00")}, {"net": money("0.00")}, {"net": money("50.00")}],
                "total": money("50.00"),
            },
        ],
        "columnTotals": [money("70.00"), money("0.00"), money("86.50")],
    }


@pytest.mark.parametrize(
    "include", ["groups", "timeSeries", "matrix", "groups,matrix", ""], ids=str
)
def test_only_the_included_sections_are_in_the_body(pg_signed_in, card, include):
    body = analyze(pg_signed_in, include=include).json()

    wanted = set(include.split(","))
    assert {"groups", "timeSeries", "matrix"} & set(body) == wanted - {""}
    assert body["totals"]["count"] == 4
    assert "comparisonRange" in body and "comparisonTotals" in body


def test_the_previous_range_is_the_same_number_of_days_right_before(pg_signed_in, card):
    body = analyze(pg_signed_in, compare="previous").json()

    assert body["comparisonRange"] == {"from": "2023-10-02", "to": "2023-12-31"}
    assert body["comparisonTotals"]["net"] == money("25.00")
    assert body["comparisonTotals"]["count"] == 1
    assert body["comparisonTotals"]["monthlyAverage"]["value"] == money("25.00")
    groups = {group["key"]: group for group in body["groups"]}
    assert groups["Pharmacy"]["previous"] == {"net": money("25.00"), "count": 1}
    assert groups["Restaurants"]["previous"] == {"net": money("0.00"), "count": 0}
    previous = body["timeSeries"]["previous"]
    assert [(p["key"], p["net"]["amount"]) for p in previous["points"]] == [
        ("2023-10", "0.00"),
        ("2023-11", "0.00"),
        ("2023-12", "25.00"),
    ]
    assert previous["average"] == money("25.00")


def test_year_over_year_compares_with_the_same_dates_a_year_earlier(pg_signed_in, card):
    body = analyze(pg_signed_in, compare="yoy").json()

    assert body["comparisonRange"] == {"from": "2023-01-01", "to": "2023-03-31"}
    assert body["comparisonTotals"]["count"] == 0
    assert body["comparisonTotals"]["monthlyAverage"]["value"] is None


def test_a_comparison_group_missing_from_the_range_is_listed_with_zeros(pg_signed_in, card):
    body = analyze(pg_signed_in, **{"from": "2024-02-01"}, compare="previous").json()

    groups = {group["key"]: group for group in body["groups"]}
    assert groups["Pharmacy"]["net"] == money("0.00")
    assert groups["Pharmacy"]["count"] == 0
    assert groups["Pharmacy"]["previous"] == {"net": money("95.00"), "count": 3}


def test_weeks_start_on_monday_and_have_no_average(pg_signed_in, card):
    body = analyze(pg_signed_in, **{"from": "2024-01-08", "to": "2024-01-21"}, bucket="week").json()

    series = body["timeSeries"]
    assert series["bucket"] == "week"
    assert series["average"] is None
    assert [(p["key"], p["start"], p["end"], p["net"]["amount"]) for p in series["points"]] == [
        ("2024-01-08", "2024-01-08", "2024-01-14", "100.00"),
        ("2024-01-15", "2024-01-15", "2024-01-21", "-30.00"),
    ]
    assert body["matrix"]["bucket"] == "week"
    assert body["totals"]["monthlyAverage"]["completeBuckets"] == 0


def test_a_period_group_takes_the_matrix_rows_from_series_by(pg_signed_in, card):
    body = analyze(pg_signed_in, groupBy="month", seriesBy="country").json()

    assert [group["key"] for group in body["groups"]] == ["2024-01", "2024-03"]
    assert body["groups"][0]["label"] == "Jan 2024"
    assert body["matrix"]["rowDimension"] == "country"
    assert [row["key"] for row in body["matrix"]["rows"]] == ["FR", "IL", "IT"]


def test_groups_by_card_are_labelled_with_the_card(pg_signed_in, card):
    body = analyze(pg_signed_in, groupBy="card", include="groups").json()

    (group,) = body["groups"]
    assert group["key"].startswith("crd_")
    assert "1111" in group["label"]
    assert group["count"] == 4


def test_the_currency_groups_keep_pending_rows_apart(pg_signed_in, card):
    body = analyze(pg_signed_in, groupBy="currency", include="groups").json()

    assert [(g["key"], g["label"], g["net"]["amount"]) for g in body["groups"]] == [
        ("UNKNOWN", "Unknown currency", "50.00"),
        ("USD", "USD", "36.50"),
        (None, "No original currency", "70.00"),
    ]


def test_the_billing_currency_selects_the_rows_that_are_summed(pg_signed_in, card):
    body = analyze(pg_signed_in, billingCurrency="EUR").json()

    assert body["totals"]["net"] == money("0.00", "EUR")
    assert body["totals"]["count"] == 0
    assert body["totals"]["monthlyAverage"]["value"] is None
    assert body["groups"] == []
    assert [p["net"] for p in body["timeSeries"]["points"]] == [money("0.00", "EUR")] * 3


@pytest.mark.parametrize(
    ("params", "count", "net"),
    [
        ({"category": "Pharmacy"}, 2, "70.00"),
        ({"customCategory": "none"}, 4, "156.50"),
        ({"country": "IT"}, 1, "36.50"),
        ({"currency": "USD"}, 1, "36.50"),
        ({"currency": "UNKNOWN"}, 1, "50.00"),
        ({"type": "REGULAR", "merchant": ["rome", "mystery"]}, 2, "86.50"),
        ({"minAmount": "40"}, 2, "150.00"),
        ({"maxAmount": "0"}, 1, "-30.00"),
        ({"tripId": "none"}, 4, "156.50"),
    ],
)
def test_the_filters_narrow_every_aggregate(pg_signed_in, card, params, count, net):
    body = analyze(pg_signed_in, **params).json()

    assert (body["totals"]["count"], body["totals"]["net"]["amount"]) == (count, net)
    assert sum(g["count"] for g in body["groups"]) == count
    assert sum(Decimal(p["net"]["amount"]) for p in body["timeSeries"]["points"]) == Decimal(net)


def test_the_card_filter_takes_card_ids(pg_signed_in, card):
    card_id = pg_signed_in.client.get("/api/v1/transactions").json()["items"][0]["card"]["id"]

    mine = analyze(pg_signed_in, cardId=card_id)
    other = analyze(pg_signed_in, cardId="crd_unknown")

    assert mine.json()["totals"]["count"] == 4
    assert other.json()["totals"]["count"] == 0


@pytest.mark.parametrize(
    "params",
    [{"month": "2024-03"}, {"bankCategory": "Pharmacy"}, {"needsReview": "true"}, {"limit": "1"}],
    ids=lambda params: next(iter(params)),
)
def test_parameters_the_operation_does_not_define_do_not_filter(pg_signed_in, card, params):
    body = analyze(pg_signed_in, **params).json()

    assert body["totals"]["count"] == 4


@pytest.mark.parametrize(
    "params",
    [
        {"compare": "weekly"},
        {"groupBy": "weekday"},
        {"seriesBy": "weekday"},
        {"bucket": "decade"},
        {"include": "groups,tables"},
        {"billingCurrency": "ils"},
        {"billingCurrency": "ILSX"},
        {"from": "2024-13-01"},
        {"from": "2024-04-01"},
        {"minAmount": "abc"},
        {"minAmount": "9", "maxAmount": "1"},
        {"currency": "usd"},
        {"country": "ISR"},
        {"type": "REFUND"},
        {"bucket": "day", "from": "2020-01-01"},
        {"bucket": "day", "from": "2024-01-01", "to": "2027-03-01", "include": "matrix"},
    ],
    ids=str,
)
def test_an_invalid_query_is_a_400_problem(pg_signed_in, card, params):
    response = analyze(pg_signed_in, **params)

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "INVALID_FILTER"


@pytest.mark.parametrize("missing", ["from", "to", "billingCurrency"])
def test_the_range_and_the_billing_currency_are_required(pg_signed_in, card, missing):
    params = {key: value for key, value in FIRST_QUARTER.items() if key != missing}

    response = pg_signed_in.client.get(URL, params=params)

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "INVALID_FILTER"


def test_a_range_over_a_thousand_buckets_is_an_invalid_filter(pg_signed_in, card):
    response = analyze(pg_signed_in, bucket="day", **{"from": "2020-01-01"})

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_FILTER"


def test_the_buckets_are_only_counted_when_a_section_needs_them(pg_signed_in, card):
    response = analyze(pg_signed_in, bucket="day", include="groups", **{"from": "2020-01-01"})

    assert response.status_code == 200
    assert response.json()["totals"]["count"] == 5


def test_another_user_sees_none_of_the_data_of_user_a(pg_sign_in: PgSignIn, card):
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")

    body = analyze(stranger, compare="previous").json()

    assert body["totals"]["count"] == 0
    assert body["totals"]["net"] == money("0.00")
    assert body["groups"] == []
    assert body["matrix"]["rows"] == []
    assert body["comparisonTotals"]["count"] == 0
    assert all(p["net"] == money("0.00") for p in body["timeSeries"]["points"])


def test_each_user_analyses_only_their_own_rows(
    pg_sign_in: PgSignIn, pg_signed_in, card, factory, cipher
):
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")
    their_card = create_claimed_card(factory, cipher, stranger.user, last4="9999")
    create_transaction(
        factory,
        their_card,
        merchant_name_raw="B Shop",
        merchant_name_norm="b shop",
        transaction_date=date(2024, 2, 2),
        billing_amount=Decimal("999.00"),
        bank_category_raw="Secret",
    )

    theirs = analyze(stranger).json()
    mine = analyze(pg_signed_in).json()

    assert [g["key"] for g in theirs["groups"]] == ["Secret"]
    assert theirs["totals"]["net"] == money("999.00")
    assert "Secret" not in [g["key"] for g in mine["groups"]]
    assert mine["totals"]["net"] == money("156.50")


def test_the_analysis_requires_a_session(pg_client: TestClient):
    response = pg_client.get(URL, params=FIRST_QUARTER)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_the_owned_operation_conforms_to_the_contract(composition: ApiComposition):
    assert_operations_conform(create_app(composition), "getSpendingAnalysis")
