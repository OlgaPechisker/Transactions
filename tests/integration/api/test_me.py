"""GET /me against the real schema and authenticator."""

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.contracts.openapi_conformance import assert_operations_conform
from tests.integration.api.conftest import PgSession
from tests.storage.seeds import create_claimed_card, create_transaction

from bank_transactions.domain.enums import ReviewReason
from bank_transactions.persistence.commands import NewForeignDetail

UNKNOWN_FOREIGN = {
    "original_amount": Decimal("10.00"),
    "original_currency": None,
    "aggregate_review_reasons": {ReviewReason.UNKNOWN_CURRENCY},
    "aggregate_foreign_detail": NewForeignDetail(Decimal("0.50"), None, Decimal("3.65")),
}


@pytest.fixture
def seed_claimed_card(factory, cipher):
    return lambda user, **card: create_claimed_card(factory, cipher, user, **card)


@pytest.fixture
def seed_transaction(factory):
    return lambda card, **overrides: create_transaction(factory, card, **overrides)


def test_me_returns_the_profile_of_the_signed_in_user_in_both_modes(pg_signed_in: PgSession):
    response = pg_signed_in.client.get("/api/v1/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": pg_signed_in.user.public_id,
        "name": "User A",
        "email": "user.a@example.com",
        "role": "OWNER",
        "csrfToken": pg_signed_in.csrf,
        "counters": {"pendingCurrencyValidations": 0, "needsReviewByReason": []},
    }


def test_me_does_not_expose_the_password_hash(pg_signed_in: PgSession):
    assert "password" not in pg_signed_in.client.get("/api/v1/me").text.lower()


def test_me_counts_unknown_currency_transactions_of_the_users_own_cards(
    pg_signed_in: PgSession, pg_sign_in, seed_claimed_card, seed_transaction
):
    card = seed_claimed_card(pg_signed_in.user, last4="4580")
    seed_transaction(card, **UNKNOWN_FOREIGN)
    seed_transaction(card, **UNKNOWN_FOREIGN)
    seed_transaction(card)
    other = pg_sign_in("user.b@example.com", "User B", pg_signed_in.mode)
    other_card = seed_claimed_card(other.user, last4="9999", account_number="1234567")
    seed_transaction(other_card, **UNKNOWN_FOREIGN)

    mine = pg_signed_in.client.get("/api/v1/me").json()["counters"]
    theirs = other.client.get("/api/v1/me").json()["counters"]

    assert mine == {
        "pendingCurrencyValidations": 2,
        "needsReviewByReason": [{"reason": "UNKNOWN_CURRENCY", "count": 2}],
    }
    assert theirs["pendingCurrencyValidations"] == 1


def test_me_without_credentials_is_a_401_problem(pg_client: TestClient):
    response = pg_client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_me_matches_the_contract(pg_app: FastAPI):
    assert_operations_conform(pg_app, "getMe")
