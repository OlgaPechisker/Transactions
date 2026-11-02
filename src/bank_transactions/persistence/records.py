"""Immutable read models returned by repositories."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from bank_transactions.domain.enums import (
    CategorySource,
    ImportStatus,
    IssuerCode,
    ReviewReason,
    TransactionType,
    TripMembership,
    ValidationStatus,
)
from bank_transactions.domain.ids import (
    AccountId,
    BatchId,
    CardId,
    ClaimId,
    CustomCategoryId,
    IdentifierToken,
    OwnerId,
    PublicId,
    RuleId,
    SessionId,
    TransactionId,
    TripId,
    UserId,
)
from bank_transactions.domain.immutability import freeze_field
from bank_transactions.domain.keys import OwnerMatchKey


@dataclass(frozen=True)
class OwnerRecord:
    owner_id: OwnerId
    display_name: str
    match_key: OwnerMatchKey


@dataclass(frozen=True)
class PlaceholderOwnerRecord:
    """The nameless owner a user's API imports hang from; its display name is the user's."""

    owner_id: OwnerId
    user_id: UserId


@dataclass(frozen=True)
class AccountRecord:
    account_id: AccountId
    owner_id: OwnerId
    bank_code: str | None
    account_number_enc: IdentifierToken | None
    display_name: str | None


@dataclass(frozen=True)
class CardRecord:
    card_id: CardId
    owner_id: OwnerId
    account_id: AccountId
    issuer_code: IssuerCode
    last4_enc: IdentifierToken
    display_name: str | None


@dataclass(frozen=True)
class CardLookup:
    """Candidate search: issuer + last4 ciphertext, optionally narrowed by account or owner.
    `user_id` scopes the search to one user's claims (API imports)."""

    issuer_code: IssuerCode
    last4_enc: IdentifierToken
    account_id: AccountId | None = None
    owner_id: OwnerId | None = None
    user_id: UserId | None = None


@dataclass(frozen=True)
class UserRecord:
    user_id: UserId
    public_id: PublicId
    email: str
    display_name: str
    password_hash: str


@dataclass(frozen=True)
class SessionRecord:
    session_id: SessionId
    user_id: UserId
    token_digest: bytes
    expires_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True)
class ClaimRecord:
    claim_id: ClaimId
    user_id: UserId
    issuer_code: IssuerCode
    last4_enc: IdentifierToken


@dataclass(frozen=True)
class VisibleCardRecord:
    """A card the user can see through a claim. `owner_display_name` is None for the
    placeholder owner; callers then fall back to the signed-in user's name."""

    card_id: CardId
    public_id: PublicId
    issuer_code: IssuerCode
    last4_enc: IdentifierToken
    nickname: str | None
    owner_display_name: str | None


@dataclass(frozen=True)
class TransactionRef:
    transaction_id: TransactionId
    public_id: PublicId


@dataclass(frozen=True)
class TripMembershipRef:
    public_id: PublicId
    name: str
    membership: TripMembership


@dataclass(frozen=True)
class TransactionViewRecord:
    """Everything `TransactionListItem` and `TransactionDetail` need. Amounts are stored
    values; `effective_rate` keeps its stored sign and the API layer presents it."""

    transaction_id: TransactionId
    public_id: PublicId
    transaction_date: date
    charge_date: date | None
    merchant: str
    city: str | None
    country: str | None
    bank_category: str | None
    custom_category: str | None
    effective_category: str | None
    category_source: CategorySource
    card: VisibleCardRecord
    transaction_type: TransactionType
    installment_number: int | None
    installment_total: int | None
    original_amount: Decimal | None
    original_currency: str | None
    billing_amount: Decimal
    billing_currency: str | None
    effective_rate: Decimal | None
    commission: Decimal | None
    trip: TripMembershipRef | None
    notes: str | None
    visible_review_reasons: tuple[ReviewReason, ...] = ()
    stated_fx_rate: Decimal | None = None
    imported_at: datetime | None = None
    plan_total_amount: Decimal | None = None
    plan_currency: str | None = None

    def __post_init__(self) -> None:
        freeze_field(self, "visible_review_reasons", tuple)


@dataclass(frozen=True)
class BillingTotalRecord:
    currency: str
    count: int
    net: Decimal


@dataclass(frozen=True)
class BillingSummaryRecord:
    """count = sum of totals' counts + unknown_billing_currency_count."""

    count: int
    unknown_billing_currency_count: int
    totals: tuple[BillingTotalRecord, ...] = ()
    unknown_currency_count: int = 0

    def __post_init__(self) -> None:
        freeze_field(self, "totals", tuple)


@dataclass(frozen=True)
class GroupRecord:
    key: str | None
    summary: BillingSummaryRecord


@dataclass(frozen=True)
class NamedCountRecord:
    name: str
    count: int


@dataclass(frozen=True)
class CodeCountRecord:
    code: str
    count: int


@dataclass(frozen=True)
class FilterOptionsRecord:
    bank_categories: tuple[NamedCountRecord, ...]
    custom_categories: tuple[NamedCountRecord, ...]
    cards: tuple[VisibleCardRecord, ...]
    original_currencies: tuple[CodeCountRecord, ...]
    billing_currencies: tuple[CodeCountRecord, ...]
    countries: tuple[CodeCountRecord, ...]
    types: tuple[TransactionType, ...]

    def __post_init__(self) -> None:
        for name in (
            "bank_categories",
            "custom_categories",
            "cards",
            "original_currencies",
            "billing_currencies",
            "countries",
            "types",
        ):
            freeze_field(self, name, tuple)


@dataclass(frozen=True)
class ImportSummaryRecord:
    public_id: PublicId
    file_sha256: str
    started_at: datetime
    status: ImportStatus
    rows_imported: int


@dataclass(frozen=True)
class TripRecord:
    trip_id: TripId
    public_id: PublicId
    name: str
    currency: str
    start_date: date
    end_date: date


@dataclass(frozen=True)
class TripSummaryRecord:
    trip: TripRecord
    summary: BillingSummaryRecord
    tagged_count: int
    auto_count: int


@dataclass(frozen=True)
class TripMembersRecord:
    tagged: tuple[TransactionViewRecord, ...] = ()
    auto: tuple[TransactionViewRecord, ...] = ()
    excluded: tuple[TransactionViewRecord, ...] = ()

    def __post_init__(self) -> None:
        for name in ("tagged", "auto", "excluded"):
            freeze_field(self, name, tuple)


@dataclass(frozen=True)
class DayBreakdownRecord:
    day: date
    summary: BillingSummaryRecord


@dataclass(frozen=True)
class TripBreakdownRecord:
    overall: BillingSummaryRecord
    outside_range: BillingSummaryRecord
    by_category: tuple[GroupRecord, ...] = ()
    by_day: tuple[DayBreakdownRecord, ...] = ()

    def __post_init__(self) -> None:
        freeze_field(self, "by_category", tuple)
        freeze_field(self, "by_day", tuple)


@dataclass(frozen=True)
class CustomCategoryRecord:
    category_id: CustomCategoryId
    name: str
    name_key: str


@dataclass(frozen=True)
class MappingRecord:
    bank_category: str
    category_id: CustomCategoryId
    category_name: str


@dataclass(frozen=True)
class RuleRecord:
    rule_id: RuleId
    public_id: PublicId
    terms: tuple[str, ...]
    category_id: CustomCategoryId
    category_name: str
    matched_count: int = 0

    def __post_init__(self) -> None:
        freeze_field(self, "terms", tuple)


@dataclass(frozen=True)
class BatchRecord:
    batch_id: BatchId
    public_id: PublicId
    user_id: UserId
    currency: str
    created_at: datetime
    undone_at: datetime | None
    validated_count: int


@dataclass(frozen=True)
class BatchMemberState:
    """A batch member as it is now. `validation_batch_id` is the batch of the transaction's
    current validation row (None when it was moved back)."""

    transaction_id: TransactionId
    public_id: PublicId
    validation_batch_id: BatchId | None
    original_currency: str | None


@dataclass(frozen=True)
class ValidationItemRecord:
    transaction: TransactionViewRecord
    status: ValidationStatus
    validated_currency: str | None = None
    validated_at: datetime | None = None
    batch_public_id: PublicId | None = None


@dataclass(frozen=True)
class AnalysisGroupRecord:
    key: str | None
    count: int
    net: Decimal


@dataclass(frozen=True)
class SeriesPointRecord:
    bucket_start: date
    series_key: str | None
    count: int
    net: Decimal


@dataclass(frozen=True)
class MatrixCellRecord:
    row_key: str | None
    bucket_start: date
    count: int
    net: Decimal


@dataclass(frozen=True)
class CountryOptionRecord:
    code: str | None
    count: int
