"""The API foundation as an HTTP client sees it: authentication, CSRF, problem bodies, query and
body validation, shared DTO formatting and the contract's operation list."""

import base64
import io
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel
from tests.contracts.openapi_conformance import (
    assert_operations_conform,
    contract_operations,
    operation_mismatches,
)
from tests.integration.api.conftest import CIPHER_KEY, IssueSession

from bank_transactions.api.app import create_app
from bank_transactions.api.dependencies import (
    CsrfUser,
    CurrentUser,
    FilterParams,
    Paging,
    single_sort_param,
    sort_param,
)
from bank_transactions.api.schemas.common import ApiModel, TransactionListItemDto
from bank_transactions.api.schemas.mappers import (
    to_billing_summary,
    to_page_meta,
    to_transaction_detail,
    to_transaction_list_item,
)
from bank_transactions.cli import main
from bank_transactions.domain.enums import (
    CategorySource,
    IssuerCode,
    ReviewReason,
    TransactionType,
    TripMembership,
)
from bank_transactions.domain.errors import (
    AuthenticationError,
    ConflictError,
    CsrfError,
    FileTooLargeError,
    InvalidQueryError,
    NotFoundError,
    PersistenceError,
    RateLimitedError,
    UnknownFormatError,
    UnreadableWorkbookError,
    UnsupportedFileError,
    ValidationFailedError,
)
from bank_transactions.domain.ids import CardId, IdentifierToken, PublicId, TransactionId
from bank_transactions.domain.queries import Page, SortSpec
from bank_transactions.services.card_service import CardView
from bank_transactions.services.read_models import (
    BillingSummaryRecord,
    BillingTotalRecord,
    TransactionViewRecord,
    TripMembershipRef,
    VisibleCardRecord,
)

PROBLEM_JSON = "application/problem+json"
SESSION_CREATION = "createSession"
MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")


# --- test-only endpoints, mounted next to the real ones -----------------------------------------


class ProbeBody(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=10)
    line_items: list["ProbeLine"]


class ProbeLine(ApiModel):
    unit_price: int = Field(ge=0)


ProbeBody.model_rebuild()

CARD = CardView(PublicId("card-1"), "Max ••1234", IssuerCode.MAX, "1234", None, "Dana")


def card_record() -> VisibleCardRecord:
    return VisibleCardRecord(
        CardId(1), PublicId("card-1"), IssuerCode.MAX, IdentifierToken(b"x"), None, "Dana"
    )


def transaction_record(**overrides: object) -> TransactionViewRecord:
    fields: dict[str, object] = {
        "transaction_id": TransactionId(1),
        "public_id": PublicId("tx-1"),
        "transaction_date": date(2024, 3, 5),
        "charge_date": None,
        "merchant": "Corner Shop",
        "city": None,
        "country": None,
        "bank_category": None,
        "custom_category": None,
        "effective_category": None,
        "category_source": CategorySource.BANK,
        "card": card_record(),
        "transaction_type": TransactionType.REGULAR,
        "installment_number": None,
        "installment_total": None,
        "original_amount": None,
        "original_currency": None,
        "billing_amount": Decimal("-12.3"),
        "billing_currency": "ILS",
        "effective_rate": None,
        "commission": None,
        "trip": None,
        "notes": None,
    }
    fields.update(overrides)
    return TransactionViewRecord(**fields)  # type: ignore[arg-type]


def present_card(_: VisibleCardRecord) -> CardView:
    return CARD


ERRORS = {
    "validation": lambda: ValidationFailedError.single("name", "REQUIRED", "A name is required."),
    "invalid-filter": lambda: InvalidQueryError(InvalidQueryError.INVALID_FILTER, "Bad filter."),
    "invalid-sort": lambda: InvalidQueryError(InvalidQueryError.INVALID_SORT, "Bad sort."),
    "unauthenticated": lambda: AuthenticationError("Authentication is required"),
    "invalid-credentials": lambda: AuthenticationError(
        "Email or password is wrong", code=AuthenticationError.INVALID_CREDENTIALS
    ),
    "csrf": lambda: CsrfError("Missing or invalid CSRF token"),
    "not-found": lambda: NotFoundError("Not found"),
    "conflict": lambda: ConflictError(ConflictError.TRIP_DATE_OVERLAP, "Trips overlap."),
    "too-large": lambda: FileTooLargeError("The file is too large."),
    "unsupported": lambda: UnsupportedFileError("Only .xlsx files are supported."),
    "unreadable": lambda: UnreadableWorkbookError("The workbook cannot be read."),
    "unknown-format": lambda: UnknownFormatError("No parser recognised the file."),
    "rate-limited": lambda: RateLimitedError(30, "Slow down."),
    "persistence": lambda: PersistenceError("connection to host=db password=hunter2 failed"),
    "crash": lambda: RuntimeError("secret /srv/data/42.xlsx"),
}

probe = APIRouter(prefix="/probe")


@probe.get("/errors/{kind}")
def raise_error(kind: str) -> None:
    raise ERRORS[kind]()


@probe.get("/not-implemented")
def not_implemented() -> None:
    raise NotImplementedError


@probe.get("/whoami")
def whoami(user: CurrentUser) -> dict[str, int]:
    return {"userId": user.user_id}


@probe.post("/mutate")
def mutate(user: CsrfUser) -> dict[str, int]:
    return {"userId": user.user_id}


@probe.post("/body")
def body(payload: ProbeBody) -> dict[str, str]:
    return {"name": payload.name}


@probe.get("/page")
def page(
    paging: Paging,
    sort: Annotated[SortSpec, Depends(sort_param(("date", "amount"), "-date"))],
) -> dict[str, object]:
    return {"page": paging.page, "size": paging.size, "sort": [list(f) for f in sort.fields]}


@probe.get("/single-sort")
def single_sort(
    sort: Annotated[SortSpec, Depends(single_sort_param(("startedAt", "status"), "-startedAt"))],
) -> dict[str, object]:
    return {"sort": [list(f) for f in sort.fields]}


@probe.get("/filter")
def filter_echo(parsed: FilterParams) -> dict[str, object]:
    return {
        "from": parsed.date_from,
        "to": parsed.date_to,
        "currencies": parsed.currencies,
        "minAmount": None if parsed.min_amount is None else str(parsed.min_amount),
        "needsReview": parsed.needs_review,
        "tripIds": parsed.trip_ids,
    }


@probe.get("/transaction", response_model=TransactionListItemDto)
def transaction(kind: str = "full") -> TransactionListItemDto:
    if kind == "minimal":
        return to_transaction_list_item(transaction_record(), present_card)
    return to_transaction_list_item(
        transaction_record(
            charge_date=date(2024, 4, 2),
            city="Berlin",
            country="DE",
            bank_category="Food",
            effective_category="Food",
            transaction_type=TransactionType.INSTALLMENTS,
            installment_number=2,
            installment_total=6,
            original_amount=Decimal("10.005"),
            original_currency=None,
            billing_amount=Decimal("-1234.5"),
            effective_rate=Decimal("-3.7123456"),
            commission=Decimal("0.4"),
            trip=TripMembershipRef(PublicId("trip-1"), "Berlin", TripMembership.AUTO),
            notes="Gift",
            visible_review_reasons=(ReviewReason.UNKNOWN_CURRENCY,),
        ),
        present_card,
    )


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    app.include_router(probe, prefix="/api/v1")
    return app


def problem(response) -> dict:
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    return response.json()


# --- authentication ------------------------------------------------------------------------------


def test_a_bearer_token_authenticates(client: TestClient, issue_session: IssueSession):
    session = issue_session()

    response = client.get(
        "/api/v1/probe/whoami", headers={"Authorization": f"Bearer {session.token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"userId": session.user.user_id}


def test_the_session_cookie_authenticates(client: TestClient, issue_session: IssueSession):
    session = issue_session()
    client.cookies.set("bt_session", session.token)

    response = client.get("/api/v1/probe/whoami")

    assert response.json() == {"userId": session.user.user_id}


def test_a_bearer_header_takes_precedence_over_the_cookie(
    client: TestClient, issue_session: IssueSession
):
    cookie_user, bearer_user = issue_session(), issue_session()
    client.cookies.set("bt_session", cookie_user.token)

    response = client.get(
        "/api/v1/probe/whoami", headers={"Authorization": f"Bearer {bearer_user.token}"}
    )

    assert response.json() == {"userId": bearer_user.user.user_id}


def test_an_invalid_bearer_token_does_not_fall_back_to_a_valid_cookie(
    client: TestClient, issue_session: IssueSession
):
    client.cookies.set("bt_session", issue_session().token)

    response = client.get("/api/v1/probe/whoami", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401
    assert problem(response)["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer   "])
def test_a_malformed_authorization_header_is_rejected_without_cookie_fallback(
    client: TestClient, issue_session: IssueSession, header: str
):
    client.cookies.set("bt_session", issue_session().token)

    response = client.get("/api/v1/probe/whoami", headers={"Authorization": header})

    assert response.status_code == 401


def test_an_unknown_cookie_is_unauthenticated(client: TestClient):
    client.cookies.set("bt_session", "unknown")

    assert client.get("/api/v1/probe/whoami").status_code == 401


def _contract_requests(*, mutating: bool) -> list[tuple[str, str, str]]:
    return [
        (name, shape.method, re.sub(r"\{[^}]+\}", "x", shape.path))
        for name, shape in contract_operations().items()
        if name != SESSION_CREATION and (shape.method in MUTATING_METHODS) == mutating
    ]


@pytest.mark.parametrize(
    ("name", "method", "url"),
    [pytest.param(*row, id=row[0]) for row in _contract_requests(mutating=False)]
    + [pytest.param(*row, id=row[0]) for row in _contract_requests(mutating=True)],
)
def test_every_operation_but_sign_in_rejects_a_request_without_credentials(
    client: TestClient, name: str, method: str, url: str
):
    response = client.request(method, url)

    assert response.status_code == 401
    assert problem(response)["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    ("name", "method", "url"),
    [pytest.param(*row, id=row[0]) for row in _contract_requests(mutating=True)],
)
def test_every_mutating_operation_but_sign_in_requires_csrf_for_cookie_sessions(
    client: TestClient, issue_session: IssueSession, name: str, method: str, url: str
):
    session = issue_session()
    client.cookies.set("bt_session", session.token)

    missing = client.request(method, url)
    wrong = client.request(method, url, headers={"X-CSRF-Token": session.csrf + "x"})

    assert (missing.status_code, wrong.status_code) == (403, 403)
    assert problem(missing)["code"] == "CSRF_VALIDATION_FAILED"
    assert problem(wrong)["code"] == "CSRF_VALIDATION_FAILED"


def test_sign_in_needs_neither_credentials_nor_csrf(client: TestClient):
    response = client.post("/api/v1/sessions", json={})

    assert response.status_code not in (401, 403)


@pytest.mark.parametrize("auth_mode", ["cookie"])
def test_a_cookie_mutation_needs_the_matching_csrf_token(signed_in):
    client, user, csrf = signed_in

    assert client.post("/api/v1/probe/mutate").status_code == 403
    assert client.post("/api/v1/probe/mutate", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    allowed = client.post("/api/v1/probe/mutate", headers={"X-CSRF-Token": csrf})
    assert allowed.status_code == 200
    assert allowed.json() == {"userId": user.user_id}


@pytest.mark.parametrize("auth_mode", ["bearer"])
def test_a_bearer_mutation_needs_no_csrf_token(signed_in):
    client, user, _ = signed_in

    response = client.post("/api/v1/probe/mutate")

    assert response.status_code == 200
    assert response.json() == {"userId": user.user_id}


def test_a_cookie_read_needs_no_csrf_token(client: TestClient, issue_session: IssueSession):
    client.cookies.set("bt_session", issue_session().token)

    assert client.get("/api/v1/probe/whoami").status_code == 200


def test_a_signed_in_client_reaches_the_api_in_both_modes(signed_in):
    client, user, _ = signed_in

    assert client.get("/api/v1/probe/whoami").json() == {"userId": user.user_id}


# --- problem bodies -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "status", "code"),
    [
        ("validation", 422, "VALIDATION_ERROR"),
        ("invalid-filter", 400, "INVALID_FILTER"),
        ("invalid-sort", 400, "INVALID_SORT"),
        ("unauthenticated", 401, "UNAUTHENTICATED"),
        ("invalid-credentials", 401, "INVALID_CREDENTIALS"),
        ("csrf", 403, "CSRF_VALIDATION_FAILED"),
        ("not-found", 404, "NOT_FOUND"),
        ("conflict", 409, "TRIP_DATE_OVERLAP"),
        ("too-large", 413, "FILE_TOO_LARGE"),
        ("unsupported", 415, "UNSUPPORTED_FILE_TYPE"),
        ("unreadable", 422, "UNREADABLE_WORKBOOK"),
        ("unknown-format", 422, "UNKNOWN_STATEMENT_FORMAT"),
        ("rate-limited", 429, "RATE_LIMITED"),
        ("persistence", 500, "INTERNAL_ERROR"),
        ("crash", 500, "INTERNAL_ERROR"),
    ],
)
def test_each_error_becomes_a_problem_with_its_status_and_code(
    client: TestClient, kind: str, status: int, code: str
):
    response = client.get(f"/api/v1/probe/errors/{kind}")

    body = problem(response)
    assert response.status_code == status
    assert body["status"] == status
    assert body["code"] == code
    assert body["instance"] == f"/api/v1/probe/errors/{kind}"
    assert body["type"] == "https://api.example/problems/" + code.lower().replace("_", "-")
    assert body["title"]


def test_a_validation_error_lists_its_field_errors(client: TestClient):
    body = problem(client.get("/api/v1/probe/errors/validation"))

    assert body["errors"] == [
        {"field": "name", "code": "REQUIRED", "message": "A name is required."}
    ]


def test_a_rate_limit_tells_the_client_when_to_retry(client: TestClient):
    response = client.get("/api/v1/probe/errors/rate-limited")

    assert response.headers["Retry-After"] == "30"


def test_a_missing_credential_challenge_names_the_bearer_scheme(client: TestClient):
    response = client.get("/api/v1/probe/errors/unauthenticated")

    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("kind", ["persistence", "crash"])
def test_a_server_failure_reveals_nothing_about_its_cause(client: TestClient, kind: str):
    text = client.get(f"/api/v1/probe/errors/{kind}").text

    assert "hunter2" not in text
    assert "/srv/data" not in text
    assert "secret" not in text
    assert "Traceback" not in text


def test_an_unimplemented_route_answers_501(client: TestClient):
    response = client.get("/api/v1/probe/not-implemented")

    assert response.status_code == 501
    assert problem(response)["code"] == "NOT_IMPLEMENTED"


def test_the_stubbed_operations_answer_501_until_they_are_built(
    client: TestClient, issue_session: IssueSession
):
    token = issue_session().token

    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 501
    assert problem(response)["code"] == "NOT_IMPLEMENTED"


def test_an_unknown_path_is_a_not_found_problem(client: TestClient):
    response = client.get("/api/v1/nope")

    assert response.status_code == 404
    assert problem(response)["code"] == "NOT_FOUND"


def test_a_wrong_method_is_a_405_problem_that_names_the_allowed_methods(client: TestClient):
    response = client.put("/api/v1/probe/whoami")

    assert response.status_code == 405
    assert problem(response)["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in response.headers["Allow"]


# --- query validation -----------------------------------------------------------------------------


def test_paging_and_sorting_have_documented_defaults(client: TestClient):
    response = client.get("/api/v1/probe/page")

    assert response.json() == {"page": 1, "size": 50, "sort": [["date", True]]}


def test_paging_and_sorting_are_parsed_from_the_query(client: TestClient):
    response = client.get("/api/v1/probe/page?page=3&pageSize=200&sort=-amount,date")

    assert response.json() == {
        "page": 3,
        "size": 200,
        "sort": [["amount", True], ["date", False]],
    }


@pytest.mark.parametrize("query", ["page=0", "pageSize=0", "pageSize=201", "page=abc"])
def test_a_bad_page_parameter_is_an_invalid_filter(client: TestClient, query: str):
    response = client.get(f"/api/v1/probe/page?{query}")

    assert response.status_code == 400
    body = problem(response)
    assert body["code"] == "INVALID_FILTER"
    assert body["instance"] == "/api/v1/probe/page"


@pytest.mark.parametrize(
    "query", ["sort=nope", "sort=-nope", "sort=date,nope", "sort=date&sort=amount"]
)
def test_an_unsupported_sort_is_an_invalid_sort(client: TestClient, query: str):
    response = client.get(f"/api/v1/probe/page?{query}")

    assert response.status_code == 400
    assert problem(response)["code"] == "INVALID_SORT"


def test_a_sort_error_never_echoes_the_requested_field(client: TestClient):
    response = client.get("/api/v1/probe/page?sort=secretColumn")

    assert "secretColumn" not in response.text


def test_a_single_field_sort_accepts_one_field(client: TestClient):
    response = client.get("/api/v1/probe/single-sort?sort=status")

    assert response.json() == {"sort": [["status", False]]}


@pytest.mark.parametrize("query", ["sort=startedAt,status", "sort=startedAt&sort=status", "sort=x"])
def test_a_single_field_sort_rejects_lists_and_unknown_fields(client: TestClient, query: str):
    response = client.get(f"/api/v1/probe/single-sort?{query}")

    assert response.status_code == 400
    assert problem(response)["code"] == "INVALID_SORT"


def test_the_single_field_sort_has_a_default(client: TestClient):
    assert client.get("/api/v1/probe/single-sort").json() == {"sort": [["startedAt", True]]}


def test_filters_are_parsed_from_the_query(client: TestClient):
    response = client.get(
        "/api/v1/probe/filter?from=2024-01-01&to=2024-03-31&currency=USD&currency=UNKNOWN"
        "&minAmount=-10.5&needsReview=true&tripId=NONE"
    )

    assert response.json() == {
        "from": "2024-01-01",
        "to": "2024-03-31",
        "currencies": ["USD", "UNKNOWN"],
        "minAmount": "-10.5",
        "needsReview": True,
        "tripIds": ["NONE"],
    }


@pytest.mark.parametrize(
    "query",
    [
        "from=yesterday",
        "currency=usd",
        "currency=US",
        "minAmount=ten",
        "needsReview=maybe",
        "month=2024-13",
        "month=2024-1",
        "month=0000-01",
        "from=2024-05-01&to=2024-04-01",
        "minAmount=5&maxAmount=1",
    ],
)
def test_a_malformed_filter_is_an_invalid_filter(client: TestClient, query: str):
    response = client.get(f"/api/v1/probe/filter?{query}")

    assert response.status_code == 400
    assert problem(response)["code"] == "INVALID_FILTER"


@pytest.mark.parametrize(
    ("query", "expected_from", "expected_to"),
    [
        ("month=2024-02", "2024-02-01", "2024-02-29"),
        ("month=2024-02&from=2024-02-10", "2024-02-10", "2024-02-29"),
        ("month=2024-02&to=2024-02-10", "2024-02-01", "2024-02-10"),
        ("month=2024-02&from=2024-01-01&to=2024-12-31", "2024-02-01", "2024-02-29"),
        ("month=2024-02&from=2024-02-05&to=2024-02-06", "2024-02-05", "2024-02-06"),
    ],
)
def test_a_month_filter_is_intersected_with_from_and_to(
    client: TestClient, query: str, expected_from: str, expected_to: str
):
    body = client.get(f"/api/v1/probe/filter?{query}").json()

    assert (body["from"], body["to"]) == (expected_from, expected_to)


def test_a_month_outside_the_from_to_range_is_an_invalid_filter(client: TestClient):
    response = client.get("/api/v1/probe/filter?month=2024-02&from=2024-03-01&to=2024-03-31")

    assert response.status_code == 400
    assert problem(response)["code"] == "INVALID_FILTER"


# --- body validation ------------------------------------------------------------------------------


def test_a_valid_body_is_accepted(client: TestClient):
    response = client.post(
        "/api/v1/probe/body", json={"name": "ok", "lineItems": [{"unitPrice": 3}]}
    )

    assert response.json() == {"name": "ok"}


def test_body_validation_errors_are_422_with_camel_case_nested_fields(client: TestClient):
    response = client.post(
        "/api/v1/probe/body",
        json={"name": "", "lineItems": [{"unitPrice": 1}, {"unitPrice": "abc"}], "extra": 1},
    )

    body = problem(response)
    assert response.status_code == 422
    assert body["code"] == "VALIDATION_ERROR"
    assert body["instance"] == "/api/v1/probe/body"
    fields = {error["field"]: error["code"] for error in body["errors"]}
    assert fields == {
        "name": "TOO_SHORT",
        "lineItems[1].unitPrice": "INVALID",
        "extra": "UNKNOWN_FIELD",
    }
    assert all(error["message"] for error in body["errors"])


def test_a_missing_body_field_is_reported_as_required(client: TestClient):
    body = problem(client.post("/api/v1/probe/body", json={"name": "ok"}))

    assert body["errors"] == [
        {"field": "lineItems", "code": "REQUIRED", "message": body["errors"][0]["message"]}
    ]


def test_a_validation_message_never_echoes_the_submitted_value(client: TestClient):
    response = client.post(
        "/api/v1/probe/body", json={"name": "ok", "lineItems": [{"unitPrice": "hunter2"}]}
    )

    assert response.status_code == 422
    assert "hunter2" not in response.text


def test_a_malformed_json_body_is_a_validation_problem(client: TestClient):
    response = client.post(
        "/api/v1/probe/body", content="{oops", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert problem(response)["errors"][0]["field"] == "body"


# --- shared DTO formatting ------------------------------------------------------------------------


def test_a_transaction_item_formats_money_rates_and_dates(client: TestClient):
    item = client.get("/api/v1/probe/transaction").json()

    assert item["billing"] == {"amount": "-1234.50", "currency": "ILS"}
    assert item["original"] == {"amount": "10.01", "currency": None}
    assert item["commission"] == {"amount": "0.40", "currency": "ILS"}
    assert item["effectiveRate"] == "3.712346"
    assert item["transactionDate"] == "2024-03-05"
    assert item["chargeDate"] == "2024-04-02"
    assert item["installment"] == {"number": 2, "total": 6}
    assert item["trip"] == {"id": "trip-1", "name": "Berlin", "membership": "AUTO"}
    assert item["category"] == {
        "bank": "Food",
        "custom": None,
        "effective": "Food",
        "source": "BANK",
    }
    assert item["card"]["label"] == "Max ••1234"
    assert item["card"]["ownerName"] == "Dana"
    assert item["reviewReasons"] == ["UNKNOWN_CURRENCY"]


def test_nullable_transaction_fields_are_present_as_null(client: TestClient):
    item = client.get("/api/v1/probe/transaction?kind=minimal").json()

    assert item["billing"] == {"amount": "-12.30", "currency": "ILS"}
    for name in (
        "chargeDate", "city", "country", "installment", "original", "effectiveRate",
        "commission", "trip", "notes",
    ):  # fmt: skip
        assert item[name] is None
    assert item["reviewReasons"] == []


def test_a_tiny_negative_amount_is_never_shown_as_negative_zero():
    item = to_transaction_list_item(
        transaction_record(billing_amount=Decimal("-0.001")), present_card
    )

    assert item.billing.amount == "0.00"


def test_the_detail_shows_the_foreign_exchange_deviation_and_the_installment_plan():
    record = transaction_record(
        transaction_type=TransactionType.INSTALLMENTS,
        installment_number=2,
        installment_total=6,
        billing_amount=Decimal("-100"),
        original_amount=Decimal("-27.03"),
        original_currency="USD",
        effective_rate=Decimal("-3.7123"),
        stated_fx_rate=Decimal("3.65"),
        commission=Decimal("1.5"),
        imported_at=datetime(2024, 5, 1, 8, 30, 15, tzinfo=UTC),
    )

    detail = to_transaction_detail(record, present_card).model_dump(by_alias=True)

    assert detail["fx"] == {
        "statedRate": "3.650000",
        "effectiveRate": "3.712300",
        "deviationPercent": "1.71",
        "commission": {"amount": "1.50", "currency": "ILS"},
    }
    plan = detail["installmentPlan"]
    assert (plan["number"], plan["total"], plan["remainingPayments"]) == (2, 6, 4)
    assert plan["perPayment"] == {"amount": "-100.00", "currency": "ILS"}
    assert plan["remainingAmount"] == {"amount": "-400.00", "currency": "ILS"}
    assert detail["importedAt"] == "2024-05-01T08:30:15Z"


def test_a_plain_detail_has_no_fx_or_installment_plan():
    detail = to_transaction_detail(
        transaction_record(imported_at=datetime(2024, 5, 1, 8, 30, 15)), present_card
    ).model_dump(by_alias=True)

    assert detail["fx"] is None
    assert detail["installmentPlan"] is None
    assert detail["importedAt"] == "2024-05-01T08:30:15Z"


def test_a_billing_summary_keeps_zero_net_currencies_and_unknown_counts():
    summary = BillingSummaryRecord(
        count=5,
        unknown_billing_currency_count=1,
        totals=(
            BillingTotalRecord("ILS", 3, Decimal("-10")),
            BillingTotalRecord("USD", 1, Decimal("0")),
        ),
    )

    dto = to_billing_summary(summary).model_dump(by_alias=True)

    assert dto == {
        "count": 5,
        "unknownBillingCurrencyCount": 1,
        "billingTotals": [
            {"count": 3, "net": {"amount": "-10.00", "currency": "ILS"}},
            {"count": 1, "net": {"amount": "0.00", "currency": "USD"}},
        ],
    }


def test_page_metadata_reports_the_page_count():
    meta = to_page_meta(Page(items=(1, 2), page=2, size=2, total=5)).model_dump(by_alias=True)

    assert meta == {"page": 2, "pageSize": 2, "totalItems": 5, "totalPages": 3}


# --- the contract's operation list ----------------------------------------------------------------


def test_the_app_routes_every_contract_operation_with_its_ids_paths_and_statuses(composition):
    assert_operations_conform(create_app(composition), error_statuses={401, 403})


def test_the_contract_helper_reports_operations_the_app_does_not_route():
    problems = operation_mismatches(FastAPI(), ["getMe"])

    assert problems == ["getMe: not routed by the app"]


def test_the_documented_problem_schema_is_shared_by_the_error_responses(composition):
    document = create_app(composition).openapi()

    response = document["paths"]["/api/v1/cards"]["get"]["responses"]["401"]
    assert response["content"][PROBLEM_JSON]["schema"] == {"$ref": "#/components/schemas/Problem"}
    assert "Problem" in document["components"]["schemas"]
    assert "HTTPValidationError" not in document["components"]["schemas"]


# --- serving --------------------------------------------------------------------------------------


def _serve(*argv: str, env: dict[str, str] | None = None):
    calls: list[tuple[str, int]] = []
    out, err = io.StringIO(), io.StringIO()
    environ = {
        "BT_DSN": "postgresql://u:p@db/x",
        "BT_CIPHER_KEY": base64.b64encode(CIPHER_KEY).decode(),
    }
    code = main(
        ["serve", *argv],
        environ=environ if env is None else env,
        stdout=out,
        stderr=err,
        api_server=lambda settings, host, port: calls.append((host, port)),
    )
    return code, calls, out.getvalue(), err.getvalue()


def test_serve_binds_to_the_loopback_interface_by_default():
    code, calls, out, _ = _serve()

    assert code == 0
    assert calls == [("127.0.0.1", 8000)]
    assert "http://127.0.0.1:8000/api/v1" in out


def test_serve_accepts_a_host_and_a_port():
    _, calls, _, _ = _serve("--host", "0.0.0.0", "--port", "9100")

    assert calls == [("0.0.0.0", 9100)]


@pytest.mark.parametrize("port", ["0", "65536", "abc", "-1"])
def test_serve_rejects_an_invalid_port(port: str):
    code, calls, _, err = _serve("--port", port)

    assert code == 2
    assert calls == []
    assert "port" in err


def test_serve_needs_the_database_and_the_cipher_key():
    code, calls, _, err = _serve(env={})

    assert code == 2
    assert calls == []
    assert "BT_DSN" in err
