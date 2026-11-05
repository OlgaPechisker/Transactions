"""Currency validation over HTTP, against the real schema and authenticator, in both auth modes."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.contracts.openapi_conformance import assert_operations_conform
from tests.integration.api.conftest import PgSession
from tests.storage.seeds import create_claimed_card, create_transaction

from bank_transactions.domain.enums import ReviewReason
from bank_transactions.domain.money import USD
from bank_transactions.persistence.commands import NewForeignDetail

QUEUE = "/api/v1/currency-validation-queue"
ITEMS = f"{QUEUE}/items"
BATCHES = "/api/v1/currency-validation-batches"
VALIDATIONS = "/api/v1/currency-validations"


def pending(**overrides):
    return {
        "original_amount": Decimal("10.00"),
        "original_currency": None,
        "aggregate_review_reasons": {ReviewReason.UNKNOWN_CURRENCY},
        "aggregate_foreign_detail": NewForeignDetail(Decimal("0.50"), None, Decimal("3.65")),
        **overrides,
    }


@pytest.fixture
def seed_claimed_card(factory, cipher):
    return lambda user, **card: create_claimed_card(factory, cipher, user, **card)


@pytest.fixture
def seed_transaction(factory):
    return lambda card, **overrides: create_transaction(factory, card, **overrides)


@pytest.fixture
def shop(pg_signed_in: PgSession, seed_claimed_card, seed_transaction) -> PgSession:
    """The signed-in user has three pending foreign rows (IT, IT, FR), one foreign row with a
    known currency and one domestic row."""
    card = seed_claimed_card(pg_signed_in.user, last4="4580")

    seed_transaction(
        card,
        **pending(
            merchant_name_raw="Rome Cafe",
            merchant_country="IT",
            transaction_date=date(2026, 7, 5),
            original_amount=Decimal("40.00"),
            billing_amount=Decimal("140.00"),
        ),
    )
    seed_transaction(
        card,
        **pending(
            merchant_name_raw="Milan Cafe",
            merchant_country="IT",
            transaction_date=date(2026, 7, 20),
            original_amount=Decimal("25.00"),
            billing_amount=Decimal("90.00"),
        ),
    )
    seed_transaction(
        card,
        **pending(
            merchant_name_raw="Paris Bakery",
            merchant_country="FR",
            transaction_date=date(2026, 7, 9),
            original_amount=Decimal("10.00"),
            billing_amount=Decimal("37.00"),
        ),
    )
    seed_transaction(
        card,
        original_amount=Decimal("5.00"),
        original_currency=USD,
        aggregate_foreign_detail=NewForeignDetail(Decimal("0"), Decimal("3.5"), Decimal("3.5")),
    )
    seed_transaction(card)
    return pg_signed_in


def ids_by_merchant(session: PgSession, **params) -> dict[str, str]:
    response = session.client.get(ITEMS, params={"pageSize": 200, **params})
    assert response.status_code == 200
    return {item["merchant"]: item["id"] for item in response.json()["items"]}


def create_batch(session: PgSession, body: dict):
    return session.client.post(BATCHES, json=body, headers=session.unsafe_headers)


def pending_counter(session: PgSession) -> int:
    return session.client.get("/api/v1/me").json()["counters"]["pendingCurrencyValidations"]


def test_the_queue_reports_progress_countries_and_the_currency_catalogue(shop: PgSession):
    body = shop.client.get(QUEUE).json()

    assert (body["total"], body["validated"], body["pending"]) == (3, 0, 3)
    assert body["progressPercent"] == "0.0"
    assert body["countries"] == [{"code": "FR", "count": 1}, {"code": "IT", "count": 2}]
    codes = {option["code"] for option in body["currencyOptions"]}
    assert {"EUR", "USD", "ILS"} <= codes and not codes & {"XXX", "XTS"}
    assert {"code": "EUR", "name": "Euro"} in body["currencyOptions"]


def test_pending_items_show_the_amounts_the_rate_and_no_validation(shop: PgSession):
    items = shop.client.get(ITEMS).json()

    assert [item["merchant"] for item in items["items"]] == [
        "Milan Cafe",
        "Paris Bakery",
        "Rome Cafe",
    ]
    assert items["items"][2] == {
        "id": ids_by_merchant(shop)["Rome Cafe"],
        "transactionDate": "2026-07-05",
        "merchant": "Rome Cafe",
        "country": "IT",
        "originalAmount": "40.00",
        "billing": {"amount": "140.00", "currency": "ILS"},
        "impliedRate": "3.500000",
        "validation": None,
    }
    assert items["meta"] == {"page": 1, "pageSize": 40, "totalItems": 3, "totalPages": 1}


@pytest.mark.parametrize(
    ("params", "merchants"),
    [
        ({"country": "IT"}, ["Milan Cafe", "Rome Cafe"]),
        ({"from": "2026-07-06", "to": "2026-07-10"}, ["Paris Bakery"]),
        ({"merchant": "cafe", "sort": "originalAmount"}, ["Milan Cafe", "Rome Cafe"]),
        ({"sort": "-originalAmount"}, ["Rome Cafe", "Milan Cafe", "Paris Bakery"]),
        ({"sort": "merchant", "pageSize": 1, "page": 2}, ["Paris Bakery"]),
    ],
)
def test_items_filter_sort_and_page(shop: PgSession, params, merchants):
    body = shop.client.get(ITEMS, params=params).json()

    assert [item["merchant"] for item in body["items"]] == merchants


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"sort": "city"}, "INVALID_SORT"),
        ({"from": "2026-08-01", "to": "2026-07-01"}, "INVALID_FILTER"),
        ({"country": "italy"}, "INVALID_FILTER"),
        ({"pageSize": 500}, "INVALID_FILTER"),
    ],
)
def test_items_reject_invalid_parameters(shop: PgSession, params, code):
    response = shop.client.get(ITEMS, params=params)

    assert response.status_code == 400
    assert response.json()["code"] == code


def test_a_batch_by_ids_validates_them_and_the_counter_follows(shop: PgSession):
    ids = ids_by_merchant(shop)
    assert pending_counter(shop) == 3

    response = create_batch(
        shop,
        {
            "currency": "EUR",
            "transactionIds": [ids["Rome Cafe"], ids["Rome Cafe"], ids["Milan Cafe"]],
        },
    )

    body = response.json()
    assert response.status_code == 201
    assert response.headers["Location"] == f"{BATCHES}/{body['id']}"
    assert body["validatedCount"] == 2
    assert body["transactionIds"] == [ids["Rome Cafe"], ids["Milan Cafe"]]
    assert body["createdAt"].endswith("Z")
    assert pending_counter(shop) == 1
    assert shop.client.get("/api/v1/me").json()["counters"]["needsReviewByReason"] == [
        {"reason": "UNKNOWN_CURRENCY", "count": 1}
    ]
    queue = shop.client.get(QUEUE).json()
    assert (queue["total"], queue["validated"], queue["pending"]) == (3, 2, 1)
    assert queue["progressPercent"] == "66.7"
    assert queue["countries"] == [{"code": "FR", "count": 1}]
    detail = shop.client.get(f"/api/v1/transactions/{ids['Rome Cafe']}").json()
    assert detail["original"] == {"amount": "40.00", "currency": "EUR"}
    assert detail["reviewReasons"] == []


def test_validated_items_list_their_validation(shop: PgSession):
    ids = ids_by_merchant(shop)
    batch = create_batch(shop, {"currency": "EUR", "transactionIds": [ids["Rome Cafe"]]}).json()

    items = shop.client.get(ITEMS, params={"status": "VALIDATED"}).json()["items"]

    assert [item["merchant"] for item in items] == ["Rome Cafe"]
    assert items[0]["validation"] == {
        "currency": "EUR",
        "batchId": batch["id"],
        "validatedAt": batch["createdAt"],
    }
    assert "Rome Cafe" not in ids_by_merchant(shop)


def test_a_batch_by_filter_validates_every_matching_pending_row(shop: PgSession):
    response = create_batch(shop, {"currency": "EUR", "filter": {"country": ["IT"]}})

    assert response.status_code == 201
    assert response.json()["validatedCount"] == 2
    assert pending_counter(shop) == 1


def test_a_batch_by_a_filter_matching_nothing_is_created_empty(shop: PgSession):
    response = create_batch(shop, {"currency": "EUR", "filter": {"country": ["JP"]}})

    assert response.status_code == 201
    assert response.json()["validatedCount"] == 0
    assert response.json()["transactionIds"] == []
    assert pending_counter(shop) == 3


def test_undoing_a_batch_restores_the_queue_and_the_counter_and_repeats_quietly(
    shop: PgSession,
):
    ids = ids_by_merchant(shop)
    batch = create_batch(
        shop, {"currency": "EUR", "transactionIds": [ids["Rome Cafe"], ids["Paris Bakery"]]}
    ).json()
    assert pending_counter(shop) == 1

    first = shop.client.delete(f"{BATCHES}/{batch['id']}", headers=shop.unsafe_headers)
    again = shop.client.delete(f"{BATCHES}/{batch['id']}", headers=shop.unsafe_headers)

    assert (first.status_code, again.status_code) == (204, 204)
    assert pending_counter(shop) == 3
    assert ids_by_merchant(shop) == ids
    assert shop.client.get(QUEUE).json()["validated"] == 0


def test_moving_a_row_back_returns_it_to_the_queue_and_blocks_the_batch_undo(
    shop: PgSession,
):
    ids = ids_by_merchant(shop)
    batch = create_batch(
        shop, {"currency": "EUR", "transactionIds": [ids["Rome Cafe"], ids["Paris Bakery"]]}
    ).json()

    moved = shop.client.delete(f"{VALIDATIONS}/{ids['Rome Cafe']}", headers=shop.unsafe_headers)
    noop = shop.client.delete(f"{VALIDATIONS}/{ids['Rome Cafe']}", headers=shop.unsafe_headers)
    undo = shop.client.delete(f"{BATCHES}/{batch['id']}", headers=shop.unsafe_headers)

    assert (moved.status_code, noop.status_code) == (204, 204)
    assert pending_counter(shop) == 2
    assert undo.status_code == 409
    assert undo.json()["code"] == "BATCH_CHANGED"
    assert shop.client.get(QUEUE).json()["validated"] == 1


def test_validating_a_row_again_after_move_back_blocks_the_older_batch_undo(shop: PgSession):
    ids = ids_by_merchant(shop)
    old = create_batch(shop, {"currency": "EUR", "transactionIds": [ids["Rome Cafe"]]}).json()
    shop.client.delete(f"{VALIDATIONS}/{ids['Rome Cafe']}", headers=shop.unsafe_headers)
    create_batch(shop, {"currency": "USD", "transactionIds": [ids["Rome Cafe"]]})

    undo = shop.client.delete(f"{BATCHES}/{old['id']}", headers=shop.unsafe_headers)

    assert undo.status_code == 409
    assert undo.json()["code"] == "BATCH_CHANGED"
    items = shop.client.get(ITEMS, params={"status": "VALIDATED"}).json()["items"]
    assert items[0]["validation"]["currency"] == "USD"


def test_a_validated_row_cannot_be_validated_again(shop: PgSession):
    ids = ids_by_merchant(shop)
    create_batch(shop, {"currency": "EUR", "transactionIds": [ids["Rome Cafe"]]})

    response = create_batch(
        shop, {"currency": "USD", "transactionIds": [ids["Rome Cafe"], ids["Paris Bakery"]]}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_VALIDATED"
    assert pending_counter(shop) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"currency": "EUR"},
        {"currency": "EUR", "transactionIds": ["x"], "filter": {}},
        {"currency": "ZZZ", "filter": {}},
        {"currency": "eur", "filter": {}},
        {"currency": "EUR", "transactionIds": []},
        {"currency": "EUR", "filter": {"from": "2026-08-01", "to": "2026-07-01"}},
    ],
)
def test_an_invalid_batch_request_is_a_422_and_changes_nothing(shop: PgSession, body):
    response = create_batch(shop, body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert pending_counter(shop) == 3


def test_a_batch_request_with_an_unknown_key_is_rejected(shop: PgSession):
    top = create_batch(shop, {"currency": "EUR", "filter": {}, "dryRun": True})
    nested = create_batch(shop, {"currency": "EUR", "filter": {"city": "Rome"}})

    for response in (top, nested):
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "UNKNOWN_FIELD"
    assert pending_counter(shop) == 3


def test_one_unknown_id_fails_the_whole_batch_with_404(shop: PgSession):
    ids = ids_by_merchant(shop)

    response = create_batch(
        shop,
        {"currency": "EUR", "transactionIds": [ids["Rome Cafe"], "txn_aaaaaaaaaaaaaaaaaaaaaaaaaa"]},
    )

    assert response.status_code == 404
    assert pending_counter(shop) == 3


def test_a_visible_transaction_that_is_not_pending_fails_the_batch_with_422(shop: PgSession):
    pending_ids = ids_by_merchant(shop)
    listing = shop.client.get("/api/v1/transactions", params={"pageSize": 100}).json()["items"]
    (domestic_id,) = [
        item["id"]
        for item in listing
        if item["id"] not in pending_ids.values() and item["original"] is None
    ]

    response = create_batch(
        shop, {"currency": "EUR", "transactionIds": [pending_ids["Rome Cafe"], domestic_id]}
    )

    assert response.status_code == 422
    assert "NOT_PENDING" in [error["code"] for error in response.json()["errors"]]
    assert pending_counter(shop) == 3


def test_a_user_cannot_validate_undo_or_move_back_the_rows_of_another_user(
    shop: PgSession, pg_sign_in, seed_claimed_card, seed_transaction
):
    ids = ids_by_merchant(shop)
    batch = create_batch(shop, {"currency": "EUR", "transactionIds": [ids["Rome Cafe"]]}).json()
    other = pg_sign_in("user.b@example.com", "User B", shop.mode)
    card = seed_claimed_card(other.user, last4="9999", account_number="1234567")
    seed_transaction(card, **pending(merchant_name_raw="Ben Only"))

    own_queue = other.client.get(QUEUE).json()
    take = create_batch(other, {"currency": "USD", "transactionIds": [ids["Paris Bakery"]]})
    undo = other.client.delete(f"{BATCHES}/{batch['id']}", headers=other.unsafe_headers)
    move = other.client.delete(f"{VALIDATIONS}/{ids['Rome Cafe']}", headers=other.unsafe_headers)
    sweep = create_batch(other, {"currency": "USD", "filter": {}})

    assert (own_queue["total"], own_queue["pending"]) == (1, 1)
    assert (take.status_code, undo.status_code, move.status_code) == (404, 404, 404)
    assert sweep.json()["validatedCount"] == 1
    assert [i["merchant"] for i in other.client.get(ITEMS).json()["items"]] == []
    assert shop.client.get(QUEUE).json()["validated"] == 1
    assert pending_counter(shop) == 2


def test_moving_back_an_unknown_row_or_undoing_an_unknown_batch_is_404(shop: PgSession):
    move = shop.client.delete(
        f"{VALIDATIONS}/txn_aaaaaaaaaaaaaaaaaaaaaaaaaa", headers=shop.unsafe_headers
    )
    undo = shop.client.delete(
        f"{BATCHES}/cvb_aaaaaaaaaaaaaaaaaaaaaaaaaa", headers=shop.unsafe_headers
    )

    assert (move.status_code, undo.status_code) == (404, 404)


@pytest.mark.parametrize("auth_mode", ["cookie"])
def test_a_cookie_mutation_needs_the_csrf_header(shop: PgSession):
    ids = ids_by_merchant(shop)
    attempts = [
        shop.client.post(BATCHES, json={"currency": "EUR", "filter": {}}),
        shop.client.delete(f"{BATCHES}/cvb_aaaaaaaaaaaaaaaaaaaaaaaaaa"),
        shop.client.delete(f"{VALIDATIONS}/{ids['Rome Cafe']}"),
    ]

    assert [a.status_code for a in attempts] == [403, 403, 403]
    assert {a.json()["code"] for a in attempts} == {"CSRF_VALIDATION_FAILED"}
    assert pending_counter(shop) == 3


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", QUEUE),
        ("get", ITEMS),
        ("post", BATCHES),
        ("delete", f"{BATCHES}/cvb_aaaaaaaaaaaaaaaaaaaaaaaaaa"),
        ("delete", f"{VALIDATIONS}/txn_aaaaaaaaaaaaaaaaaaaaaaaaaa"),
    ],
)
def test_every_operation_needs_a_session(pg_client: TestClient, method, path):
    response = getattr(pg_client, method)(path)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_the_operations_match_the_contract(pg_app: FastAPI):
    assert_operations_conform(
        pg_app,
        "getCurrencyValidationQueue",
        "listCurrencyValidationItems",
        "createCurrencyValidationBatch",
        "undoCurrencyValidationBatch",
        "deleteCurrencyValidation",
    )
