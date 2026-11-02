"""Card DTOs."""

from bank_transactions.api.schemas.common import ApiModel, CardDto


class CardListResponse(ApiModel):
    items: list[CardDto]


class UpdateCardRequest(ApiModel):
    # The length limit applies after trimming, so CardService checks it, not this DTO.
    nickname: str | None
