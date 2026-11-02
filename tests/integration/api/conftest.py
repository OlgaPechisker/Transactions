"""Fixtures for the API tests: an app over in-memory fakes and signed-in clients.

`signed_in` yields `(client, user, csrf)` and is parametrised over the two ways a client can
authenticate (`bearer`, `cookie`). The session comes from the `issue_session` fixture; a
Postgres-backed suite replaces that fixture (and `composition`) to sign in a real user."""

from collections.abc import Callable
from dataclasses import dataclass
from itertools import count

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.fakes import (
    FakeAuthenticator,
    FakeCatalogue,
    FakeClock,
    FakeHasher,
    FakeTokenService,
    InMemoryUnitOfWorkFactory,
)
from tests.fakes.auth import make_auth_context

from bank_transactions.api.app import create_app
from bank_transactions.api.dependencies import SESSION_COOKIE
from bank_transactions.bootstrap import build_parser_selector
from bank_transactions.config import Settings
from bank_transactions.identity.crypto import IdentifierCipher
from bank_transactions.parsing.workbook import ExcelWorkbookReader
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
