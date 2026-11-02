"""Card DTOs."""

from pydantic import Field

from bank_transactions.api.schemas.common import ApiModel, CardDto


class CardListResponse(ApiModel):
    items: list[CardDto]


class UpdateCardRequest(ApiModel):
    nickname: str | None = Field(max_length=120)
