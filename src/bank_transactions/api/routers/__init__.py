"""The router registry: every router mounted under `/api/v1` by `create_app`."""

from fastapi import APIRouter

from bank_transactions.api.routers import (
    analysis,
    cards,
    categorization,
    currency_validation,
    imports,
    me,
    sessions,
    transaction_overrides,
    transactions,
    trips,
)

ALL_ROUTERS: tuple[APIRouter, ...] = (
    sessions.router,
    me.router,
    cards.router,
    imports.router,
    transactions.router,
    transaction_overrides.router,
    trips.router,
    analysis.router,
    currency_validation.router,
    categorization.router,
)
