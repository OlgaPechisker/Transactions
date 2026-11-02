"""Pure query vocabulary shared by the API, the services and the repositories."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from bank_transactions.domain.enums import CategorySource, ReviewReason, TransactionType
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.immutability import freeze_field

__all__ = [
    "MAX_PAGE_SIZE",
    "NONE",
    "UNKNOWN",
    "AnalysisInclude",
    "AnalysisQuery",
    "Bucket",
    "CompareMode",
    "Dimension",
    "Page",
    "PageRequest",
    "PendingValidationFilter",
    "SortSpec",
    "TransactionFilter",
    "TransactionInclude",
]

MAX_PAGE_SIZE = 200
NONE = "none"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PageRequest:
    page: int = 1
    size: int = 50

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page starts at 1")
        if not 1 <= self.size <= MAX_PAGE_SIZE:
            raise ValueError(f"page size must be between 1 and {MAX_PAGE_SIZE}")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


@dataclass(frozen=True)
class Page[T]:
    items: tuple[T, ...]
    page: int
    size: int
    total: int

    def __post_init__(self) -> None:
        freeze_field(self, "items", tuple)
        if self.total < 0:
            raise ValueError("total cannot be negative")

    @property
    def total_pages(self) -> int:
        return -(-self.total // self.size)


@dataclass(frozen=True)
class SortSpec:
    """Ordered (field, descending) pairs. Each service validates the names against its own
    set of sortable fields."""

    fields: tuple[tuple[str, bool], ...]

    def __post_init__(self) -> None:
        freeze_field(self, "fields", tuple)


class Dimension(StrEnum):
    CATEGORY = "category"
    CUSTOM_CATEGORY = "customCategory"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    CARD = "card"
    CURRENCY = "currency"
    MERCHANT = "merchant"
    TYPE = "type"
    TRIP = "trip"
    COUNTRY = "country"


class Bucket(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class CompareMode(StrEnum):
    NONE = "none"
    PREVIOUS = "previous"
    YOY = "yoy"


class TransactionInclude(StrEnum):
    SUMMARY = "summary"
    GROUPS = "groups"


class AnalysisInclude(StrEnum):
    GROUPS = "groups"
    TIME_SERIES = "timeSeries"
    MATRIX = "matrix"


@dataclass(frozen=True)
class TransactionFilter:
    """Every transaction filter of the API. Empty tuples and None mean "no restriction".
    `currencies` may contain UNKNOWN and `trip_ids` may contain NONE."""

    date_from: date | None = None
    date_to: date | None = None
    categories: tuple[str, ...] = ()
    bank_categories: tuple[str, ...] = ()
    custom_categories: tuple[str, ...] = ()
    category_sources: tuple[CategorySource, ...] = ()
    card_ids: tuple[PublicId, ...] = ()
    currencies: tuple[str, ...] = ()
    billing_currency: str | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    merchants: tuple[str, ...] = ()
    types: tuple[TransactionType, ...] = ()
    countries: tuple[str, ...] = ()
    trip_ids: tuple[str, ...] = ()
    needs_review: bool | None = None
    review_reasons: tuple[ReviewReason, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "categories",
            "bank_categories",
            "custom_categories",
            "category_sources",
            "card_ids",
            "currencies",
            "merchants",
            "types",
            "countries",
            "trip_ids",
            "review_reasons",
        ):
            freeze_field(self, name, tuple)
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")
        low, high = self.min_amount, self.max_amount
        if low is not None and high is not None and low > high:
            raise ValueError("min_amount cannot exceed max_amount")


@dataclass(frozen=True)
class PendingValidationFilter:
    """Narrows the currency-validation queue."""

    countries: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    merchants: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        freeze_field(self, "countries", tuple)
        freeze_field(self, "merchants", tuple)


@dataclass(frozen=True)
class AnalysisQuery:
    filter: TransactionFilter
    date_from: date
    date_to: date
    billing_currency: str
    compare: CompareMode = CompareMode.NONE
    group_by: Dimension = Dimension.CATEGORY
    bucket: Bucket = Bucket.MONTH
    series_by: Dimension | None = None
    include: frozenset[AnalysisInclude] = frozenset({AnalysisInclude.GROUPS})

    def __post_init__(self) -> None:
        freeze_field(self, "include", frozenset)
        if self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")
