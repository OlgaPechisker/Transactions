"""The caller's cards."""

from fastapi import APIRouter

from bank_transactions.api.dependencies import CardIdPath, Composition, CsrfUser, CurrentUser
from bank_transactions.api.schemas.cards import CardListResponse, UpdateCardRequest
from bank_transactions.api.schemas.common import CardDto

router = APIRouter(tags=["Cards"])


@router.get("/cards", operation_id="listCards")
def list_cards(user: CurrentUser, comp: Composition) -> CardListResponse:
    comp.cards.list(user)
    raise NotImplementedError


@router.patch("/cards/{cardId}", operation_id="updateCard")
def update_card(
    card_id: CardIdPath, body: UpdateCardRequest, user: CsrfUser, comp: Composition
) -> CardDto:
    comp.cards.set_nickname(user, card_id, body.nickname)
    raise NotImplementedError
