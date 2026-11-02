"""FastAPI application factory. The composition (services and the authenticator) is built by
the composition root and handed in; nothing here constructs infrastructure."""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from bank_transactions.api.dependencies import csrf_protected
from bank_transactions.api.problems import (
    PROBLEM_SCHEMA,
    install_problem_handlers,
    problem_responses,
)
from bank_transactions.api.routers import ALL_ROUTERS
from bank_transactions.api.schemas.common import ProblemDto
from bank_transactions.services.composition import ApiComposition

API_PREFIX = "/api/v1"
_FRAMEWORK_SCHEMAS = ("HTTPValidationError", "ValidationError")


def _uses_csrf(route: APIRoute) -> bool:
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        if dependant.call is csrf_protected:
            return True
        pending.extend(dependant.dependencies)
    return False


def _build_openapi(app: FastAPI) -> dict[str, Any]:
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    paths: dict[str, Any] = schema["paths"]
    operations = {
        (API_PREFIX + route.path_format, method.lower()): _uses_csrf(route)
        for router in ALL_ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in sorted(route.methods or ())
    }
    for path, item in paths.items():
        for method, operation in item.items():
            csrf = operations.get((path, method), False)
            responses: dict[str, Any] = operation["responses"]
            responses.pop("422", None)
            for status, response in problem_responses(401, *((403,) if csrf else ())).items():
                responses.setdefault(str(status), response)
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name in _FRAMEWORK_SCHEMAS:
        components.pop(name, None)
    problem = ProblemDto.model_json_schema(
        by_alias=True, ref_template="#/components/schemas/{model}"
    )
    components.update(problem.pop("$defs", {}))
    components[PROBLEM_SCHEMA] = problem
    return schema


def create_app(composition: ApiComposition) -> FastAPI:
    app = FastAPI(
        title="Bank Transactions UI API",
        version="1.0.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.composition = composition
    install_problem_handlers(app)
    for router in ALL_ROUTERS:
        app.include_router(router, prefix=API_PREFIX)

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = _build_openapi(app)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app
