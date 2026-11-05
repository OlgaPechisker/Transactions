"""End to end on Postgres with the monthly Max export: the category layers (override, newest
matching rule, bank-category mapping, bank category) as the transaction list, the detail, the
categorization overview and the spending analysis see them."""

from decimal import Decimal
from typing import Any
from urllib.parse import quote

import pytest
from tests.integration.api.conftest import PgSession
from tests.integration.api.e2e_support import (
    API,
    MAX_MONTHLY,
    all_transaction_ids,
    import_example,
    list_transactions,
)

pytestmark = pytest.mark.postgres

PHARMACY_BANK = "רפואה ובתי מרקחת"
SUPER_PHARM = "סופר פארם"
FIRST_DAY, LAST_DAY = "2026-02-10", "2026-07-08"


@pytest.fixture
def monthly(pg_signed_in: PgSession) -> PgSession:
    import_example(pg_signed_in, MAX_MONTHLY)
    return pg_signed_in


def send(session: PgSession, method: str, path: str, body: Any = None) -> Any:
    kwargs: dict[str, Any] = {"headers": session.unsafe_headers}
    if body is not None:
        kwargs["json"] = body
    return getattr(session.client, method)(f"{API}{path}", **kwargs)


def create_category(session: PgSession, name: str) -> None:
    assert send(session, "post", "/custom-categories", {"name": name}).status_code == 201


def create_rule(session: PgSession, term: str, category: str) -> str:
    response = send(session, "post", "/category-rules", {"terms": [term], "category": category})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def map_bank_category(session: PgSession, bank: str, custom: str) -> None:
    response = send(
        session, "put", f"/category-mappings/{quote(bank, safe='')}", {"customCategory": custom}
    )
    assert response.status_code == 200, response.text


def analysis_groups(session: PgSession) -> tuple[dict[str, tuple[int, Decimal]], dict[str, Any]]:
    """`{group label or key: (count, net)}` of the analysis grouped by category, and the body."""
    response = session.client.get(
        f"{API}/spending-analysis",
        params={
            "from": FIRST_DAY,
            "to": LAST_DAY,
            "billingCurrency": "ILS",
            "groupBy": "category",
            "include": "groups",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    groups = {g["key"]: (g["count"], Decimal(g["net"]["amount"])) for g in body["groups"]}
    assert sum(count for count, _ in groups.values()) == body["totals"]["count"]
    assert sum(net for _, net in groups.values()) == Decimal(body["totals"]["net"]["amount"])
    return groups, body


def categories_of(session: PgSession, **filters: Any) -> set[tuple[str, str]]:
    items = list_transactions(session, **filters)["items"]
    return {(item["category"]["effective"], item["category"]["source"]) for item in items}


def test_each_layer_of_the_category_precedence_shows_in_the_list_and_the_analysis(
    monthly: PgSession,
) -> None:
    session = monthly
    baseline, body = analysis_groups(session)
    assert body["totals"]["count"] == 158
    pharmacy_count, pharmacy_net = baseline[PHARMACY_BANK]
    assert pharmacy_count == 20
    assert categories_of(session, bankCategory=PHARMACY_BANK) == {(PHARMACY_BANK, "BANK")}

    for name in ("Health", "Pharmacy", "Treats", "Snack"):
        create_category(session, name)

    # Mapping: the whole bank category becomes the custom category.
    map_bank_category(session, PHARMACY_BANK, "Health")
    mapped, _ = analysis_groups(session)
    assert PHARMACY_BANK not in mapped
    assert mapped["Health"] == (pharmacy_count, pharmacy_net)
    assert categories_of(session, bankCategory=PHARMACY_BANK) == {("Health", "MAPPING")}
    assert list_transactions(session, categorySource="MAPPING")["summary"]["count"] == 20

    # Rule: beats the mapping for the rows whose merchant contains the term.
    matches = list_transactions(session, merchant=SUPER_PHARM)
    matched = matches["summary"]["count"]
    assert matched > 0
    first_rule = create_rule(session, SUPER_PHARM, "Pharmacy")
    ruled, _ = analysis_groups(session)
    assert ruled["Pharmacy"][0] == matched
    assert ruled["Health"][0] == pharmacy_count - sum(
        1 for item in matches["items"] if item["category"]["bank"] == PHARMACY_BANK
    )
    assert categories_of(session, categorySource="RULE") == {("Pharmacy", "RULE")}

    # The newest matching rule wins; deleting it brings the older one back.
    newest_rule = create_rule(session, SUPER_PHARM.upper(), "Treats")
    newest, _ = analysis_groups(session)
    assert "Pharmacy" not in newest
    assert newest["Treats"] == ruled["Pharmacy"]
    overview = send(session, "get", "/categorization").json()
    assert [rule["id"] for rule in overview["rules"]] == [newest_rule, first_rule]
    assert {rule["stats"]["matched"] for rule in overview["rules"]} == {matched}

    # Override: beats the rule for one row only.
    row = matches["items"][0]
    overridden = send(session, "patch", f"/transactions/{row['id']}", {"customCategory": "Snack"})
    assert overridden.status_code == 200
    assert overridden.json()["category"] == {
        "bank": row["category"]["bank"],
        "custom": "Snack",
        "effective": "Snack",
        "source": "OVERRIDE",
    }
    layered, _ = analysis_groups(session)
    assert layered["Snack"][0] == 1
    assert layered["Treats"][0] == matched - 1
    assert all_transaction_ids(session, categorySource="OVERRIDE") == [row["id"]]
    assert all_transaction_ids(session, category="Snack") == [row["id"]]

    # Clearing the override and deleting the rules unwinds one layer at a time.
    assert (
        send(session, "patch", f"/transactions/{row['id']}", {"customCategory": None}).status_code
        == 200
    )
    assert analysis_groups(session)[0]["Treats"] == newest["Treats"]
    assert send(session, "delete", f"/category-rules/{newest_rule}").status_code == 204
    assert analysis_groups(session)[0]["Pharmacy"] == ruled["Pharmacy"]
    assert send(session, "delete", f"/category-rules/{first_rule}").status_code == 204
    assert analysis_groups(session)[0] == mapped
    assert (
        send(session, "delete", f"/category-mappings/{quote(PHARMACY_BANK, safe='')}").status_code
        == 204
    )
    assert analysis_groups(session)[0] == baseline
    assert categories_of(session, bankCategory=PHARMACY_BANK) == {(PHARMACY_BANK, "BANK")}


def test_the_categorization_overview_counts_follow_the_layers(monthly: PgSession) -> None:
    session = monthly
    overview = send(session, "get", "/categorization").json()
    bank = {entry["name"]: entry["count"] for entry in overview["bankCategories"]}
    assert bank[PHARMACY_BANK] == 20
    assert bank["מזון וצריכה"] == 70
    assert sum(bank.values()) == 158
    assert overview["rules"] == [] and overview["customCategories"] == []

    create_category(session, "Groceries")
    map_bank_category(session, "מזון וצריכה", "Groceries")
    overview = send(session, "get", "/categorization").json()
    mapped = {entry["name"]: entry["mappedTo"] for entry in overview["bankCategories"]}
    assert mapped["מזון וצריכה"] == "Groceries" and mapped[PHARMACY_BANK] is None
    resulting = {entry["name"]: entry["count"] for entry in overview["resultingCategories"]}
    assert resulting["Groceries"] == 70
    assert "מזון וצריכה" not in resulting
    assert resulting[PHARMACY_BANK] == 20
    assert list_transactions(session, customCategory="Groceries")["summary"]["count"] == 70
    assert list_transactions(session, customCategory="none")["summary"]["count"] == 158 - 70
