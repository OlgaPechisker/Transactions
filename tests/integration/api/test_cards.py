"""GET /cards and PATCH /cards/{cardId} against the real schema and authenticator."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.contracts.openapi_conformance import assert_operations_conform
from tests.integration.api.conftest import PgSession
from tests.storage.seeds import create_claimed_card

from bank_transactions.domain.enums import IssuerCode


@pytest.fixture
def seed_claimed_card(factory, cipher):
    return lambda user, **card: create_claimed_card(factory, cipher, user, **card)


@pytest.fixture
def card_id(pg_signed_in: PgSession, factory):
    def find(user=None) -> str:
        with factory.create() as uow:
            return str(
                uow.visible_cards.list_visible((user or pg_signed_in.user).user_id)[0].public_id
            )

    return find


def patch(session: PgSession, card_id: str, body: dict, **kwargs):
    return session.client.patch(
        f"/api/v1/cards/{card_id}", json=body, headers=session.unsafe_headers, **kwargs
    )


def test_the_card_list_shows_the_users_cards_in_both_modes(pg_signed_in, seed_claimed_card):
    seed_claimed_card(
        pg_signed_in.user, last4="4580", issuer=IssuerCode.ISRACARD, owner_name="Noa Levi"
    )

    response = pg_signed_in.client.get("/api/v1/cards")

    assert response.status_code == 200
    (card,) = response.json()["items"]
    assert card["id"].startswith("crd_")
    assert (card["issuer"], card["last4"], card["nickname"]) == ("ISRACARD", "4580", None)
    assert card["owner"] == {"name": "Noa Levi"}
    assert "4580" in card["label"] and "Noa Levi" in card["label"]


def test_a_user_without_cards_gets_an_empty_list(pg_signed_in):
    response = pg_signed_in.client.get("/api/v1/cards")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_the_card_list_requires_a_session(pg_client: TestClient):
    assert pg_client.get("/api/v1/cards").status_code == 401


def test_a_nickname_is_trimmed_stored_and_listed(pg_signed_in, seed_claimed_card, card_id):
    seed_claimed_card(pg_signed_in.user)

    response = patch(pg_signed_in, card_id(), {"nickname": "  Travel card  "})

    assert response.status_code == 200
    assert response.json()["nickname"] == "Travel card"
    assert pg_signed_in.client.get("/api/v1/cards").json()["items"][0]["nickname"] == "Travel card"


@pytest.mark.parametrize("cleared", [None, "", "   "])
def test_a_null_or_blank_nickname_clears_it(pg_signed_in, seed_claimed_card, card_id, cleared):
    seed_claimed_card(pg_signed_in.user)
    patch(pg_signed_in, card_id(), {"nickname": "Old"})

    response = patch(pg_signed_in, card_id(), {"nickname": cleared})

    assert response.status_code == 200
    assert response.json()["nickname"] is None


def test_the_nickname_limit_applies_after_trimming(pg_signed_in, seed_claimed_card, card_id):
    seed_claimed_card(pg_signed_in.user)

    padded = patch(pg_signed_in, card_id(), {"nickname": "  " + "n" * 120 + "  "})
    too_long = patch(pg_signed_in, card_id(), {"nickname": "n" * 121})

    assert padded.status_code == 200
    assert padded.json()["nickname"] == "n" * 120
    assert too_long.status_code == 422
    assert too_long.json()["code"] == "VALIDATION_ERROR"
    assert [(e["field"], e["code"]) for e in too_long.json()["errors"]] == [
        ("nickname", "TOO_LONG")
    ]
    assert pg_signed_in.client.get("/api/v1/cards").json()["items"][0]["nickname"] == "n" * 120


def test_a_body_without_the_nickname_is_a_422(pg_signed_in, seed_claimed_card, card_id):
    seed_claimed_card(pg_signed_in.user)

    response = patch(pg_signed_in, card_id(), {})

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "nickname"


def test_an_unknown_card_is_a_404(pg_signed_in):
    response = patch(pg_signed_in, "crd_aaaaaaaaaaaaaaaaaaaaaaaaaa", {"nickname": "x"})

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_another_users_card_is_invisible_and_cannot_be_renamed(
    pg_signed_in, pg_sign_in, seed_claimed_card, card_id
):
    seed_claimed_card(pg_signed_in.user, last4="4580", owner_name="Dana Levi")
    mine = card_id()
    stranger = pg_sign_in("user.b@example.com", "User B", pg_signed_in.mode)

    listing = stranger.client.get("/api/v1/cards")
    attempt = patch(stranger, mine, {"nickname": "stolen"})

    assert listing.json() == {"items": []}
    assert attempt.status_code == 404
    assert attempt.json()["code"] == "NOT_FOUND"
    assert pg_signed_in.client.get("/api/v1/cards").json()["items"][0]["nickname"] is None


@pytest.mark.parametrize("auth_mode", ["cookie"])
def test_a_cookie_rename_needs_the_csrf_header(pg_signed_in, seed_claimed_card, card_id):
    seed_claimed_card(pg_signed_in.user)

    response = pg_signed_in.client.patch(f"/api/v1/cards/{card_id()}", json={"nickname": "x"})

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_VALIDATION_FAILED"


def test_renaming_requires_a_session(pg_client: TestClient):
    response = pg_client.patch(
        "/api/v1/cards/crd_aaaaaaaaaaaaaaaaaaaaaaaaaa", json={"nickname": "x"}
    )

    assert response.status_code == 401


def test_the_card_operations_match_the_contract(pg_app: FastAPI):
    assert_operations_conform(pg_app, "listCards", "updateCard")
