"""Currency-validation DTOs."""

from datetime import date

from pydantic import Field

from bank_transactions.api.schemas.common import ApiModel


class CurrencyValidationFilterDto(ApiModel):
    country: list[str] = Field(default_factory=list)
    date_from: date | None = Field(default=None, alias="from")
    date_to: date | None = Field(default=None, alias="to")
    merchant: list[str] = Field(default_factory=list)


class CurrencyValidationBatchRequest(ApiModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    transaction_ids: list[str] | None = Field(default=None, min_length=1)
    filter: CurrencyValidationFilterDto | None = None
