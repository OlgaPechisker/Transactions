"""The Postgres-backed `pg_signed_in` fixture against the real dependency chain: bootstrap
composition, real user and session rows, real auth and CSRF dependencies."""

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from tests.integration.api.conftest import PgSession, PgSignIn

from bank_transactions.api.dependencies import CsrfUser, CurrentUser

probe = APIRouter(prefix="/probe")


@probe.get("/whoami")
def whoami(user: CurrentUser) -> dict[str, object]:
    return {"userId": user.user_id, "name": user.user_display_name}


@probe.post("/mutate")
def mutate(user: CsrfUser) -> dict[str, object]:
    return {"userId": user.user_id}


@pytest.fixture
def pg_app(pg_app: FastAPI) -> FastAPI:
    pg_app.include_router(probe, prefix="/api/v1")
    return pg_app


def test_an_authenticated_read_passes_the_auth_dependency_in_both_modes(pg_signed_in: PgSession):
    response = pg_signed_in.client.get("/api/v1/probe/whoami")

    assert response.status_code == 200
    assert response.json() == {"userId": pg_signed_in.user.user_id, "name": "User A"}


def test_an_unauthenticated_request_is_a_401_problem(pg_client: TestClient):
    response = pg_client.get("/api/v1/probe/whoami")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_an_unknown_token_is_unauthenticated(pg_client: TestClient):
    response = pg_client.get(
        "/api/v1/probe/whoami", headers={"Authorization": "Bearer not-a-session"}
    )

    assert response.status_code == 401


@pytest.mark.parametrize("auth_mode", ["cookie"])
def test_a_cookie_mutation_without_the_csrf_header_is_forbidden(pg_signed_in: PgSession):
    response = pg_signed_in.client.post("/api/v1/probe/mutate")

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_VALIDATION_FAILED"


@pytest.mark.parametrize("auth_mode", ["cookie"])
def test_a_cookie_mutation_with_a_wrong_csrf_token_is_forbidden(pg_signed_in: PgSession):
    response = pg_signed_in.client.post(
        "/api/v1/probe/mutate", headers={"X-CSRF-Token": "not-the-session-token"}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_VALIDATION_FAILED"


def test_a_mutation_with_the_unsafe_headers_succeeds_in_both_modes(pg_signed_in: PgSession):
    response = pg_signed_in.client.post(
        "/api/v1/probe/mutate", headers=pg_signed_in.unsafe_headers
    )

    assert response.status_code == 200
    assert response.json() == {"userId": pg_signed_in.user.user_id}


@pytest.mark.parametrize("auth_mode", ["bearer"])
def test_a_bearer_mutation_needs_no_csrf_header(pg_signed_in: PgSession):
    assert pg_signed_in.unsafe_headers == {}
    assert pg_signed_in.client.post("/api/v1/probe/mutate").status_code == 200


def test_two_users_are_distinct_callers(pg_sign_in: PgSignIn):
    user_a = pg_sign_in()
    user_b = pg_sign_in("user.b@example.com", "User B")

    a = user_a.client.get("/api/v1/probe/whoami").json()
    b = user_b.client.get("/api/v1/probe/whoami").json()

    assert user_a.user.user_id != user_b.user.user_id
    assert (a["userId"], a["name"]) == (user_a.user.user_id, "User A")
    assert (b["userId"], b["name"]) == (user_b.user.user_id, "User B")


def test_the_session_token_is_stored_only_as_a_digest(pg_signed_in: PgSession, stored_text):
    assert pg_signed_in.token not in stored_text()
