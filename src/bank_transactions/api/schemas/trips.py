"""Trip DTOs."""

from datetime import date

from pydantic import Field

from bank_transactions.api.schemas.common import (
    ApiModel,
    BillingSummaryDto,
    TransactionListItemDto,
)


class CreateTripRequest(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    start_date: date
    end_date: date


class TripSummaryStatsDto(BillingSummaryDto):
    tagged_count: int = Field(ge=0)
    auto_count: int = Field(ge=0)


class TripDetailStatsDto(BillingSummaryDto):
    unknown_currency_count: int = Field(ge=0)
    outside_range: BillingSummaryDto


class TripCategoryDto(BillingSummaryDto):
    category: str | None
    label: str


class TripDayDto(BillingSummaryDto):
    date: str


class TripSummaryDto(ApiModel):
    id: str
    name: str
    currency: str
    start_date: str
    end_date: str
    days: int = Field(ge=1)
    stats: TripSummaryStatsDto


class TripListResponse(ApiModel):
    items: list[TripSummaryDto]


class TripDetailDto(ApiModel):
    id: str
    name: str
    currency: str
    start_date: str
    end_date: str
    days: int = Field(ge=1)
    stats: TripDetailStatsDto
    by_category: list[TripCategoryDto]
    by_day: list[TripDayDto]
    tagged: list[TransactionListItemDto]
    auto: list[TransactionListItemDto]
    excluded: list[TransactionListItemDto]


class TripCandidatesResponse(ApiModel):
    items: list[TransactionListItemDto]
