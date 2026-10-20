"""Pre-finalisation row data. Parsers produce drafts; the shared algorithms fold fees into
them and attach installment plans before immutable ParsedTransactions are built."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from bank_transactions.domain.enums import Channel, ReviewReason, TransactionType
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.models import (
    CardHintKey,
    FeeCandidate,
    ParsedInstallmentRef,
)
from bank_transactions.domain.money import Money
from bank_transactions.domain.text import fold_text
from bank_transactions.parsing.normalizers.text import MerchantParts, split_merchant


@dataclass(frozen=True)
class TransactionDraft:
    """One charged source row. `is_foreign` is the transient marker that becomes
    ParsedTransaction.fx; `commission` accumulates folded fees (billing currency)."""

    card_key: CardHintKey
    location: SourceLocation
    transaction_date: date
    charge_date: date
    billing: Money
    merchant_name_raw: str
    transaction_type: TransactionType
    original: Money | None = None
    bank_category_raw: str | None = None
    channel: Channel | None = None
    notes: str | None = None
    issuer_reference: str | None = None
    installment: ParsedInstallmentRef | None = None
    is_foreign: bool = False
    stated_fx_rate: Decimal | None = None
    commission: Decimal = Decimal(0)
    review_reasons: frozenset[ReviewReason] = field(default=frozenset())

    @property
    def merchant_parts(self) -> MerchantParts:
        return split_merchant(self.merchant_name_raw)

    @property
    def merchant_name_norm(self) -> str:
        return fold_text(self.merchant_parts.name)


@dataclass(frozen=True)
class FeeMarker:
    """A recognised fee row: the domain candidate plus the ordinary transaction to persist when
    no parent is found."""

    candidate: FeeCandidate
    fallback: TransactionDraft

    @classmethod
    def of(cls, fee_row: TransactionDraft) -> "FeeMarker":
        candidate = FeeCandidate(
            card_key=fee_row.card_key,
            location=fee_row.location,
            transaction_date=fee_row.transaction_date,
            charge_date=fee_row.charge_date,
            amount=fee_row.billing,
        )
        return cls(candidate, fee_row)
