"""Card DTOs."""

from pydantic import Field

from bank_transactions.api.schemas.common import ApiModel, CardDto


class CardListResponse(ApiModel):
    items: list[CardDto]


class UpdateCardRequest(ApiModel):
    # The length limit applies after trimming, so CardService enforces it; the schema extra only
    # documents it in the OpenAPI.
    nickname: str | None = Field(..., json_schema_extra={"maxLength": 120})
