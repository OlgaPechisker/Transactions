"""The whole `/api/v1` surface against `docs/api-contract/openapi.yaml`, on the real composition
(bootstrap wiring; no database connection is opened), plus a scan proving no stub is left."""

import base64
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from tests.contracts.openapi_conformance import (
    app_operations,
    assert_operations_conform,
    contract_operations,
)

from bank_transactions.api.app import create_app
from bank_transactions.bootstrap import build_api_composition
from bank_transactions.config import Settings

SOURCE = Path(__file__).resolve().parents[2] / "src" / "bank_transactions"
ABSTRACT_STUBS = SOURCE / "persistence" / "interfaces.py"
EXPECTED_OPERATION_COUNT = 31


@pytest.fixture(scope="module")
def app() -> FastAPI:
    settings = Settings(
        dsn="postgresql+psycopg://nobody:none@127.0.0.1:1/none",
        cipher_key=base64.b64encode(bytes(range(64))).decode(),
    )
    return create_app(build_api_composition(settings))


def test_the_contract_defines_thirty_one_operations() -> None:
    assert len(contract_operations()) == EXPECTED_OPERATION_COUNT


def test_the_app_routes_exactly_the_operations_of_the_contract(app: FastAPI) -> None:
    assert set(app_operations(app)) == set(contract_operations())


@pytest.mark.parametrize("operation_id", sorted(contract_operations()))
def test_every_operation_conforms_to_the_contract(app: FastAPI, operation_id: str) -> None:
    assert_operations_conform(app, operation_id)


def test_the_whole_app_conforms_to_the_contract(app: FastAPI) -> None:
    assert_operations_conform(app)


def test_no_stub_raises_not_implemented_outside_the_abstract_interfaces() -> None:
    offenders = [
        f"{path.relative_to(SOURCE)}:{number}"
        for path in sorted(SOURCE.rglob("*.py"))
        if path != ABSTRACT_STUBS
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"\s*raise NotImplementedError\b", line)
    ]
    assert offenders == []
