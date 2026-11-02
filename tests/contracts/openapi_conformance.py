"""Compares the app's generated OpenAPI operations with `docs/api-contract/openapi.yaml`.

Slice units call `assert_operations_conform(app, "listTransactions", ...)` from their endpoint
tests; the foundation tests use it on the whole skeleton."""

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "docs" / "api-contract" / "openapi.yaml"
API_PREFIX = "/api/v1"
_HTTP_METHODS = ("get", "put", "post", "delete", "patch")


@dataclass(frozen=True)
class OperationShape:
    operation_id: str
    method: str
    path: str
    success_status: int
    error_statuses: frozenset[int]


def _shapes(paths: dict[str, Any], prefix: str) -> dict[str, OperationShape]:
    shapes: dict[str, OperationShape] = {}
    for path, item in paths.items():
        for method in _HTTP_METHODS:
            if method not in item:
                continue
            operation = item[method]
            statuses = {int(code) for code in operation["responses"]}
            shape = OperationShape(
                operation_id=str(operation["operationId"]),
                method=method.upper(),
                path=prefix + path,
                success_status=min(code for code in statuses if 200 <= code < 300),
                error_statuses=frozenset(code for code in statuses if code >= 400),
            )
            shapes[shape.operation_id] = shape
    return shapes


def contract_operations() -> dict[str, OperationShape]:
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    return _shapes(document["paths"], API_PREFIX)


def app_operations(app: FastAPI) -> dict[str, OperationShape]:
    return _shapes(app.openapi()["paths"], "")


def operation_mismatches(
    app: FastAPI,
    operation_ids: Collection[str] | None = None,
    *,
    error_statuses: Collection[int] | None = None,
) -> list[str]:
    """Differences between the app and the contract, one readable line each.

    `operation_ids` limits the check to those operations (default: all of them, and the app
    must not have extra ones). `error_statuses` limits which documented error statuses are
    compared (default: the exact set)."""
    expected, actual = contract_operations(), app_operations(app)
    wanted = set(expected) if operation_ids is None else set(operation_ids)
    problems = [f"{name}: not in the contract" for name in sorted(wanted - set(expected))]
    wanted &= set(expected)
    if operation_ids is None:
        problems += [f"{name}: not in the contract" for name in sorted(set(actual) - set(expected))]
    for name in sorted(wanted):
        if name not in actual:
            problems.append(f"{name}: not routed by the app")
            continue
        problems += _differences(name, expected[name], actual[name], error_statuses)
    return problems


def _differences(
    name: str,
    expected: OperationShape,
    actual: OperationShape,
    error_statuses: Collection[int] | None,
) -> list[str]:
    problems = [
        f"{name}: {label} is {got!r}, the contract says {want!r}"
        for label, want, got in (
            ("method", expected.method, actual.method),
            ("path", expected.path, actual.path),
            ("success status", expected.success_status, actual.success_status),
        )
        if want != got
    ]
    scope = frozenset(range(400, 600)) if error_statuses is None else frozenset(error_statuses)
    want_errors, got_errors = expected.error_statuses & scope, actual.error_statuses & scope
    if want_errors != got_errors:
        problems.append(
            f"{name}: error statuses are {sorted(got_errors)}, the contract says "
            f"{sorted(want_errors)}"
        )
    return problems


def assert_operations_conform(
    app: FastAPI,
    *operation_ids: str,
    error_statuses: Collection[int] | None = None,
) -> None:
    problems = operation_mismatches(app, operation_ids or None, error_statuses=error_statuses)
    assert not problems, "OpenAPI does not conform to the contract:\n" + "\n".join(problems)
