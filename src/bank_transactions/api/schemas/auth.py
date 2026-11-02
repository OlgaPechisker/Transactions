"""Sign-in and profile DTOs."""

from datetime import datetime

from pydantic import Field

from bank_transactions.api.schemas.common import ApiModel
from bank_transactions.domain.enums import ReviewReason
from bank_transactions.services.profile_service import MeView

OWNER_ROLE = "OWNER"


class SignInRequest(ApiModel):
    email: str
    password: str = Field(min_length=1)


class ReviewReasonCountDto(ApiModel):
    reason: str
    count: int = Field(ge=0)


class MeCountersDto(ApiModel):
    pending_currency_validations: int
    needs_review_by_reason: list[ReviewReasonCountDto]


class MeDto(ApiModel):
    id: str
    name: str
    email: str
    role: str
    csrf_token: str
    counters: MeCountersDto


class SessionResponse(ApiModel):
    token: str
    expires_at: datetime
    user: MeDto


def to_me_dto(me: MeView) -> MeDto:
    pending = me.pending_currency_validations
    return MeDto(
        id=me.public_id,
        name=me.name,
        email=me.email,
        role=OWNER_ROLE,
        csrf_token=me.csrf_token,
        counters=MeCountersDto(
            pending_currency_validations=pending,
            needs_review_by_reason=(
                [ReviewReasonCountDto(reason=ReviewReason.UNKNOWN_CURRENCY.value, count=pending)]
                if pending
                else []
            ),
        ),
    )
