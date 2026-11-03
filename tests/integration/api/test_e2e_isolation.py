"""End to end on Postgres: two users who each imported their own workbook. Every route that takes
an id of the other user answers 404, writes nothing, and no list, option set or analysis of one
user shows the other's data."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import pytest
from tests.contracts.openapi_conformance import contract_operations
from tests.integration.api.conftest import PgSession, PgSignIn
from tests.integration.api.e2e_support import (
    API,
    CAL,
    EXAMPLES,
    ISRACARD,
    MAX_MONTHLY,
    counters,
    import_example,
    list_transactions,
    totals_by_currency,
)
from tests.integration.api.test_imports import upload

pytestmark = pytest.mark.postgres

OWNER_BANK_CATEGORY = "רפואה ובתי מרקחת"
FOOD_BANK_CATEGORY = "מזון וצריכה"
WHOLE_YEAR = {"from": "2026-01-01", "to": "2026-12-31", "billingCurrency": "ILS"}


@dataclass(frozen=True)
class Owner:
    """User A with one of everything: cards, transactions, a validation batch, a trip, a custom
    category with a mapping, a rule and an override."""

    session: PgSession
    card_id: str
    overridden_id: str
    pending_id: str
    trip_id: str
    batch_id: str
    rule_id: str


def send(session: PgSession, method: str, path: str, body: Any = None, **params: Any) -> Any:
    kwargs: dict[str, Any] = {"params": params}
    if method != "get":
        kwargs["headers"] = session.unsafe_headers
    if body is not None:
        kwargs["json"] = body
    return getattr(session.client, method)(f"{API}{path}", **kwargs)


def snapshot(owner: Owner) -> dict[str, Any]:
    """Everything the owner can observe, to prove another user's calls changed nothing."""
    session = owner.session
    listing = list_transactions(session)
    return {
        "transactions": listing["items"],
        "summary": listing["summary"],
        "counters": counters(session),
        "options": send(session, "get", "/filter-options").json(),
        "categorization": send(session, "get", "/categorization").json(),
        "trips": send(session, "get", "/trips").json(),
        "trip": send(session, "get", f"/trips/{owner.trip_id}").json(),
        "queue": send(session, "get", "/currency-validation-queue").json(),
        "validated": send(
            session, "get", "/currency-validation-queue/items", status="VALIDATED"
        ).json(),
        "cards": send(session, "get", "/cards").json(),
        "imports": send(session, "get", "/imports").json(),
        "analysis": send(session, "get", "/spending-analysis", **WHOLE_YEAR).json(),
    }


@pytest.fixture
def owner(pg_signed_in: PgSession) -> Owner:
    session = pg_signed_in
    import_example(session, ISRACARD)
    import_example(session, MAX_MONTHLY)
    card_id = send(session, "get", "/cards").json()["items"][0]["id"]

    pending = send(session, "get", "/currency-validation-queue/items").json()["items"][0]
    batch = send(
        session,
        "post",
        "/currency-validation-batches",
        {"currency": "GBP", "transactionIds": [pending["id"]]},
    )
    assert batch.status_code == 201
    day = pending["transactionDate"]
    trip = send(
        session,
        "post",
        "/trips",
        {"name": "Britain", "currency": "GBP", "startDate": day, "endDate": day},
    )
    assert trip.status_code == 201

    category = "Owner Only"
    assert send(session, "post", "/custom-categories", {"name": category}).status_code == 201
    mapping = send(
        session,
        "put",
        f"/category-mappings/{quote(OWNER_BANK_CATEGORY, safe='')}",
        {"customCategory": category},
    )
    assert mapping.status_code == 200
    rule = send(session, "post", "/category-rules", {"terms": ["super"], "category": category})
    assert rule.status_code == 201
    food = list_transactions(session, bankCategory=FOOD_BANK_CATEGORY, pageSize=1)["items"][0]
    overridden = send(session, "patch", f"/transactions/{food['id']}", {"customCategory": category})
    assert overridden.status_code == 200
    return Owner(
        session,
        card_id,
        food["id"],
        pending["id"],
        trip.json()["id"],
        batch.json()["id"],
        rule.json()["id"],
    )


@pytest.fixture
def stranger(owner: Owner, pg_sign_in: PgSignIn) -> PgSession:
    """User B, who imported a different workbook of their own."""
    other = pg_sign_in("user.b@example.com", "User B", owner.session.mode)
    import_example(other, CAL)
    return other


def routes_with_the_owners_ids(
    owner: Owner, mine: str, my_trip: str
) -> dict[str, tuple[str, str, Any]]:
    """Every route that takes an id, called with ids of the owner (alone or mixed with the
    caller's own): `{name: (method, path, body)}`."""
    theirs, pending, trip = owner.overridden_id, owner.pending_id, owner.trip_id
    bank = quote(OWNER_BANK_CATEGORY, safe="")
    return {
        "updateCard": ("patch", f"/cards/{owner.card_id}", {"nickname": "stolen"}),
        "getTransaction": ("get", f"/transactions/{theirs}", None),
        "updateTransaction": ("patch", f"/transactions/{theirs}", {"customCategory": None}),
        "getTrip": ("get", f"/trips/{trip}", None),
        "listTripCandidates": ("get", f"/trips/{trip}/candidates", None),
        "tagTransaction (their trip, their transaction)": (
            "put",
            f"/trips/{trip}/tagged-transactions/{theirs}",
            None,
        ),
        "untagTransaction (their trip, their transaction)": (
            "delete",
            f"/trips/{trip}/tagged-transactions/{theirs}",
            None,
        ),
        "excludeTransaction (their trip, their transaction)": (
            "put",
            f"/trips/{trip}/excluded-transactions/{pending}",
            None,
        ),
        "restoreTransaction (their trip, their transaction)": (
            "delete",
            f"/trips/{trip}/excluded-transactions/{pending}",
            None,
        ),
        "tagTransaction (my trip, their transaction)": (
            "put",
            f"/trips/{my_trip}/tagged-transactions/{theirs}",
            None,
        ),
        "untagTransaction (my trip, their transaction)": (
            "delete",
            f"/trips/{my_trip}/tagged-transactions/{theirs}",
            None,
        ),
        "excludeTransaction (my trip, their transaction)": (
            "put",
            f"/trips/{my_trip}/excluded-transactions/{pending}",
            None,
        ),
        "restoreTransaction (my trip, their transaction)": (
            "delete",
            f"/trips/{my_trip}/excluded-transactions/{pending}",
            None,
        ),
        "tagTransaction (their trip, my transaction)": (
            "put",
            f"/trips/{trip}/tagged-transactions/{mine}",
            None,
        ),
        "untagTransaction (their trip, my transaction)": (
            "delete",
            f"/trips/{trip}/tagged-transactions/{mine}",
            None,
        ),
        "excludeTransaction (their trip, my transaction)": (
            "put",
            f"/trips/{trip}/excluded-transactions/{mine}",
            None,
        ),
        "restoreTransaction (their trip, my transaction)": (
            "delete",
            f"/trips/{trip}/excluded-transactions/{mine}",
            None,
        ),
        "createCurrencyValidationBatch (their transaction)": (
            "post",
            "/currency-validation-batches",
            {"currency": "USD", "transactionIds": [pending]},
        ),
        "createCurrencyValidationBatch (mine and theirs)": (
            "post",
            "/currency-validation-batches",
            {"currency": "USD", "transactionIds": [mine, pending]},
        ),
        "undoCurrencyValidationBatch": (
            "delete",
            f"/currency-validation-batches/{owner.batch_id}",
            None,
        ),
        "deleteCurrencyValidation": ("delete", f"/currency-validations/{pending}", None),
        "putCategoryMapping (their bank category)": (
            "put",
            f"/category-mappings/{bank}",
            {"customCategory": "Mine"},
        ),
        "deleteCategoryMapping (their bank category)": (
            "delete",
            f"/category-mappings/{bank}",
            None,
        ),
        "deleteCategoryRule": ("delete", f"/category-rules/{owner.rule_id}", None),
    }


def test_every_route_with_the_owners_id_is_a_404_for_another_user_and_changes_nothing(
    owner: Owner, stranger: PgSession
) -> None:
    before = snapshot(owner)
    mine = list_transactions(stranger)["items"][0]["id"]
    created = send(
        stranger,
        "post",
        "/trips",
        {"name": "Mine", "currency": "EUR", "startDate": "2026-05-01", "endDate": "2026-05-31"},
    )
    assert created.status_code == 201
    assert send(stranger, "post", "/custom-categories", {"name": "Mine"}).status_code == 201

    routes = routes_with_the_owners_ids(owner, mine, created.json()["id"])
    outcomes = {
        name: send(stranger, method, path, body).status_code
        for name, (method, path, body) in routes.items()
    }

    assert outcomes == {name: 404 for name in routes}
    covered = {name.split()[0] for name in routes}
    with_ids = {op for op, shape in contract_operations().items() if "{" in shape.path}
    assert with_ids <= covered, f"routes with ids not swept: {sorted(with_ids - covered)}"
    assert snapshot(owner) == before


def test_selecting_the_owners_card_in_an_import_is_a_404(
    owner: Owner, pg_sign_in: PgSignIn
) -> None:
    newcomer = pg_sign_in("user.c@example.com", "User C", owner.session.mode)
    workbook = (EXAMPLES / CAL).read_bytes()
    asked = upload(newcomer, workbook, dry_run=True).json()
    selections = [
        {
            "questionId": question["id"],
            "action": "USE_EXISTING_CARD",
            "cardId": owner.card_id,
        }
        for question in asked["identityQuestions"]
    ]
    assert selections

    response = upload(newcomer, workbook, dry_run=False, selections=selections)

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert send(newcomer, "get", "/imports").json()["items"] == []


def test_the_owners_category_names_are_unknown_to_another_user(
    owner: Owner, stranger: PgSession
) -> None:
    mine = list_transactions(stranger)["items"][0]["id"]
    name = "Owner Only"

    overridden = send(stranger, "patch", f"/transactions/{mine}", {"customCategory": name})
    ruled = send(stranger, "post", "/category-rules", {"terms": ["x"], "category": name})
    cal_bank = send(stranger, "get", "/categorization").json()["bankCategories"][0]["name"]
    mapped = send(
        stranger,
        "put",
        f"/category-mappings/{quote(cal_bank, safe='')}",
        {"customCategory": name},
    )

    for response in (overridden, ruled, mapped):
        assert response.status_code == 422
    assert "UNKNOWN_CATEGORY" in [error["code"] for error in overridden.json()["errors"]]
    assert send(stranger, "post", "/custom-categories", {"name": name}).status_code == 201


def test_the_lists_options_and_analysis_of_another_user_hold_only_their_own_data(
    owner: Owner, stranger: PgSession
) -> None:
    mine = list_transactions(stranger)
    assert mine["summary"]["count"] == 8
    assert totals_by_currency(mine["summary"]) == {"ILS": (8, Decimal("645.13"))}
    assert {item["card"]["last4"] for item in mine["items"]} == {"0538"}
    owners_ids = {t["id"] for t in list_transactions(owner.session)["items"]}
    assert not owners_ids & {item["id"] for item in mine["items"]}

    options = send(stranger, "get", "/filter-options").json()
    assert [card["last4"] for card in options["cards"]] == ["0538"]
    assert options["dateRange"] == {"min": "2026-05-11", "max": "2026-08-25"}
    assert options["trips"] == [] and options["customCategories"] == []
    assert options["unknownCurrencyCount"] == 0
    assert sum(entry["count"] for entry in options["categories"]) == 8
    assert {entry["bank"] for entry in options["categories"]}.isdisjoint(
        {OWNER_BANK_CATEGORY, FOOD_BANK_CATEGORY}
    )

    assert len(send(stranger, "get", "/cards").json()["items"]) == 1
    imports = send(stranger, "get", "/imports").json()["items"]
    assert [item["rowsImported"] for item in imports] == [8]
    assert send(stranger, "get", "/trips").json()["items"] == []
    overview = send(stranger, "get", "/categorization").json()
    assert overview["rules"] == [] and overview["customCategories"] == []
    assert sum(entry["count"] for entry in overview["bankCategories"]) == 8

    queue = send(stranger, "get", "/currency-validation-queue").json()
    assert (queue["total"], queue["pending"], queue["validated"]) == (0, 0, 0)
    assert send(stranger, "get", "/currency-validation-queue/items").json()["items"] == []
    assert counters(stranger) == {"pendingCurrencyValidations": 0, "needsReviewByReason": []}

    analysis = send(stranger, "get", "/spending-analysis", **WHOLE_YEAR).json()
    assert analysis["totals"]["count"] == 8
    assert Decimal(analysis["totals"]["net"]["amount"]) == Decimal("645.13")
    owners_analysis = send(owner.session, "get", "/spending-analysis", **WHOLE_YEAR).json()
    assert owners_analysis["totals"]["count"] == 3 + 158  # the ILS rows; one Isracard row is USD
    assert list_transactions(owner.session)["summary"]["count"] == 4 + 158


def test_filtering_by_ids_of_another_user_matches_nothing(
    owner: Owner, stranger: PgSession
) -> None:
    by_card = list_transactions(stranger, cardId=owner.card_id)
    by_trip = list_transactions(stranger, tripId=owner.trip_id)
    by_merchant = list_transactions(stranger, merchant="סופר פארם")

    assert [by_card["summary"]["count"], by_trip["summary"]["count"]] == [0, 0]
    assert by_merchant["summary"]["count"] == 0
    in_analysis = send(
        stranger, "get", "/spending-analysis", cardId=owner.card_id, **WHOLE_YEAR
    ).json()
    assert in_analysis["totals"]["count"] == 0
