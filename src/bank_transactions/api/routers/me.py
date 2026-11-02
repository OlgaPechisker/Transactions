"""The signed-in user's profile and counters."""

from fastapi import APIRouter

from bank_transactions.api.dependencies import Composition, CurrentUser
from bank_transactions.api.schemas.auth import MeDto, to_me_dto

router = APIRouter(tags=["Auth"])


@router.get("/me", operation_id="getMe")
def get_me(user: CurrentUser, comp: Composition) -> MeDto:
    return to_me_dto(comp.profile.me(user))
