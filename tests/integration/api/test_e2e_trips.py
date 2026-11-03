"""End to end on Postgres: a trip over the rows the currency validation turns into JPY, and how the
membership follows validation, undo, tag and exclusion in the transaction list and the analysis."""

from decimal import Decimal
from typing import Any

import pytest
from tests.integration.api.conftest import PgSession
from tests.integration.api.e2e_support import (
    API,
    MAX_YEARLY,
    all_transaction_ids,
    import_example,
    list_transactions,
    totals_by_currency,
)

pytestmark = pytest.mark.postgres

BATCHES = f"{API}/currency-validation-batches"
TRIPS = f"{API}/trips"


@pytest.fixture
def yearly(pg_signed_in: PgSession) -> PgSession:
    import_example(pg_signed_in, MAX_YEARLY)
    return pg_signed_in


def change(session: PgSession, method: str, url: str) -> int:
    response = getattr(session.client, method)(url, headers=session.unsafe_headers)
    return response.status_code


def japan_dates(session: PgSession) -> tuple[str, str]:
    """First and last day of the rows whose country is JP."""
    ends = (
        list_transactions(session, country="JP", sort=sort, pageSize=1)["items"][0]
        for sort in ("transactionDate", "-transactionDate")
    )
    first, last = (item["transactionDate"] for item in ends)
    return first, last


def validate_japan(session: PgSession) -> dict[str, Any]:
    response = session.client.post(
        BATCHES,
        json={"currency": "JPY", "filter": {"country": ["JP"]}},
        headers=session.unsafe_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def trip_stats(session: PgSession, trip_id: str) -> dict[str, Any]:
    response = session.client.get(f"{TRIPS}/{trip_id}")
    assert response.status_code == 200, response.text
    return response.json()


def analyze_by_trip(session: PgSession) -> dict[str, Any]:
    window = session.client.get(f"{API}/filter-options").json()["dateRange"]
    response = session.client.get(
        f"{API}/spending-analysis",
        params={
            "from": window["min"],
            "to": window["max"],
            "billingCurrency": "ILS",
            "groupBy": "trip",
            "include": "groups",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_validating_the_currency_makes_the_matching_rows_automatic_trip_members(
    yearly: PgSession,
) -> None:
    session, headers = yearly, yearly.unsafe_headers
    first, last = japan_dates(session)
    created = session.client.post(
        TRIPS,
        json={"name": "Japan", "currency": "JPY", "startDate": first, "endDate": last},
        headers=headers,
    )
    assert created.status_code == 201
    trip = created.json()
    assert trip["stats"]["count"] == 0

    batch = validate_japan(session)
    assert batch["validatedCount"] == 317
    after = trip_stats(session, trip["id"])
    assert after["stats"]["count"] == 317
    assert len(after["auto"]) == 317 and after["tagged"] == [] and after["excluded"] == []
    listed = session.client.get(TRIPS).json()["items"][0]["stats"]
    assert (listed["count"], listed["autoCount"], listed["taggedCount"]) == (317, 317, 0)

    members = list_transactions(session, tripId=trip["id"])
    assert members["summary"]["count"] == 317
    assert {item["trip"]["membership"] for item in members["items"]} == {"AUTO"}
    assert {item["trip"]["id"] for item in members["items"]} == {trip["id"]}
    assert totals_by_currency(members["summary"]) == totals_by_currency(after["stats"])

    undone = session.client.delete(f"{BATCHES}/{batch['id']}", headers=headers)
    assert undone.status_code == 204
    assert trip_stats(session, trip["id"])["stats"]["count"] == 0
    assert list_transactions(session, tripId=trip["id"])["summary"]["count"] == 0


def test_tagging_and_excluding_change_the_trip_in_every_view(yearly: PgSession) -> None:
    session, headers = yearly, yearly.unsafe_headers
    first, last = japan_dates(session)
    trip = session.client.post(
        TRIPS,
        json={"name": "Japan", "currency": "JPY", "startDate": first, "endDate": last},
        headers=headers,
    ).json()
    validate_japan(session)
    base = f"{TRIPS}/{trip['id']}"
    detail = trip_stats(session, trip["id"])
    ils_count, ils_net = totals_by_currency(detail["stats"])["ILS"]
    assert ils_count == 317

    member = detail["auto"][0]
    domestic = list_transactions(session, country="IL", pageSize=1)["items"][0]
    assert domestic["trip"] is None

    refused = session.client.put(f"{base}/excluded-transactions/{domestic['id']}", headers=headers)
    assert (refused.status_code, refused.json()["code"]) == (409, "NOT_AUTO_MEMBER")

    assert change(session, "put", f"{base}/excluded-transactions/{member['id']}") == 204
    assert change(session, "put", f"{base}/tagged-transactions/{domestic['id']}") == 204

    changed = trip_stats(session, trip["id"])
    assert changed["stats"]["count"] == 317
    assert [item["id"] for item in changed["excluded"]] == [member["id"]]
    assert [item["id"] for item in changed["tagged"]] == [domestic["id"]]
    _, net = totals_by_currency(changed["stats"])["ILS"]
    member_amount = Decimal(member["billing"]["amount"])
    domestic_amount = Decimal(domestic["billing"]["amount"])
    assert net == ils_net - member_amount + domestic_amount

    tagged_detail = session.client.get(f"{API}/transactions/{domestic['id']}").json()
    assert tagged_detail["trip"] == {"id": trip["id"], "name": "Japan", "membership": "TAGGED"}
    excluded_detail = session.client.get(f"{API}/transactions/{member['id']}").json()
    assert excluded_detail["trip"] is None
    assert member["id"] not in all_transaction_ids(session, tripId=trip["id"])
    assert member["id"] in all_transaction_ids(session, tripId="none")

    assert change(session, "delete", f"{base}/excluded-transactions/{member['id']}") == 204
    assert change(session, "delete", f"{base}/tagged-transactions/{domestic['id']}") == 204
    restored = trip_stats(session, trip["id"])
    assert restored["stats"]["count"] == 317
    assert totals_by_currency(restored["stats"])["ILS"] == (ils_count, ils_net)
    assert restored["excluded"] == [] and restored["tagged"] == []


def test_the_analysis_and_the_list_group_by_trip_count_each_row_once(yearly: PgSession) -> None:
    session, headers = yearly, yearly.unsafe_headers
    first, last = japan_dates(session)
    trip = session.client.post(
        TRIPS,
        json={"name": "Japan", "currency": "JPY", "startDate": first, "endDate": last},
        headers=headers,
    ).json()
    validate_japan(session)
    stats = trip_stats(session, trip["id"])["stats"]
    count, net = totals_by_currency(stats)["ILS"]

    analysis = analyze_by_trip(session)
    groups = {group["key"]: group for group in analysis["groups"]}
    assert set(groups) == {trip["id"], None}
    assert groups[trip["id"]]["label"] == "Japan"
    assert groups[trip["id"]]["count"] == count
    assert Decimal(groups[trip["id"]]["net"]["amount"]) == net
    assert groups[None]["count"] == 2169 - count
    assert analysis["totals"]["count"] == 2169
    assert sum(g["count"] for g in groups.values()) == analysis["totals"]["count"]
    assert sum(Decimal(g["net"]["amount"]) for g in groups.values()) == Decimal(
        analysis["totals"]["net"]["amount"]
    )

    listed = session.client.get(
        f"{API}/transactions",
        params={"groupBy": "trip", "include": "summary,groups", "pageSize": 1},
    ).json()
    by_key = {group["key"]: group for group in listed["groups"]}
    assert set(by_key) == {trip["id"], None}
    assert by_key[trip["id"]]["label"] == "Japan"
    assert by_key[trip["id"]]["count"] == count
    assert totals_by_currency(by_key[trip["id"]])["ILS"] == (count, net)
    assert by_key[None]["count"] == 2169 - count
    assert listed["summary"]["count"] == 2169
