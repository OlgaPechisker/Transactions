"""Trip DTOs."""

from datetime import date

from pydantic import Field

from bank_transactions.api.schemas.common import ApiModel


class CreateTripRequest(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    start_date: date
    end_date: date
