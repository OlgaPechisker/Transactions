"""Currency-validation DTOs."""

from datetime import date
from typing import Annotated

from pydantic import ConfigDict, Field

from bank_transactions.api.schemas.common import ApiModel, MoneyDto, PageMeta
from bank_transactions.api.schemas.transactions import CountryOptionDto


class CurrencyValidationFilterDto(ApiModel):
    model_config = ConfigDict(extra="forbid")

    country: list[Annotated[str, Field(pattern=r"^[A-Z]{2}$")]] = Field(default_factory=list)
    date_from: date | None = Field(default=None, alias="from")
    date_to: date | None = Field(default=None, alias="to")
    merchant: list[str] = Field(default_factory=list)


class CurrencyValidationBatchRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    transaction_ids: list[str] | None = Field(default=None, min_length=1)
    filter: CurrencyValidationFilterDto | None = None


class CurrencyOptionDto(ApiModel):
    code: str = Field(pattern=r"^[A-Z]{3}$")
    name: str = Field(min_length=1)


class CurrencyValidationQueueDto(ApiModel):
    total: int
    validated: int
    pending: int
    progress_percent: str = Field(pattern=r"^\d+(\.\d+)?$")
    countries: list[CountryOptionDto]
    currency_options: list[CurrencyOptionDto]


class ValidationDto(ApiModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    batch_id: str
    validated_at: str


class CurrencyValidationItemDto(ApiModel):
    id: str
    transaction_date: str
    merchant: str
    country: str | None
    original_amount: str = Field(pattern=r"^-?\d+(\.\d+)?$")
    billing: MoneyDto
    implied_rate: str | None = Field(pattern=r"^\d+\.\d{6}$")
    validation: ValidationDto | None


class CurrencyValidationItemsResponse(ApiModel):
    items: list[CurrencyValidationItemDto]
    meta: PageMeta


class CurrencyValidationBatchDto(ApiModel):
    id: str
    validated_count: int = Field(ge=0)
    transaction_ids: list[str]
    created_at: str
