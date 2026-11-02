"""FastAPI application factory. The composition (services and the authenticator) is built by
the composition root and handed in; nothing here constructs infrastructure."""

from fastapi import FastAPI

from bank_transactions.api.problems import install_problem_handlers
from bank_transactions.api.routers import ALL_ROUTERS
from bank_transactions.services.composition import ApiComposition

API_PREFIX = "/api/v1"


def create_app(composition: ApiComposition) -> FastAPI:
    app = FastAPI(title="Bank Transactions UI API", version="1.0.0")
    app.state.composition = composition
    install_problem_handlers(app)
    for router in ALL_ROUTERS:
        app.include_router(router, prefix=API_PREFIX)
    return app
