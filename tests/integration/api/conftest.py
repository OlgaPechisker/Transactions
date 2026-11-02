"""Fixtures for the API tests.

Fake-backed: `client` and `signed_in` yield `(client, user, csrf)` over in-memory fakes, in both
auth modes (`bearer`, `cookie`).

Postgres-backed (real schema, bootstrap composition, real session rows): `pg_signed_in` gives a
`PgSession` for a seeded user, `pg_sign_in` creates further users (isolation tests) and
`pg_client` is the unauthenticated client. They run on the per-test schema of the integration
conftest (BT_TEST_DSN)."""

import base64
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from itertools import count
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.fakes import (
    FakeAuthenticator,
    FakeCatalogue,
    FakeClock,
    FakeHasher,
    FakeTokenService,
    InMemoryUnitOfWorkFactory,
)
from tests.fakes.auth import make_auth_context
from tests.storage.seeds import create_user

from bank_transactions import bootstrap
from bank_transactions.api.app import create_app
from bank_transactions.api.dependencies import CSRF_HEADER, SESSION_COOKIE
from bank_transactions.bootstrap import SystemClock, build_api_composition, build_parser_selector
from bank_transactions.config import Settings
from bank_transactions.identity.crypto import IdentifierCipher
from bank_transactions.identity.tokens import HmacTokenService
from bank_transactions.parsing.workbook import ExcelWorkbookReader
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import UserRecord
from bank_transactions.ports.clock import Clock
from bank_transactions.ports.token_service import TokenService
from bank_transactions.services.analysis_service import AnalysisService
from bank_transactions.services.card_service import CardPresenter, CardService
from bank_transactions.services.category_service import CategoryService
from bank_transactions.services.composition import ApiComposition
from bank_transactions.services.currency_validation_service import CurrencyValidationService
from bank_transactions.services.import_api_service import ImportApiService
from bank_transactions.services.interfaces import AuthContext, Authenticator
from bank_transactions.services.profile_service import ProfileService
from bank_transactions.services.review_counter import UowReviewCounter
from bank_transactions.services.session_service import SessionService
from bank_transactions.services.transaction_service import TransactionService
from bank_transactions.services.trip_service import TripService

CIPHER_KEY = bytes(range(64))
AUTH_MODES = ("bearer", "cookie")


@dataclass(frozen=True)
class IssuedSession:
    """A user with a live session: the credentials a client presents and the CSRF token."""

    user: AuthContext
    token: str
    csrf: str


IssueSession = Callable[[], IssuedSession]


def build_fake_composition(authenticator: Authenticator) -> ApiComposition:
    uow_factory = InMemoryUnitOfWorkFactory()
    clock, tokens, catalogue = FakeClock(), FakeTokenService(), FakeCatalogue()
    protector = IdentifierCipher(CIPHER_KEY)
    presenter = CardPresenter(protector)
    return ApiComposition(
        authenticator=authenticator,
        card_presenter=presenter,
        sessions=SessionService(uow_factory, FakeHasher(), tokens, clock),
        profile=ProfileService(uow_factory, UowReviewCounter(uow_factory)),
        cards=CardService(uow_factory, presenter),
        imports=ImportApiService(
            uow_factory,
            ExcelWorkbookReader(),
            build_parser_selector(Settings()),
            protector,
            tokens,
            clock,
        ),
        transactions=TransactionService(uow_factory),
        categories=CategoryService(uow_factory),
        trips=TripService(uow_factory, catalogue, clock),
        analysis=AnalysisService(uow_factory, clock),
        currency_validation=CurrencyValidationService(uow_factory, catalogue, clock),
    )


@pytest.fixture
def fake_authenticator() -> FakeAuthenticator:
    return FakeAuthenticator({})


@pytest.fixture
def issue_session(fake_authenticator: FakeAuthenticator) -> IssueSession:
    """Creates a user with a session each call. Override to sign in against a real database."""
    numbers = count(1)

    def issue() -> IssuedSession:
        number = next(numbers)
        user = make_auth_context(
            user_id=number,
            session_id=number,
            csrf_token=f"csrf-token-{number}",
            user_display_name=f"User {number}",
        )
        token = f"session-token-{number}"
        fake_authenticator.sessions[token] = user
        return IssuedSession(user, token, user.csrf_token)

    return issue


@pytest.fixture
def composition(fake_authenticator: FakeAuthenticator) -> ApiComposition:
    return build_fake_composition(fake_authenticator)


@pytest.fixture
def app(composition: ApiComposition) -> FastAPI:
    return create_app(composition)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Unauthenticated; unexpected server errors surface as 500 problems, not exceptions."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(params=AUTH_MODES)
def auth_mode(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def signed_in(
    client: TestClient, issue_session: IssueSession, auth_mode: str
) -> tuple[TestClient, AuthContext, str]:
    """(client, user, csrf). Bearer clients send `Authorization`; cookie clients carry the
    `bt_session` cookie. The CSRF token is returned, never added to requests automatically."""
    session = issue_session()
    if auth_mode == "bearer":
        client.headers["Authorization"] = f"Bearer {session.token}"
    else:
        client.cookies.set(SESSION_COOKIE, session.token)
    return client, session.user, session.csrf


# --- Postgres-backed -----------------------------------------------------------------------------

SESSION_LIFETIME = timedelta(hours=12)


@dataclass(frozen=True)
class PgSession:
    """A seeded user signed in through a real session row.

    `client` already carries the credential (bearer header or `bt_session` cookie). Pass
    `unsafe_headers` on POST/PUT/PATCH/DELETE: it holds the CSRF header in cookie mode and is
    empty in bearer mode, so one test body serves both modes."""

    client: TestClient
    user: UserRecord
    token: str
    csrf: str
    mode: str

    @property
    def unsafe_headers(self) -> dict[str, str]:
        return {CSRF_HEADER: self.csrf} if self.mode == "cookie" else {}


PgSignIn = Callable[..., PgSession]


class StoredSessionAuthenticator(Authenticator):
    """Reads the real session and user rows, as U4's `SessionAuthenticator` will. TEMPORARY
    (U2-R7): `pg_composition` swaps it in unconditionally while `SessionAuthenticator` and
    `SessionRepository` are stubs (hence raw SQL); U4 deletes it together with the swap."""

    def __init__(self, sql: Callable[..., list[Any]], tokens: TokenService, clock: Clock) -> None:
        self._sql = sql
        self._tokens = tokens
        self._clock = clock

    def authenticate(self, token: str) -> AuthContext | None:
        digest = self._tokens.digest(token)
        rows = self._sql(
            "SELECT s.id, s.user_id, u.display_name FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token_digest = :digest AND s.revoked_at IS NULL AND s.expires_at > :now",
            digest=digest,
            now=self._clock.now(),
        )
        if not rows:
            return None
        session_id, user_id, display_name = rows[0]
        return AuthContext(user_id, session_id, self._tokens.csrf_for(digest), display_name)


@pytest.fixture
def pg_composition(
    engine: Engine,
    sql: Callable[..., list[Any]],
    postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> ApiComposition:
    """The bootstrap composition, wired to the per-test schema instead of the default one."""
    monkeypatch.setattr(bootstrap, "create_database_engine", lambda dsn: engine)
    settings = Settings(dsn=postgres_dsn, cipher_key=base64.b64encode(CIPHER_KEY).decode())
    composition = build_api_composition(settings)
    # TEMPORARY (U2-R7): U4 must delete StoredSessionAuthenticator and this swap; the real
    # authenticator must then pass these tests.
    stand_in = StoredSessionAuthenticator(sql, HmacTokenService(CIPHER_KEY), SystemClock())
    return replace(composition, authenticator=stand_in)


@pytest.fixture
def pg_app(pg_composition: ApiComposition) -> FastAPI:
    return create_app(pg_composition)


@pytest.fixture
def pg_client(pg_app: FastAPI) -> TestClient:
    """Unauthenticated client on the Postgres-backed app."""
    return TestClient(pg_app, raise_server_exceptions=False)


@pytest.fixture
def pg_sign_in(
    pg_app: FastAPI,
    factory: UnitOfWorkFactory,
    sql: Callable[..., list[Any]],
    auth_mode: str,
) -> PgSignIn:
    """`pg_sign_in(email=..., display_name=..., mode=...)`: commits the user (or reuses the one
    with that email) and a fresh session row, and returns a `PgSession` with its own client.
    `mode` defaults to the current `auth_mode` parameter. The session row is inserted with SQL
    because SessionRepository is still a stub (U4)."""
    tokens, clock = HmacTokenService(CIPHER_KEY), SystemClock()

    def sign_in(
        email: str = "user.a@example.com",
        display_name: str = "User A",
        mode: str | None = None,
    ) -> PgSession:
        chosen = mode or auth_mode
        user = create_user(factory, email, display_name)
        token = tokens.new_session_token()
        digest = tokens.digest(token)
        now = clock.now()
        sql(
            "INSERT INTO sessions (user_id, token_digest, created_at, expires_at) "
            "VALUES (:user_id, :digest, :created_at, :expires_at)",
            user_id=user.user_id,
            digest=digest,
            created_at=now,
            expires_at=now + SESSION_LIFETIME,
        )
        client = TestClient(pg_app, raise_server_exceptions=False)
        if chosen == "bearer":
            client.headers["Authorization"] = f"Bearer {token}"
        else:
            client.cookies.set(SESSION_COOKIE, token)
        return PgSession(client, user, token, tokens.csrf_for(digest), chosen)

    return sign_in


@pytest.fixture
def pg_signed_in(pg_sign_in: PgSignIn) -> PgSession:
    """User A signed in, in each auth mode (parametrised through `auth_mode`)."""
    return pg_sign_in()
