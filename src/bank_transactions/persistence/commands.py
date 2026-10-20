"""Immutable write commands. They carry ciphertext and ids only, never plaintext identifiers."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from bank_transactions.domain.enums import (
    Channel,
    ImportStatus,
    IssuerCode,
    ReviewReason,
    TransactionType,
)
from bank_transactions.domain.ids import (
    AccountId,
    CardId,
    IdentifierToken,
    ImportRunId,
    InstallmentPlanId,
    OwnerId,
)
from bank_transactions.domain.immutability import freeze_field
from bank_transactions.domain.keys import OwnerMatchKey
from bank_transactions.domain.money import Currency, round_amount, round_rate


@dataclass(frozen=True)
class NewImportRun:
    """file_path and file_name are hash-based safe labels, never the raw path or filename."""

    file_path: str
    file_name: str
    file_sha256: str
    started_at: datetime


@dataclass(frozen=True)
class ImportRunOutcome:
    status: ImportStatus
    finished_at: datetime
    issuer_code: IssuerCode | None
    rows_total: int
    rows_imported: int
    rows_duplicate: int
    rows_skipped: int
    error_summary: str | None

    def __post_init__(self) -> None:
        if self.status is ImportStatus.RUNNING:
            raise ValueError("an outcome must be a terminal status")
        counts = (self.rows_total, self.rows_imported, self.rows_duplicate, self.rows_skipped)
        if any(count < 0 for count in counts):
            raise ValueError("row counts cannot be negative")


@dataclass(frozen=True)
class NewOwner:
    display_name: str
    match_key: OwnerMatchKey

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("an owner needs a name")


@dataclass(frozen=True)
class NewAccount:
    owner_id: OwnerId
    bank_code: str | None
    account_number_enc: IdentifierToken | None
    display_name: str | None


@dataclass(frozen=True)
class NewCard:
    owner_id: OwnerId
    account_id: AccountId
    issuer_code: IssuerCode
    last4_enc: IdentifierToken
    display_name: str | None


@dataclass(frozen=True)
class NewInstallmentPlan:
    """Natural identity: (card, issuer_reference) when a reference exists, and always the
    unconditional (card, merchant, purchase date, total, currency, count) key."""

    card_id: CardId
    issuer_reference: str | None
    merchant_name_norm: str
    purchase_date: date
    total_amount: Decimal
    currency: Currency
    total_installments: int

    def __post_init__(self) -> None:
        if self.total_installments < 1:
            raise ValueError("a plan has at least one installment")
        object.__setattr__(self, "total_amount", round_amount(self.total_amount))


@dataclass(frozen=True)
class NewTransaction:
    """Amounts are quantized here, once, to 2 places half-up; repositories store them as given."""

    card_id: CardId
    import_run_id: ImportRunId
    issuer_reference: str | None
    transaction_date: date
    charge_date: date
    billing_amount: Decimal
    billing_currency: Currency | None
    original_amount: Decimal | None
    original_currency: Currency | None
    merchant_name_raw: str
    merchant_name_norm: str
    merchant_city: str | None
    merchant_country: str | None
    bank_category_raw: str | None
    transaction_type: TransactionType
    channel: Channel | None
    notes: str | None
    installment_plan_id: InstallmentPlanId | None
    installment_number: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "billing_amount", round_amount(self.billing_amount))
        if self.original_amount is not None:
            object.__setattr__(self, "original_amount", round_amount(self.original_amount))


@dataclass(frozen=True)
class NewForeignDetail:
    """Commission is quantized to 2 places and both rates to 6 places, half-up, once."""

    commission_amount: Decimal
    stated_fx_rate: Decimal | None
    effective_fx_rate: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "commission_amount", round_amount(self.commission_amount))
        if self.stated_fx_rate is not None:
            object.__setattr__(self, "stated_fx_rate", round_rate(self.stated_fx_rate))
        object.__setattr__(self, "effective_fx_rate", round_rate(self.effective_fx_rate))


@dataclass(frozen=True)
class NewTransactionAggregate:
    transaction: NewTransaction
    review_reasons: frozenset[ReviewReason] = field(default=frozenset())
    foreign_detail: NewForeignDetail | None = None

    def __post_init__(self) -> None:
        freeze_field(self, "review_reasons", frozenset)
