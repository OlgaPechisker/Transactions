"""Transaction DTOs. List and detail DTOs live in `common.py` (shared with trips)."""

from pydantic import Field

from bank_transactions.api.schemas.common import (
    ApiModel,
    BillingSummaryDto,
    CardRefDto,
    GroupDto,
    PageMeta,
    TransactionListItemDto,
)


class UpdateTransactionRequest(ApiModel):
    custom_category: str | None = Field(min_length=1, max_length=60)


class TransactionSummaryDto(BillingSummaryDto):
    unknown_currency_count: int = Field(ge=0)


class TransactionListResponse(ApiModel):
    """`summary` and `groups` are left out of the body when they were not requested."""

    items: list[TransactionListItemDto]
    meta: PageMeta
    summary: TransactionSummaryDto | None = None
    groups: list[GroupDto] | None = None


class BankCategoryOptionDto(ApiModel):
    bank: str
    count: int


class CustomCategoryOptionDto(ApiModel):
    name: str
    count: int


class CurrencyCountDto(ApiModel):
    code: str = Field(pattern=r"^[A-Z]{3}$")
    count: int = Field(ge=1)


class CountryOptionDto(ApiModel):
    code: str = Field(pattern=r"^[A-Z]{2}$")
    count: int


class TripOptionDto(ApiModel):
    id: str
    name: str


class DateRangeDto(ApiModel):
    min: str | None
    max: str | None


class FilterOptionsDto(ApiModel):
    categories: list[BankCategoryOptionDto]
    custom_categories: list[CustomCategoryOptionDto]
    cards: list[CardRefDto]
    currencies: list[CurrencyCountDto]
    unknown_currency_count: int
    billing_currencies: list[CurrencyCountDto]
    unknown_billing_currency_count: int = Field(ge=0)
    types: list[str]
    countries: list[CountryOptionDto]
    trips: list[TripOptionDto]
    date_range: DateRangeDto
