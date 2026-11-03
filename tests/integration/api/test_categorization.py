"""Categorization endpoints against the real schema, in both auth modes. User A owns the data;
user B is a stranger who must never see or change any of it."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.contracts.openapi_conformance import assert_operations_conform
from tests.integration.api.conftest import PgSession, PgSignIn
from tests.storage.seeds import create_claimed_card, create_transaction

from bank_transactions.api.app import create_app
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.services.composition import ApiComposition

OVERVIEW = "/api/v1/categorization"
CATEGORIES = "/api/v1/custom-categories"
MAPPINGS = "/api/v1/category-mappings"
RULES = "/api/v1/category-rules"
TRANSACTIONS = "/api/v1/transactions"


@dataclass(frozen=True)
class Ledger:
    pharmacy: str
    kiosk: str


@pytest.fixture
def ledger(pg_signed_in: PgSession, factory: UnitOfWorkFactory, cipher: Any, sql: Any) -> Ledger:
    """Four transactions of user A: two pharmacies, a kiosk and one whose bank category contains a
    slash."""
    card = create_claimed_card(factory, cipher, pg_signed_in.user, last4="1111")
    seeded = [
        ("Super Pharm", "super pharm", "Pharmacy"),
        ("Be Pharm", "be pharm", "Pharmacy"),
        ("Corner Kiosk", "corner kiosk", None),
        ("Bus Co", "bus co", "Bus/Train"),
    ]
    for raw, norm, bank in seeded:
        create_transaction(
            factory,
            card,
            merchant_name_raw=raw,
            merchant_name_norm=norm,
            bank_category_raw=bank,
            transaction_date=date(2026, 7, 3),
            billing_amount=Decimal("10.00"),
        )
    public = {
        row[0]: row[1] for row in sql("SELECT merchant_name_raw, public_id FROM transactions")
    }
    return Ledger(public["Super Pharm"], public["Corner Kiosk"])


def create_category(session: PgSession, name: str) -> Any:
    return session.client.post(CATEGORIES, json={"name": name}, headers=session.unsafe_headers)


# --- overview -------------------------------------------------------------------------------------


def test_a_new_user_has_an_empty_overview(pg_signed_in: PgSession):
    response = pg_signed_in.client.get(OVERVIEW)

    assert response.status_code == 200
    assert response.json() == {
        "bankCategories": [],
        "rules": [],
        "customCategories": [],
        "resultingCategories": [],
    }


def test_the_overview_shows_bank_categories_mappings_rules_and_the_resulting_counts(
    pg_signed_in: PgSession, ledger
):
    session = pg_signed_in
    create_category(session, "Health")
    create_category(session, "Snacks")
    session.client.put(
        f"{MAPPINGS}/Pharmacy", json={"customCategory": "health"}, headers=session.unsafe_headers
    )
    session.client.post(
        RULES,
        json={"terms": ["Kiosk"], "category": "Snacks"},
        headers=session.unsafe_headers,
    )

    body = session.client.get(OVERVIEW).json()

    assert body["bankCategories"] == [
        {"name": "Bus/Train", "count": 1, "mappedTo": None},
        {"name": "Pharmacy", "count": 2, "mappedTo": "Health"},
    ]
    assert body["customCategories"] == [{"name": "Health"}, {"name": "Snacks"}]
    (rule,) = body["rules"]
    assert rule["terms"] == ["kiosk"] and rule["category"] == "Snacks"
    assert rule["stats"] == {"matched": 1} and rule["id"].startswith("rul_")
    assert sorted(body["resultingCategories"], key=lambda c: c["name"]) == [
        {"name": "Bus/Train", "count": 1},
        {"name": "Health", "count": 2},
        {"name": "Snacks", "count": 1},
    ]


# --- custom categories ----------------------------------------------------------------------------


def test_a_custom_category_is_created_trimmed_and_listed(pg_signed_in: PgSession):
    response = create_category(pg_signed_in, "  Groceries ")

    assert response.status_code == 201
    assert response.json() == {"name": "Groceries"}
    assert pg_signed_in.client.get(OVERVIEW).json()["customCategories"] == [{"name": "Groceries"}]


@pytest.mark.parametrize("again", ["groceries", "GROCERIES", " Groceries "])
def test_a_duplicate_name_in_any_casing_is_a_409(pg_signed_in: PgSession, again):
    create_category(pg_signed_in, "Groceries")

    response = create_category(pg_signed_in, again)

    assert response.status_code == 409
    assert response.json()["code"] == "CATEGORY_EXISTS"


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("none", "RESERVED_NAME"),
        ("NONE", "RESERVED_NAME"),
        ("x" * 61, "TOO_LONG"),
        ("", "TOO_SHORT"),
    ],
)
def test_an_invalid_name_is_a_422_naming_the_field(pg_signed_in: PgSession, name, code):
    response = create_category(pg_signed_in, name)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert [(e["field"], e["code"]) for e in response.json()["errors"]] == [("name", code)]


def test_a_blank_name_after_trimming_is_a_422(pg_signed_in: PgSession):
    response = create_category(pg_signed_in, "   ")

    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "TOO_SHORT"


def test_two_users_can_each_have_a_category_with_the_same_name(
    pg_signed_in: PgSession, pg_sign_in: PgSignIn
):
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")
    create_category(pg_signed_in, "Groceries")

    response = create_category(stranger, "groceries")

    assert response.status_code == 201
    assert stranger.client.get(OVERVIEW).json()["customCategories"] == [{"name": "groceries"}]


# --- mappings -------------------------------------------------------------------------------------


def test_a_bank_category_is_mapped_replaced_and_unmapped(pg_signed_in: PgSession, ledger):
    session = pg_signed_in
    create_category(session, "Health")
    create_category(session, "Care")

    first = session.client.put(
        f"{MAPPINGS}/Pharmacy", json={"customCategory": "HEALTH"}, headers=session.unsafe_headers
    )
    second = session.client.put(
        f"{MAPPINGS}/Pharmacy", json={"customCategory": "Care"}, headers=session.unsafe_headers
    )
    mapped = session.client.get(OVERVIEW).json()["bankCategories"]
    deleted = session.client.delete(f"{MAPPINGS}/Pharmacy", headers=session.unsafe_headers)
    unmapped = session.client.get(OVERVIEW).json()["bankCategories"]

    assert first.status_code == 200
    assert first.json() == {"bankCategory": "Pharmacy", "customCategory": "Health"}
    assert second.json() == {"bankCategory": "Pharmacy", "customCategory": "Care"}
    assert {c["name"]: c["mappedTo"] for c in mapped}["Pharmacy"] == "Care"
    assert deleted.status_code == 204 and deleted.content == b""
    assert {c["name"]: c["mappedTo"] for c in unmapped}["Pharmacy"] is None


def test_a_bank_category_with_a_slash_or_a_space_can_be_mapped(pg_signed_in: PgSession, ledger):
    session = pg_signed_in
    create_category(session, "Transit")

    response = session.client.put(
        f"{MAPPINGS}/Bus%2FTrain",
        json={"customCategory": "Transit"},
        headers=session.unsafe_headers,
    )
    raw_slash = session.client.put(
        f"{MAPPINGS}/Bus/Train", json={"customCategory": "Transit"}, headers=session.unsafe_headers
    )

    assert response.status_code == 200
    assert response.json()["bankCategory"] == "Bus/Train"
    assert raw_slash.status_code == 200
    assert raw_slash.json()["bankCategory"] == "Bus/Train"


def test_mapping_an_unknown_bank_category_is_a_404_and_an_unknown_target_a_422(
    pg_signed_in: PgSession, ledger
):
    session = pg_signed_in
    create_category(session, "Health")

    missing = session.client.put(
        f"{MAPPINGS}/Gambling", json={"customCategory": "Health"}, headers=session.unsafe_headers
    )
    unknown = session.client.put(
        f"{MAPPINGS}/Pharmacy", json={"customCategory": "Nowhere"}, headers=session.unsafe_headers
    )

    assert missing.status_code == 404
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["field"] == "customCategory"
    assert unknown.json()["errors"][0]["code"] == "UNKNOWN_CATEGORY"


def test_unmapping_a_bank_category_without_a_mapping_is_a_404(pg_signed_in: PgSession, ledger):
    response = pg_signed_in.client.delete(
        f"{MAPPINGS}/Pharmacy", headers=pg_signed_in.unsafe_headers
    )

    assert response.status_code == 404


# --- rules ----------------------------------------------------------------------------------------


def test_a_rule_is_created_with_normalised_terms_counted_and_deleted(
    pg_signed_in: PgSession, ledger
):
    session = pg_signed_in
    create_category(session, "Health")

    created = session.client.post(
        RULES,
        json={"terms": ["  PHARM ", "pharm", "zzz"], "category": "health"},
        headers=session.unsafe_headers,
    )
    deleted = session.client.delete(
        f"{RULES}/{created.json()['id']}", headers=session.unsafe_headers
    )

    assert created.status_code == 201
    assert created.json()["terms"] == ["pharm", "zzz"]
    assert created.json()["category"] == "Health"
    assert created.json()["stats"] == {"matched": 2}
    assert deleted.status_code == 204
    assert session.client.get(OVERVIEW).json()["rules"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"terms": [], "category": "Health"},
        {"terms": [""], "category": "Health"},
        {"terms": ["  "], "category": "Health"},
        {"terms": ["pharm"], "category": "Nowhere"},
        {"terms": ["pharm"]},
        {"terms": ["pharm"], "category": "Health", "extra": 1},
    ],
    ids=["no terms", "empty term", "blank term", "unknown category", "no category", "extra field"],
)
def test_an_invalid_rule_is_a_422(pg_signed_in: PgSession, body):
    create_category(pg_signed_in, "Health")

    response = pg_signed_in.client.post(RULES, json=body, headers=pg_signed_in.unsafe_headers)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert pg_signed_in.client.get(OVERVIEW).json()["rules"] == []


def test_deleting_an_unknown_rule_is_a_404(pg_signed_in: PgSession):
    response = pg_signed_in.client.delete(f"{RULES}/rul_nope", headers=pg_signed_in.unsafe_headers)

    assert response.status_code == 404


# --- PATCH /transactions/{id} ---------------------------------------------------------------------


def test_an_override_sets_the_category_and_null_clears_it(pg_signed_in: PgSession, ledger):
    session = pg_signed_in
    create_category(session, "Health")
    url = f"{TRANSACTIONS}/{ledger.kiosk}"

    set_ = session.client.patch(
        url, json={"customCategory": "HEALTH"}, headers=session.unsafe_headers
    )
    cleared = session.client.patch(
        url, json={"customCategory": None}, headers=session.unsafe_headers
    )

    assert set_.status_code == 200
    assert set_.json()["id"] == ledger.kiosk
    assert set_.json()["category"] == {
        "bank": None,
        "custom": "Health",
        "effective": "Health",
        "source": "OVERRIDE",
    }
    assert cleared.status_code == 200
    assert cleared.json()["category"]["custom"] is None
    assert cleared.json()["category"]["effective"] is None


def test_an_override_beats_a_rule_and_a_mapping_and_clearing_it_restores_them(
    pg_signed_in: PgSession, ledger
):
    session = pg_signed_in
    for name in ("Health", "Care", "Special"):
        create_category(session, name)
    session.client.put(
        f"{MAPPINGS}/Pharmacy", json={"customCategory": "Health"}, headers=session.unsafe_headers
    )
    session.client.post(
        RULES, json={"terms": ["super"], "category": "Care"}, headers=session.unsafe_headers
    )
    url = f"{TRANSACTIONS}/{ledger.pharmacy}"

    overridden = session.client.patch(
        url, json={"customCategory": "Special"}, headers=session.unsafe_headers
    )
    restored = session.client.patch(
        url, json={"customCategory": None}, headers=session.unsafe_headers
    )

    assert overridden.json()["category"]["source"] == "OVERRIDE"
    assert overridden.json()["category"]["effective"] == "Special"
    assert restored.json()["category"]["source"] == "RULE"
    assert restored.json()["category"]["effective"] == "Care"


def test_an_override_is_reflected_in_the_resulting_counts(pg_signed_in: PgSession, ledger):
    session = pg_signed_in
    create_category(session, "Health")
    session.client.patch(
        f"{TRANSACTIONS}/{ledger.kiosk}",
        json={"customCategory": "Health"},
        headers=session.unsafe_headers,
    )

    results = session.client.get(OVERVIEW).json()["resultingCategories"]

    assert {"name": "Health", "count": 1} in results


@pytest.mark.parametrize(
    ("body", "status"),
    [({"customCategory": "Nowhere"}, 422), ({"customCategory": ""}, 422), ({}, 422)],
    ids=["unknown category", "empty category", "missing field"],
)
def test_an_invalid_override_is_a_422(pg_signed_in: PgSession, ledger, body, status):
    response = pg_signed_in.client.patch(
        f"{TRANSACTIONS}/{ledger.kiosk}", json=body, headers=pg_signed_in.unsafe_headers
    )

    assert response.status_code == status


def test_an_unknown_transaction_is_a_404(pg_signed_in: PgSession, ledger):
    create_category(pg_signed_in, "Health")

    response = pg_signed_in.client.patch(
        f"{TRANSACTIONS}/txn_nope",
        json={"customCategory": "Health"},
        headers=pg_signed_in.unsafe_headers,
    )

    assert response.status_code == 404


# --- isolation ------------------------------------------------------------------------------------


def test_another_user_cannot_see_or_change_the_categorization_of_user_a(
    pg_signed_in: PgSession, pg_sign_in: PgSignIn, ledger
):
    owner = pg_signed_in
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")
    create_category(owner, "Health")
    owner.client.put(
        f"{MAPPINGS}/Pharmacy", json={"customCategory": "Health"}, headers=owner.unsafe_headers
    )
    rule = owner.client.post(
        RULES, json={"terms": ["super"], "category": "Health"}, headers=owner.unsafe_headers
    ).json()
    create_category(stranger, "Mine")

    overview = stranger.client.get(OVERVIEW).json()
    patched = stranger.client.patch(
        f"{TRANSACTIONS}/{ledger.pharmacy}",
        json={"customCategory": "Mine"},
        headers=stranger.unsafe_headers,
    )
    unmapped = stranger.client.delete(f"{MAPPINGS}/Pharmacy", headers=stranger.unsafe_headers)
    mapped = stranger.client.put(
        f"{MAPPINGS}/Pharmacy", json={"customCategory": "Mine"}, headers=stranger.unsafe_headers
    )
    borrowed = stranger.client.post(
        RULES, json={"terms": ["super"], "category": "Health"}, headers=stranger.unsafe_headers
    )
    removed = stranger.client.delete(f"{RULES}/{rule['id']}", headers=stranger.unsafe_headers)

    assert overview == {
        "bankCategories": [],
        "rules": [],
        "customCategories": [{"name": "Mine"}],
        "resultingCategories": [],
    }
    assert patched.status_code == 404
    assert unmapped.status_code == 404
    assert mapped.status_code == 404
    assert borrowed.status_code == 422
    assert removed.status_code == 404
    still = owner.client.get(OVERVIEW).json()
    assert [r["id"] for r in still["rules"]] == [rule["id"]]
    assert {c["name"]: c["mappedTo"] for c in still["bankCategories"]}["Pharmacy"] == "Health"


def test_another_users_override_does_not_touch_user_a_transaction(
    pg_signed_in: PgSession, ledger, pg_sign_in: PgSignIn
):
    owner = pg_signed_in
    create_category(owner, "Health")
    owner.client.patch(
        f"{TRANSACTIONS}/{ledger.pharmacy}",
        json={"customCategory": "Health"},
        headers=owner.unsafe_headers,
    )
    stranger = pg_sign_in(email="user.b@example.com", display_name="User B")
    create_category(stranger, "Health")

    stranger.client.patch(
        f"{TRANSACTIONS}/{ledger.pharmacy}",
        json={"customCategory": "Health"},
        headers=stranger.unsafe_headers,
    )

    results = owner.client.get(OVERVIEW).json()["resultingCategories"]
    assert {"name": "Health", "count": 1} in results


# --- sessions and CSRF ----------------------------------------------------------------------------


@pytest.mark.parametrize("auth_mode", ["cookie"])
@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("post", CATEGORIES, {"name": "Health"}),
        ("put", f"{MAPPINGS}/Pharmacy", {"customCategory": "Health"}),
        ("delete", f"{MAPPINGS}/Pharmacy", None),
        ("post", RULES, {"terms": ["a"], "category": "Health"}),
        ("delete", f"{RULES}/rul_1", None),
        ("patch", f"{TRANSACTIONS}/txn_1", {"customCategory": None}),
    ],
    ids=["create category", "put mapping", "delete mapping", "create rule", "delete rule", "patch"],
)
def test_a_cookie_mutation_without_the_csrf_token_is_a_403(
    pg_signed_in: PgSession, ledger, method, url, body
):
    response = pg_signed_in.client.request(method, url, json=body)

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_VALIDATION_FAILED"


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", OVERVIEW),
        ("post", CATEGORIES),
        ("put", f"{MAPPINGS}/Pharmacy"),
        ("delete", f"{MAPPINGS}/Pharmacy"),
        ("post", RULES),
        ("delete", f"{RULES}/rul_1"),
        ("patch", f"{TRANSACTIONS}/txn_1"),
    ],
)
def test_every_operation_requires_a_session(pg_client: TestClient, method, url):
    response = pg_client.request(method, url)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_the_owned_operations_conform_to_the_contract(composition: ApiComposition):
    assert_operations_conform(
        create_app(composition),
        "getCategorization",
        "createCustomCategory",
        "putCategoryMapping",
        "deleteCategoryMapping",
        "createCategoryRule",
        "deleteCategoryRule",
        "updateTransaction",
    )
