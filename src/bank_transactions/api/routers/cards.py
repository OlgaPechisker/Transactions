"""The caller's cards."""

from fastapi import APIRouter

from bank_transactions.api.dependencies import CardIdPath, Composition, CsrfUser, CurrentUser
from bank_transactions.api.problems import problem_responses
from bank_transactions.api.schemas.cards import CardListResponse, UpdateCardRequest
from bank_transactions.api.schemas.common import CardDto
from bank_transactions.api.schemas.mappers import to_card_dto

router = APIRouter(tags=["Cards"])


@router.get("/cards", operation_id="listCards")
def list_cards(user: CurrentUser, comp: Composition) -> CardListResponse:
    return CardListResponse(items=[to_card_dto(card) for card in comp.cards.list(user)])


@router.patch("/cards/{cardId}", operation_id="updateCard", responses=problem_responses(404, 422))
def update_card(
    card_id: CardIdPath, body: UpdateCardRequest, user: CsrfUser, comp: Composition
) -> CardDto:
    return to_card_dto(comp.cards.set_nickname(user, card_id, body.nickname))
