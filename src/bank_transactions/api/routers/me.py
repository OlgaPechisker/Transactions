"""The signed-in user's profile and counters."""

from fastapi import APIRouter

from bank_transactions.api.dependencies import Composition, CurrentUser
from bank_transactions.api.schemas.auth import MeDto

router = APIRouter(tags=["Auth"])


@router.get("/me", operation_id="getMe")
def get_me(user: CurrentUser, comp: Composition) -> MeDto:
    comp.profile.me(user)
    raise NotImplementedError
