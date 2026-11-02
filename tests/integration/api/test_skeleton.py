"""The walking skeleton: the app starts and every operation of the contract is routed."""

import base64
from pathlib import Path

import pytest
import yaml
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
from tests.fakes.auth import DEFAULT_TOKEN

from bank_transactions.api.app import create_app
from bank_transactions.bootstrap import build_api_composition, build_parser_selector
from bank_transactions.config import Settings
from bank_transactions.identity.crypto import IdentifierCipher
from bank_transactions.services.analysis_service import AnalysisService
from bank_transactions.services.card_service import CardPresenter, CardService
from bank_transactions.services.category_service import CategoryService
from bank_transactions.services.composition import ApiComposition
from bank_transactions.services.currency_validation_service import CurrencyValidationService
from bank_transactions.services.import_api_service import ImportApiService
from bank_transactions.services.profile_service import ProfileService
from bank_transactions.services.review_counter import UowReviewCounter
from bank_transactions.services.session_service import SessionService
from bank_transactions.services.transaction_service import TransactionService
from bank_transactions.services.trip_service import TripService

OPENAPI = Path(__file__).resolve().parents[3] / "docs" / "api-contract" / "openapi.yaml"
HTTP_METHODS = ("get", "put", "post", "delete", "patch")
CIPHER_KEY = bytes(range(64))


def fake_composition() -> ApiComposition:
    from bank_transactions.parsing.workbook import ExcelWorkbookReader

    uow_factory = InMemoryUnitOfWorkFactory()
    clock, tokens, catalogue = FakeClock(), FakeTokenService(), FakeCatalogue()
    protector = IdentifierCipher(CIPHER_KEY)
    presenter = CardPresenter(protector)
    return ApiComposition(
        authenticator=FakeAuthenticator(),
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
def app() -> FastAPI:
    return create_app(fake_composition())


def contract_operations() -> dict[tuple[str, str], tuple[str, str]]:
    """(METHOD, path) -> (operationId, success status) as written in openapi.yaml."""
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    operations = {}
    for path, item in document["paths"].items():
        for method in HTTP_METHODS:
            if method in item:
                operation = item[method]
                success = min(code for code in operation["responses"] if str(code).startswith("2"))
                operations[(method.upper(), "/api/v1" + path)] = (
                    operation["operationId"],
                    str(success),
                )
    return operations


def app_operations(app: FastAPI) -> dict[tuple[str, str], tuple[str, str]]:
    operations = {}
    for path, item in app.openapi()["paths"].items():
        for method, operation in item.items():
            success = min(code for code in operation["responses"] if code.startswith("2"))
            operations[(method.upper(), path)] = (operation["operationId"], success)
    return operations


def test_the_contract_has_thirty_one_operations():
    assert len(contract_operations()) == 31


def test_every_contract_operation_is_routed_with_its_operation_id_and_success_status(app):
    assert app_operations(app) == contract_operations()


def test_a_stubbed_route_answers_501_as_a_problem(app):
    response = TestClient(app).get(
        "/api/v1/me", headers={"Authorization": f"Bearer {DEFAULT_TOKEN}"}
    )

    assert response.status_code == 501
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "NOT_IMPLEMENTED"


def test_a_request_without_credentials_is_a_401_problem(app):
    response = TestClient(app).get("/api/v1/me")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_the_composition_root_builds_the_api_without_opening_a_connection():
    settings = Settings(
        dsn="postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
        cipher_key=base64.b64encode(CIPHER_KEY).decode(),
    )

    app = create_app(build_api_composition(settings))

    assert len(app_operations(app)) == 31
