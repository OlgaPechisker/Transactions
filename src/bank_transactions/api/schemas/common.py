"""DTOs shared by several resources, mirroring `docs/api-contract/openapi.yaml` components.

Nullable fields are always present on the wire (never omitted), so models keep
`None` values when serialised."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class MoneyDto(ApiModel):
    amount: str = Field(pattern=r"^-?\d+\.\d{2}$")
    currency: str | None = Field(pattern=r"^[A-Z]{3}$")


class CardRefDto(ApiModel):
    id: str
    label: str
    issuer: str
    last4: str = Field(pattern=r"^\d{4}$")
    nickname: str | None
    owner_name: str


class PersonDto(ApiModel):
    name: str


class CardDto(ApiModel):
    id: str
    label: str
    issuer: str
    last4: str = Field(pattern=r"^\d{4}$")
    nickname: str | None
    owner: PersonDto


class CategoryDto(ApiModel):
    bank: str | None
    custom: str | None
    effective: str | None
    source: str


class TripRefDto(ApiModel):
    id: str
    name: str
    membership: str


class InstallmentDto(ApiModel):
    number: int
    total: int


class TransactionListItemDto(ApiModel):
    id: str
    transaction_date: str
    charge_date: str | None
    merchant: str
    city: str | None
    country: str | None
    category: CategoryDto
    card: CardRefDto
    type: str
    installment: InstallmentDto | None
    original: MoneyDto | None
    billing: MoneyDto
    effective_rate: str | None
    commission: MoneyDto | None
    trip: TripRefDto | None
    notes: str | None
    review_reasons: list[str]


class FxDto(ApiModel):
    stated_rate: str | None
    effective_rate: str
    deviation_percent: str | None
    commission: MoneyDto


class InstallmentPlanDto(ApiModel):
    number: int
    total: int
    plan_total: MoneyDto
    per_payment: MoneyDto
    remaining_payments: int
    remaining_amount: MoneyDto


class TransactionDetailDto(TransactionListItemDto):
    fx: FxDto | None
    installment_plan: InstallmentPlanDto | None
    imported_at: str


class BillingTotalDto(ApiModel):
    count: int = Field(ge=1)
    net: MoneyDto


class BillingSummaryDto(ApiModel):
    count: int = Field(ge=0)
    unknown_billing_currency_count: int = Field(ge=0)
    billing_totals: list[BillingTotalDto]


class GroupDto(BillingSummaryDto):
    key: str | None
    label: str


class PageMeta(ApiModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int


class ProblemErrorDto(ApiModel):
    field: str
    code: str
    message: str


class ProblemDto(ApiModel):
    """`application/problem+json`. Optional members are omitted when absent."""

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None
    instance: str | None = None
    errors: list[ProblemErrorDto] | None = None
