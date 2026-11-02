"""Transaction DTOs. List and detail DTOs live in `common.py` (shared with trips)."""

from pydantic import Field

from bank_transactions.api.schemas.common import ApiModel


class UpdateTransactionRequest(ApiModel):
    custom_category: str | None = Field(min_length=1, max_length=60)
