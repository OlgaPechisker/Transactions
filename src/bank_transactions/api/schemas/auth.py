"""Sign-in and profile DTOs."""

from datetime import datetime

from pydantic import Field

from bank_transactions.api.schemas.common import ApiModel


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
