"""POST /sessions and DELETE /sessions/current against the real schema, hasher and authenticator."""

from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.contracts.openapi_conformance import assert_operations_conform
from tests.integration.api.conftest import PgSession

from bank_transactions.api.dependencies import CSRF_HEADER, SESSION_COOKIE
from bank_transactions.identity.argon2_hasher import Argon2PasswordHasher
from bank_transactions.persistence.commands import NewUser

PASSWORD = "correct horse battery"


def create_login_user(factory, email="dana@example.com", name="Dana Levi", password=PASSWORD):
    with factory.create() as uow:
        user = uow.users.create(
            NewUser(email, email.strip().casefold(), name, Argon2PasswordHasher().hash(password))
        )
        uow.commit()
    return user


def sign_in(client: TestClient, email="dana@example.com", password=PASSWORD):
    return client.post("/api/v1/sessions", json={"email": email, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_sign_in_returns_a_token_and_the_profile(pg_client: TestClient, factory):
    user = create_login_user(factory)

    response = sign_in(pg_client)

    assert response.status_code == 201
    body = response.json()
    assert body["token"]
    assert body["expiresAt"].endswith("Z") or "+00:00" in body["expiresAt"]
    assert body["user"]["id"] == user.public_id
    assert (body["user"]["name"], body["user"]["email"]) == ("Dana Levi", "dana@example.com")
    assert body["user"]["role"] == "OWNER"
    assert body["user"]["csrfToken"]
    assert body["user"]["counters"] == {
        "pendingCurrencyValidations": 0,
        "needsReviewByReason": [],
    }


def test_sign_in_sets_the_session_cookie_with_the_documented_attributes(
    pg_client: TestClient, factory
):
    create_login_user(factory)

    response = sign_in(pg_client)

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}={response.json()['token']};")
    attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert {"httponly", "secure", "samesite=lax", "path=/api", "max-age=43200"} <= attributes


def test_the_issued_token_authenticates_as_a_bearer_and_as_a_cookie(pg_app: FastAPI, factory):
    create_login_user(factory)
    token = sign_in(TestClient(pg_app)).json()["token"]

    as_bearer = TestClient(pg_app).get("/api/v1/me", headers=bearer(token))
    cookie_client = TestClient(pg_app)
    cookie_client.cookies.set(SESSION_COOKIE, token)
    as_cookie = cookie_client.get("/api/v1/me")

    assert as_bearer.status_code == as_cookie.status_code == 200
    assert as_bearer.json()["email"] == as_cookie.json()["email"] == "dana@example.com"


def test_the_csrf_token_from_sign_in_authorises_a_cookie_sign_out(pg_app: FastAPI, factory):
    create_login_user(factory)
    body = sign_in(TestClient(pg_app)).json()
    client = TestClient(pg_app)
    client.cookies.set(SESSION_COOKIE, body["token"])

    without_csrf = client.delete("/api/v1/sessions/current")
    with_csrf = client.delete(
        "/api/v1/sessions/current", headers={CSRF_HEADER: body["user"]["csrfToken"]}
    )

    assert without_csrf.status_code == 403
    assert with_csrf.status_code == 204


def test_the_email_is_matched_after_trimming_and_ignoring_case(pg_client: TestClient, factory):
    create_login_user(factory, "Dana@Example.com")

    assert sign_in(pg_client, "  DANA@example.COM ").status_code == 201


def test_a_wrong_password_and_an_unknown_email_get_the_same_401(pg_client: TestClient, factory):
    create_login_user(factory)

    wrong = sign_in(pg_client, password="wrong")
    unknown = sign_in(pg_client, email="nobody@example.com")

    for response in (wrong, unknown):
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "INVALID_CREDENTIALS"
        assert "set-cookie" not in response.headers
    assert wrong.json() == unknown.json()


def test_the_eleventh_attempt_is_rate_limited_even_with_the_right_password(
    pg_client: TestClient, factory
):
    create_login_user(factory)

    statuses = [sign_in(pg_client, password="wrong").status_code for _ in range(10)]
    limited = sign_in(pg_client)

    assert statuses == [401] * 10
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "900"
    assert limited.json()["code"] == "RATE_LIMITED"
    assert limited.headers["content-type"].startswith("application/problem+json")


def test_failures_for_one_email_block_it_from_another_address(pg_app: FastAPI, factory):
    create_login_user(factory)
    for _ in range(10):
        assert (
            sign_in(TestClient(pg_app, client=("198.51.100.1", 5000)), password="x").status_code
            == 401
        )

    blocked = sign_in(TestClient(pg_app, client=("198.51.100.2", 5000)))

    assert blocked.status_code == 429


def test_an_outdated_password_hash_is_upgraded_on_sign_in(pg_client: TestClient, factory, sql):
    user = create_login_user(factory)
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    sql("UPDATE users SET password_hash = :hash WHERE id = :id", hash=weak, id=user.user_id)

    assert sign_in(pg_client).status_code == 201

    stored = sql("SELECT password_hash FROM users WHERE id = :id", id=user.user_id)[0][0]
    assert stored != weak
    assert not Argon2PasswordHasher().needs_rehash(stored)
    assert sign_in(pg_client).status_code == 201


def test_a_malformed_or_incomplete_body_is_a_400_problem_not_a_422(pg_client: TestClient):
    cases = [
        pg_client.post(
            "/api/v1/sessions", content=b"{not json", headers={"Content-Type": "application/json"}
        ),
        pg_client.post("/api/v1/sessions", json={"email": "dana@example.com"}),
        pg_client.post("/api/v1/sessions", json={"email": "dana@example.com", "password": ""}),
        pg_client.post("/api/v1/sessions", json={}),
    ]

    for response in cases:
        assert response.status_code == 400
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "INVALID_REQUEST"


def test_signing_out_ends_the_session_in_both_modes(pg_signed_in: PgSession):
    client = pg_signed_in.client

    response = client.delete("/api/v1/sessions/current", headers=pg_signed_in.unsafe_headers)

    assert response.status_code == 204
    assert response.content == b""
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert client.get("/api/v1/me").status_code == 401


def test_signing_out_only_ends_the_current_session(pg_sign_in):
    first = pg_sign_in()
    second = pg_sign_in()

    first.client.delete("/api/v1/sessions/current", headers=first.unsafe_headers)

    assert first.client.get("/api/v1/me").status_code == 401
    assert second.client.get("/api/v1/me").status_code == 200


def test_signing_out_without_a_session_is_a_401(pg_client: TestClient):
    assert pg_client.delete("/api/v1/sessions/current").status_code == 401


def test_the_session_operations_match_the_contract(pg_app: FastAPI):
    assert_operations_conform(pg_app, "createSession", "deleteCurrentSession")
