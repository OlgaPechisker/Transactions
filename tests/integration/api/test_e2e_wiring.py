"""Wiring check on the real Postgres-backed app: every operation of the contract is routed to a
working handler (no stub answers 501, 405 or 500) for a signed-in user."""

from typing import Any

import pytest
from tests.contracts.openapi_conformance import contract_operations
from tests.integration.api.conftest import PgSession

pytestmark = pytest.mark.postgres

MISSING_TRANSACTION = "txn_doesnotexist"
MISSING_TRIP = "trp_doesnotexist"
ANALYSIS = {"from": "2026-01-01", "to": "2026-12-31", "billingCurrency": "ILS"}
CALLS: dict[str, dict[str, Any]] = {
    "getMe": {},
    "listCards": {},
    "updateCard": {"json": {"nickname": "x"}},
    "listImports": {},
    "runImport": {"files": {"file": ("notes.txt", b"not a workbook")}},
    "listTransactions": {},
    "getTransaction": {},
    "updateTransaction": {"json": {"customCategory": None}},
    "getFilterOptions": {},
    "listTrips": {},
    "createTrip": {
        "json": {"name": "T", "currency": "EUR", "startDate": "2026-01-01", "endDate": "2026-01-02"}
    },
    "getTrip": {},
    "listTripCandidates": {},
    "tagTransaction": {},
    "untagTransaction": {},
    "excludeTransaction": {},
    "restoreTransaction": {},
    "getSpendingAnalysis": {"params": ANALYSIS},
    "getCurrencyValidationQueue": {},
    "listCurrencyValidationItems": {},
    "createCurrencyValidationBatch": {"json": {"currency": "USD", "filter": {}}},
    "undoCurrencyValidationBatch": {},
    "deleteCurrencyValidation": {},
    "getCategorization": {},
    "createCustomCategory": {"json": {"name": "Wiring"}},
    "putCategoryMapping": {"json": {"customCategory": "Wiring"}},
    "deleteCategoryMapping": {},
    "createCategoryRule": {"json": {"terms": ["x"], "category": "Wiring"}},
    "deleteCategoryRule": {},
    "deleteCurrentSession": {},
}
PLACEHOLDERS = {
    "{cardId}": "crd_doesnotexist",
    "{transactionId}": MISSING_TRANSACTION,
    "{tripId}": MISSING_TRIP,
    "{batchId}": "cvb_doesnotexist",
    "{ruleId}": "rul_doesnotexist",
    "{bankCategory}": "no-such-bank-category",
}


def url_of(path: str) -> str:
    for placeholder, value in PLACEHOLDERS.items():
        path = path.replace(placeholder, value)
    return path


def test_every_operation_of_the_contract_has_a_call_in_this_check() -> None:
    assert set(CALLS) | {"createSession"} == set(contract_operations())


@pytest.mark.parametrize("operation_id", sorted(CALLS))
def test_a_signed_in_user_gets_a_documented_answer_from_every_operation(
    pg_signed_in: PgSession, operation_id: str
) -> None:
    shape = contract_operations()[operation_id]
    call = dict(CALLS[operation_id])
    if shape.method != "GET":
        call["headers"] = pg_signed_in.unsafe_headers

    response = pg_signed_in.client.request(shape.method, url_of(shape.path), **call)

    documented = {shape.success_status, *shape.error_statuses}
    assert response.status_code not in (405, 500, 501)
    assert response.status_code in documented, response.text


def test_signing_in_is_wired_to_the_real_session_service(pg_client: Any) -> None:
    response = pg_client.post(
        "/api/v1/sessions", json={"email": "nobody@example.com", "password": "wrong"}
    )

    assert response.status_code == 401
