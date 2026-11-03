"""The trip endpoints against the real schema, in both auth modes. User A owns the data; user B is
a stranger who must never see or change any of it."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from tests.contracts.openapi_conformance import assert_operations_conform
from tests.integration.api.conftest import PgSession, PgSignIn
from tests.storage.seeds import create_claimed_card, create_transaction

from bank_transactions.api.app import create_app
from bank_transactions.domain.money import USD
from bank_transactions.persistence.commands import NewForeignDetail
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.services.composition import ApiComposition

TRIPS = "/api/v1/trips"
FOREIGN = {
    "original_amount": Decimal("10.00"),
    "original_currency": USD,
    "aggregate_foreign_detail": NewForeignDetail(Decimal("0.50"), Decimal("3.5"), Decimal("3.65")),
}
ROME = {"name": "Rome", "currency": "USD", "startDate": "2026-07-01", "endDate": "2026-07-09"}


@dataclass(frozen=True)
class Ledger:
    cafe: str
    museum: str
    pharmacy: str
    later: str


def ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["id"] for item in items]


def create_trip(session: PgSession, **fields: Any) -> dict[str, Any]:
    response = session.client.post(TRIPS, json=ROME | fields, headers=session.unsafe_headers)
    assert response.status_code == 201, response.text
    return response.json()


def tag(session: PgSession, trip: str, transaction: str) -> Any:
    url = f"{TRIPS}/{trip}/tagged-transactions/{transaction}"
    return session.client.put(url, headers=session.unsafe_headers)


def exclude(session: PgSession, trip: str, transaction: str) -> Any:
    url = f"{TRIPS}/{trip}/excluded-transactions/{transaction}"
    return session.client.put(url, headers=session.unsafe_headers)


def detail(session: PgSession, trip: str) -> dict[str, Any]:
    response = session.client.get(f"{TRIPS}/{trip}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def ledger(pg_signed_in: PgSession, factory: UnitOfWorkFactory, cipher: Any) -> Ledger:
    """Four transactions of user A: two foreign USD ones inside July 1-9, a domestic one inside
    the range and a foreign one outside it."""
    card = create_claimed_card(factory, cipher, pg_signed_in.user, last4="1111")
    seeded = [
        dict(
            FOREIGN,
            merchant_name_raw="Rome Cafe",
            merchant_name_norm="rome cafe",
            transaction_date=date(2026, 7, 3),
            billing_amount=Decimal("36.50"),
            bank_category_raw="Restaurants",
        ),
        dict(
            FOREIGN,
            merchant_name_raw="Vatican Museum",
            merchant_name_norm="vatican museum",
            transaction_date=date(2026, 7, 5),
            billing_amount=Decimal("20.00"),
        ),
        dict(
            merchant_name_raw="Super Pharm",
            merchant_name_norm="super pharm",
            transaction_date=date(2026, 7, 4),
            billing_amount=Decimal("100.00"),
            bank_category_raw="Pharmacy",
        ),
        dict(
            FOREIGN,
            merchant_name_raw="Mystery Shop",
            merchant_name_norm="mystery shop",
            transaction_date=date(2026, 8, 10),
            billing_amount=Decimal("11.00"),
        ),
    ]
    for fields in seeded:
        create_transaction(factory, card, **fields)
    items = pg_signed_in.client.get("/api/v1/transactions").json()["items"]
    by_merchant = {item["merchant"]: item["id"] for item in items}
    return Ledger(
        by_merchant["Rome Cafe"],
        by_merchant["Vatican Museum"],
        by_merchant["Super Pharm"],
        by_merchant["Mystery Shop"],
    )


def test_creating_a_trip_answers_201_with_its_location_and_an_empty_detail(pg_signed_in):
    response = pg_signed_in.client.post(TRIPS, json=ROME, headers=pg_signed_in.unsafe_headers)

    body = response.json()
    assert response.status_code == 201
    assert response.headers["location"].endswith(f"{TRIPS}/{body['id']}")
    assert body["id"].startswith("trp_")
    assert (body["name"], body["currency"], body["days"]) == ("Rome", "USD", 9)
    assert (body["startDate"], body["endDate"]) == ("2026-07-01", "2026-07-09")
    assert body["stats"]["count"] == 0
    assert body["stats"]["outsideRange"]["count"] == 0
    assert body["tagged"] == body["auto"] == body["excluded"] == []


def test_the_detail_lists_the_auto_members_with_the_totals_and_the_breakdowns(pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)

    body = detail(pg_signed_in, trip["id"])

    assert ids(body["auto"]) == [ledger.cafe, ledger.museum]
    assert body["tagged"] == [] and body["excluded"] == []
    assert body["stats"]["count"] == 2
    assert body["stats"]["billingTotals"][0]["net"] == {"amount": "56.50", "currency": "ILS"}
    assert [(c["category"], c["label"], c["count"]) for c in body["byCategory"]] == [
        ("Restaurants", "Restaurants", 1),
        (None, "Uncategorized", 1),
    ]
    assert [(d["date"], d["count"]) for d in body["byDay"]] == [
        ("2026-07-03", 1),
        ("2026-07-05", 1),
    ]
    assert body["auto"][0]["card"]["last4"] == "1111"


def test_the_list_gives_each_trip_its_counts_and_follows_the_requested_sort(pg_signed_in, ledger):
    rome = create_trip(pg_signed_in)
    autumn = create_trip(pg_signed_in, name="alps", startDate="2026-09-01", endDate="2026-09-05")
    tag(pg_signed_in, rome["id"], ledger.pharmacy)

    default = pg_signed_in.client.get(TRIPS)
    by_name = pg_signed_in.client.get(TRIPS, params={"sort": "name"})
    by_start = pg_signed_in.client.get(TRIPS, params={"sort": "startDate"})

    assert default.status_code == 200
    assert ids(default.json()["items"]) == [autumn["id"], rome["id"]]
    assert ids(by_name.json()["items"]) == [autumn["id"], rome["id"]]
    assert ids(by_start.json()["items"]) == [rome["id"], autumn["id"]]
    first = {item["id"]: item for item in default.json()["items"]}[rome["id"]]
    assert (first["days"], first["stats"]["taggedCount"], first["stats"]["autoCount"]) == (9, 1, 2)
    assert first["stats"]["count"] == 3
    assert first["stats"]["billingTotals"][0]["net"]["amount"] == "156.50"


@pytest.mark.parametrize("sort", ["amount", "name,startDate", "group:month"])
def test_an_unsupported_sort_is_a_400_problem(pg_signed_in, sort):
    response = pg_signed_in.client.get(TRIPS, params={"sort": sort})

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "INVALID_SORT"


def test_an_overlapping_trip_is_a_409_and_a_touching_bound_counts_as_overlap(pg_signed_in):
    create_trip(pg_signed_in)
    headers = pg_signed_in.unsafe_headers

    touching = pg_signed_in.client.post(
        TRIPS, json=ROME | {"startDate": "2026-07-09", "endDate": "2026-07-12"}, headers=headers
    )
    adjacent = pg_signed_in.client.post(
        TRIPS, json=ROME | {"startDate": "2026-07-10", "endDate": "2026-07-12"}, headers=headers
    )

    assert touching.status_code == 409
    assert touching.json()["code"] == "TRIP_DATE_OVERLAP"
    assert adjacent.status_code == 201
    assert len(pg_signed_in.client.get(TRIPS).json()["items"]) == 2


@pytest.mark.parametrize(
    ("fields", "code", "field"),
    [
        ({"name": ""}, "TOO_SHORT", "name"),
        ({"name": "   "}, "TOO_SHORT", "name"),
        ({"name": "x" * 81}, "TOO_LONG", "name"),
        ({"currency": "ZZZ"}, "INVALID_CURRENCY", "currency"),
        ({"endDate": "2026-06-30"}, "BEFORE_START_DATE", "endDate"),
    ],
    ids=["empty name", "blank name", "long name", "unknown currency", "end before start"],
)
def test_an_invalid_trip_is_a_422_problem_naming_the_field(pg_signed_in, fields, code, field):
    response = pg_signed_in.client.post(
        TRIPS, json=ROME | fields, headers=pg_signed_in.unsafe_headers
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["code"] == "VALIDATION_ERROR"
    assert (code, field) in {(e["code"], e["field"]) for e in problem["errors"]}
    assert pg_signed_in.client.get(TRIPS).json()["items"] == []


def test_tagging_puts_a_transaction_into_the_trip_and_untagging_takes_it_out(pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)
    url = f"{TRIPS}/{trip['id']}/tagged-transactions/{ledger.pharmacy}"

    tagged = pg_signed_in.client.put(url, headers=pg_signed_in.unsafe_headers)
    again = pg_signed_in.client.put(url, headers=pg_signed_in.unsafe_headers)
    after_tag = detail(pg_signed_in, trip["id"])
    untagged = pg_signed_in.client.delete(url, headers=pg_signed_in.unsafe_headers)
    after_untag = detail(pg_signed_in, trip["id"])

    assert (tagged.status_code, again.status_code, untagged.status_code) == (204, 204, 204)
    assert tagged.content == b""
    assert ids(after_tag["tagged"]) == [ledger.pharmacy]
    assert after_tag["stats"]["count"] == 3
    assert after_untag["tagged"] == []
    assert after_untag["stats"]["count"] == 2


def test_a_transaction_outside_the_range_is_counted_apart_when_tagged(pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)

    tag(pg_signed_in, trip["id"], ledger.later)

    stats = detail(pg_signed_in, trip["id"])["stats"]
    assert stats["outsideRange"]["count"] == 1
    assert stats["count"] == 3


def test_tagging_into_another_trip_moves_the_transaction_and_clears_that_exclusion(
    pg_signed_in, ledger
):
    first = create_trip(pg_signed_in)
    second = create_trip(pg_signed_in, name="Again", startDate="2026-07-20", endDate="2026-07-25")
    tag(pg_signed_in, first["id"], ledger.pharmacy)
    exclude(pg_signed_in, first["id"], ledger.cafe)

    moved = tag(pg_signed_in, second["id"], ledger.pharmacy)
    back = tag(pg_signed_in, first["id"], ledger.cafe)

    assert (moved.status_code, back.status_code) == (204, 204)
    after_first = detail(pg_signed_in, first["id"])
    assert ids(after_first["tagged"]) == [ledger.cafe]
    assert after_first["excluded"] == []
    assert ids(detail(pg_signed_in, second["id"])["tagged"]) == [ledger.pharmacy]


def test_excluding_an_auto_member_removes_it_and_restoring_brings_it_back(pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)
    url = f"{TRIPS}/{trip['id']}/excluded-transactions/{ledger.cafe}"

    excluded = pg_signed_in.client.put(url, headers=pg_signed_in.unsafe_headers)
    after_exclude = detail(pg_signed_in, trip["id"])
    restored = pg_signed_in.client.delete(url, headers=pg_signed_in.unsafe_headers)
    after_restore = detail(pg_signed_in, trip["id"])

    assert (excluded.status_code, restored.status_code) == (204, 204)
    assert ids(after_exclude["auto"]) == [ledger.museum]
    assert ids(after_exclude["excluded"]) == [ledger.cafe]
    assert after_exclude["stats"]["count"] == 1
    assert ids(after_restore["auto"]) == [ledger.cafe, ledger.museum]
    assert after_restore["excluded"] == []


@pytest.mark.parametrize("target", ["pharmacy", "later"], ids=["domestic", "outside the range"])
def test_excluding_a_transaction_that_is_not_an_auto_member_is_a_409(pg_signed_in, ledger, target):
    trip = create_trip(pg_signed_in)

    response = exclude(pg_signed_in, trip["id"], getattr(ledger, target))

    assert response.status_code == 409
    assert response.json()["code"] == "NOT_AUTO_MEMBER"
    assert detail(pg_signed_in, trip["id"])["excluded"] == []


def test_excluding_a_tagged_transaction_is_a_409(pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)
    tag(pg_signed_in, trip["id"], ledger.cafe)

    response = exclude(pg_signed_in, trip["id"], ledger.cafe)

    assert response.status_code == 409
    assert response.json()["code"] == "NOT_AUTO_MEMBER"


def test_the_candidates_are_the_transactions_around_the_trip_not_yet_in_it(pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)
    tag(pg_signed_in, trip["id"], ledger.pharmacy)

    response = pg_signed_in.client.get(f"{TRIPS}/{trip['id']}/candidates")

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_a_search_finds_candidates_anywhere_and_limit_caps_them(pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)
    client = pg_signed_in.client

    by_merchant = client.get(f"{TRIPS}/{trip['id']}/candidates", params={"q": "mystery"})
    by_date = client.get(f"{TRIPS}/{trip['id']}/candidates", params={"q": "2026-08"})
    by_category = client.get(f"{TRIPS}/{trip['id']}/candidates", params={"q": "pharm"})
    limited = client.get(f"{TRIPS}/{trip['id']}/candidates", params={"q": "s", "limit": 1})

    assert ids(by_merchant.json()["items"]) == [ledger.later]
    assert ids(by_date.json()["items"]) == [ledger.later]
    assert ids(by_category.json()["items"]) == [ledger.pharmacy]
    assert len(limited.json()["items"]) == 1


def test_an_excluded_transaction_is_a_candidate_again(pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)
    exclude(pg_signed_in, trip["id"], ledger.cafe)

    response = pg_signed_in.client.get(f"{TRIPS}/{trip['id']}/candidates", params={"q": "cafe"})

    assert ids(response.json()["items"]) == [ledger.cafe]


@pytest.mark.parametrize("limit", ["0", "51", "many"])
def test_a_malformed_candidate_limit_is_rejected(pg_signed_in, limit):
    trip = create_trip(pg_signed_in)

    response = pg_signed_in.client.get(f"{TRIPS}/{trip['id']}/candidates", params={"limit": limit})

    assert response.status_code in (400, 422)


def test_an_unknown_trip_or_transaction_is_a_404_problem(pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)
    client, headers = pg_signed_in.client, pg_signed_in.unsafe_headers

    responses = [
        client.get(f"{TRIPS}/trp_doesnotexist"),
        client.get(f"{TRIPS}/trp_doesnotexist/candidates"),
        tag(pg_signed_in, "trp_doesnotexist", ledger.cafe),
        tag(pg_signed_in, trip["id"], "txn_doesnotexist"),
        exclude(pg_signed_in, trip["id"], "txn_doesnotexist"),
        client.delete(f"{TRIPS}/{trip['id']}/tagged-transactions/txn_nope", headers=headers),
        client.delete(f"{TRIPS}/{trip['id']}/excluded-transactions/txn_nope", headers=headers),
    ]

    assert [r.status_code for r in responses] == [404] * len(responses)
    assert {r.json()["code"] for r in responses} == {"NOT_FOUND"}


def test_another_user_sees_none_of_the_trips_of_user_a(pg_sign_in: PgSignIn, pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)
    tag(pg_signed_in, trip["id"], ledger.pharmacy)
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")

    listing = stranger.client.get(TRIPS)
    one = stranger.client.get(f"{TRIPS}/{trip['id']}")
    candidates = stranger.client.get(f"{TRIPS}/{trip['id']}/candidates")

    assert listing.json()["items"] == []
    assert one.status_code == 404
    assert candidates.status_code == 404


def test_another_user_cannot_change_the_trips_or_use_the_transactions_of_user_a(
    pg_sign_in: PgSignIn, pg_signed_in, ledger, factory, cipher
):
    trip = create_trip(pg_signed_in)
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")
    own_card = create_claimed_card(factory, cipher, stranger.user, last4="9999")
    create_transaction(factory, own_card, merchant_name_raw="B Shop", merchant_name_norm="b shop")
    own = stranger.client.get("/api/v1/transactions").json()["items"][0]["id"]
    theirs = create_trip(stranger, name="Theirs")

    into_foreign_trip = tag(stranger, trip["id"], own)
    foreign_transaction = tag(stranger, theirs["id"], ledger.cafe)
    exclude_foreign = exclude(stranger, theirs["id"], ledger.cafe)
    untag_foreign = stranger.client.delete(
        f"{TRIPS}/{trip['id']}/tagged-transactions/{ledger.cafe}", headers=stranger.unsafe_headers
    )

    for response in (into_foreign_trip, foreign_transaction, exclude_foreign, untag_foreign):
        assert response.status_code == 404
    assert detail(pg_signed_in, trip["id"])["tagged"] == []
    assert detail(stranger, theirs["id"])["tagged"] == []


def test_two_users_may_have_overlapping_trips(pg_sign_in: PgSignIn, pg_signed_in):
    create_trip(pg_signed_in)
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")

    response = stranger.client.post(TRIPS, json=ROME, headers=stranger.unsafe_headers)

    assert response.status_code == 201


def test_the_transactions_of_a_trip_show_it_in_the_transaction_list(pg_signed_in, ledger):
    trip = create_trip(pg_signed_in)
    tag(pg_signed_in, trip["id"], ledger.pharmacy)

    response = pg_signed_in.client.get("/api/v1/transactions", params={"tripId": trip["id"]})

    assert ids(response.json()["items"]) == [ledger.museum, ledger.pharmacy, ledger.cafe]


def test_the_mutations_need_the_csrf_header_in_cookie_mode(pg_signed_in, ledger):
    if pg_signed_in.mode != "cookie":
        pytest.skip("bearer sessions carry no CSRF token")
    trip = create_trip(pg_signed_in)
    client = pg_signed_in.client
    base = f"{TRIPS}/{trip['id']}"

    responses = [
        client.post(TRIPS, json=ROME | {"name": "Other", "startDate": "2027-01-01"}),
        client.put(f"{base}/tagged-transactions/{ledger.cafe}"),
        client.delete(f"{base}/tagged-transactions/{ledger.cafe}"),
        client.put(f"{base}/excluded-transactions/{ledger.cafe}"),
        client.delete(f"{base}/excluded-transactions/{ledger.cafe}"),
    ]

    assert [r.status_code for r in responses] == [403] * len(responses)
    assert {r.json()["code"] for r in responses} == {"CSRF_VALIDATION_FAILED"}
    assert len(client.get(TRIPS).json()["items"]) == 1


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", TRIPS),
        ("POST", TRIPS),
        ("GET", f"{TRIPS}/trp_1"),
        ("GET", f"{TRIPS}/trp_1/candidates"),
        ("PUT", f"{TRIPS}/trp_1/tagged-transactions/txn_1"),
        ("DELETE", f"{TRIPS}/trp_1/tagged-transactions/txn_1"),
        ("PUT", f"{TRIPS}/trp_1/excluded-transactions/txn_1"),
        ("DELETE", f"{TRIPS}/trp_1/excluded-transactions/txn_1"),
    ],
)
def test_every_trip_endpoint_requires_a_session(pg_client, method, url):
    response = pg_client.request(method, url)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_the_owned_operations_conform_to_the_contract(composition: ApiComposition):
    assert_operations_conform(
        create_app(composition),
        "listTrips",
        "createTrip",
        "getTrip",
        "listTripCandidates",
        "tagTransaction",
        "untagTransaction",
        "excludeTransaction",
        "restoreTransaction",
    )
