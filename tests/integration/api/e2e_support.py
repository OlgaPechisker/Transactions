"""Shared pieces of the cross-slice end-to-end tests: the committed example workbooks (by exact
file name) and the calls every journey repeats."""

from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.integration.api.conftest import PgSession
from tests.integration.api.test_imports import import_for_real

API = "/api/v1"
EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
ISRACARD = "9462_02_2026.xlsx"
MAX_MONTHLY = "transaction-details_export_1785931743433.xlsx"
MAX_YEARLY = "transaction-details_export_1785938963329.xlsx"
CAL = "פירוט חיובים לכרטיס ויזה 0538 - 13.09.26 (1).xlsx"


def import_example(session: PgSession, file_name: str) -> dict[str, Any]:
    """Imports an example workbook for real, creating the cards it names; returns the 201 body."""
    return import_for_real(session, (EXAMPLES / file_name).read_bytes())


def totals_by_currency(summary: dict[str, Any]) -> dict[str, tuple[int, Decimal]]:
    """`{currency: (count, net)}` of a BillingSummary."""
    return {
        entry["net"]["currency"]: (entry["count"], Decimal(entry["net"]["amount"]))
        for entry in summary["billingTotals"]
    }


def list_transactions(session: PgSession, **params: Any) -> dict[str, Any]:
    response = session.client.get(f"{API}/transactions", params={"pageSize": 200} | params)
    assert response.status_code == 200, response.text
    return response.json()


def all_transaction_ids(session: PgSession, **params: Any) -> list[str]:
    """Every id matching the filter, following the pages."""
    ids: list[str] = []
    page = 1
    while True:
        body = list_transactions(session, page=page, **params)
        ids += [item["id"] for item in body["items"]]
        if page >= body["meta"]["totalPages"]:
            return ids
        page += 1


def counters(session: PgSession) -> dict[str, Any]:
    response = session.client.get(f"{API}/me")
    assert response.status_code == 200, response.text
    return response.json()["counters"]
