"""End to end on Postgres with the committed example workbooks: import, list, currency validation
and undo, with the /me counters following along."""

from decimal import Decimal

import pytest
from tests.integration.api.conftest import PgSession
from tests.integration.api.e2e_support import (
    API,
    CAL,
    ISRACARD,
    MAX_MONTHLY,
    MAX_YEARLY,
    counters,
    import_example,
    list_transactions,
    totals_by_currency,
)

pytestmark = pytest.mark.postgres

QUEUE = f"{API}/currency-validation-queue"
BATCHES = f"{API}/currency-validation-batches"


def pending_reasons(session: PgSession) -> int:
    return counters(session)["pendingCurrencyValidations"]


def test_three_examples_build_one_ledger_across_four_cards(pg_signed_in: PgSession) -> None:
    session = pg_signed_in
    isracard = import_example(session, ISRACARD)
    monthly = import_example(session, MAX_MONTHLY)
    cal = import_example(session, CAL)

    assert [r["counts"]["imported"] for r in (isracard, monthly, cal)] == [4, 158, 8]
    assert cal["counts"]["skipped"] == 13

    history = session.client.get(f"{API}/imports").json()["items"]
    assert sorted(item["rowsImported"] for item in history) == [4, 8, 158]

    body = list_transactions(session, pageSize=1)
    assert body["summary"]["count"] == 4 + 158 + 8
    assert totals_by_currency(body["summary"]) == {
        "ILS": (169, Decimal("22822.40")),
        "USD": (1, Decimal("26.39")),
    }
    assert body["summary"]["unknownCurrencyCount"] == 1

    options = session.client.get(f"{API}/filter-options").json()
    assert sorted(card["last4"] for card in options["cards"]) == ["0538", "2023", "4396", "9462"]
    assert options["dateRange"] == {"min": "2026-01-15", "max": "2026-08-25"}

    cards = session.client.get(f"{API}/cards").json()["items"]
    assert {card["issuer"] for card in cards} == {"ISRACARD", "MAX", "CAL"}

    assert counters(session) == {
        "pendingCurrencyValidations": 1,
        "needsReviewByReason": [{"reason": "UNKNOWN_CURRENCY", "count": 1}],
    }


def test_the_unknown_currency_of_the_monthly_export_is_validated_and_undone(
    pg_signed_in: PgSession,
) -> None:
    session, headers = pg_signed_in, pg_signed_in.unsafe_headers
    import_example(session, MAX_MONTHLY)
    assert session.client.get(QUEUE).json()["countries"] == [{"code": "GB", "count": 1}]

    batch = session.client.post(
        BATCHES, json={"currency": "GBP", "filter": {"country": ["GB"]}}, headers=headers
    )
    assert batch.status_code == 201
    created = batch.json()
    assert created["validatedCount"] == 1
    assert pending_reasons(session) == 0
    validated = list_transactions(session, currency="GBP")
    assert [item["id"] for item in validated["items"]] == created["transactionIds"]
    assert validated["items"][0]["reviewReasons"] == []
    assert list_transactions(session, currency="UNKNOWN")["summary"]["count"] == 0

    undone = session.client.delete(f"{BATCHES}/{created['id']}", headers=headers)
    assert undone.status_code == 204
    assert pending_reasons(session) == 1
    assert list_transactions(session, currency="GBP")["summary"]["count"] == 0
    assert list_transactions(session, currency="UNKNOWN")["summary"]["count"] == 1


def test_a_filter_batch_over_the_yearly_export_validates_every_matching_row_atomically(
    pg_signed_in: PgSession,
) -> None:
    session, headers = pg_signed_in, pg_signed_in.unsafe_headers
    yearly = import_example(session, MAX_YEARLY)
    assert yearly["counts"]["imported"] == 2169
    assert pending_reasons(session) == 374

    queue = session.client.get(QUEUE).json()
    countries = {entry["code"]: entry["count"] for entry in queue["countries"]}
    assert countries == {"DE": 1, "GB": 9, "JP": 317, "KZ": 1, "NL": 3, "TH": 25, "UA": 1}
    assert (queue["total"], queue["validated"], queue["pending"]) == (374, 0, 374)

    batch = session.client.post(
        BATCHES, json={"currency": "JPY", "filter": {"country": ["JP"]}}, headers=headers
    )
    assert batch.status_code == 201
    created = batch.json()
    assert created["validatedCount"] == 317
    assert len(created["transactionIds"]) == 317
    assert pending_reasons(session) == 374 - 317

    assert list_transactions(session, currency="JPY")["summary"]["count"] == 317
    queue = session.client.get(QUEUE).json()
    assert (queue["validated"], queue["pending"]) == (317, 57)
    items = session.client.get(
        f"{QUEUE}/items", params={"status": "VALIDATED", "pageSize": 200}
    ).json()
    assert items["meta"]["totalItems"] == 317
    assert {item["validation"]["currency"] for item in items["items"]} == {"JPY"}
    assert {item["validation"]["batchId"] for item in items["items"]} == {created["id"]}

    assert session.client.delete(f"{BATCHES}/{created['id']}", headers=headers).status_code == 204
    assert pending_reasons(session) == 374
    assert list_transactions(session, currency="JPY")["summary"]["count"] == 0
    assert session.client.get(QUEUE).json()["validated"] == 0
    again = session.client.delete(f"{BATCHES}/{created['id']}", headers=headers)
    assert again.status_code == 204


def test_a_validated_row_moves_back_to_the_queue_by_itself(pg_signed_in: PgSession) -> None:
    session, headers = pg_signed_in, pg_signed_in.unsafe_headers
    import_example(session, MAX_YEARLY)
    pending = session.client.get(f"{QUEUE}/items", params={"country": "KZ", "pageSize": 5}).json()[
        "items"
    ]
    assert len(pending) == 1
    transaction_id = pending[0]["id"]

    batch = session.client.post(
        BATCHES, json={"currency": "KZT", "transactionIds": [transaction_id]}, headers=headers
    )
    assert batch.status_code == 201
    assert pending_reasons(session) == 373
    repeat = session.client.post(
        BATCHES, json={"currency": "KZT", "transactionIds": [transaction_id]}, headers=headers
    )
    assert repeat.status_code == 409

    moved = session.client.delete(f"{API}/currency-validations/{transaction_id}", headers=headers)
    assert moved.status_code == 204
    assert pending_reasons(session) == 374
    stale_undo = session.client.delete(f"{BATCHES}/{batch.json()['id']}", headers=headers)
    assert stale_undo.status_code == 409
    assert stale_undo.json()["code"] == "BATCH_CHANGED"
    assert pending_reasons(session) == 374
