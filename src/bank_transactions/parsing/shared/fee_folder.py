"""Recognises foreign-commission rows and folds each into its nearest foreign parent."""

from collections.abc import Iterable, Sequence
from dataclasses import replace

from bank_transactions.domain.enums import ReviewReason
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.models import FeeCandidate
from bank_transactions.domain.text import fold_text
from bank_transactions.parsing.shared.draft import FeeMarker, TransactionDraft

DEFAULT_FEE_WINDOW_DAYS = 3
DEFAULT_FEE_KEYWORDS: tuple[str, ...] = (
    'עמלת מט"ח',
    "עמלת מטח",
    "עמלת המרה",
    "עמלת המרת מטבע",
    'עמלת עסקה בחו"ל',
    'עמלת חו"ל',
)


class ForeignFeeMatcher:
    """Case-, whitespace- and quote-insensitive keyword test on a merchant name."""

    def __init__(self, keywords: Iterable[str]) -> None:
        folded = [fold_text(keyword) for keyword in keywords]
        if not all(folded):
            raise ValueError("a fee keyword cannot be blank")
        self._keywords = tuple(folded)

    def is_fee(self, merchant_name: str) -> bool:
        name = fold_text(merchant_name)
        return any(keyword in name for keyword in self._keywords)


class ForeignFeeFolder:
    """Matches each fee to the nearest unmatched foreign transaction of the same card and billing
    currency within `date_window_days` of the fee's transaction date; a parent absorbs at most one
    fee. Fees without a parent come back as ordinary transactions flagged UNMATCHED_FOREIGN_FEE,
    so statement totals never change."""

    def __init__(self, date_window_days: int = DEFAULT_FEE_WINDOW_DAYS) -> None:
        if date_window_days < 0:
            raise ValueError("the fee date window cannot be negative")
        self._window = date_window_days

    def fold(
        self, drafts: Sequence[TransactionDraft], fees: Sequence[FeeMarker]
    ) -> tuple[TransactionDraft, ...]:
        resolved = {draft.location: draft for draft in drafts}
        absorbed: set[SourceLocation] = set()
        leftovers: list[TransactionDraft] = []
        for marker in sorted(fees, key=lambda item: _order(item.candidate.location)):
            parent = self._nearest_parent(marker.candidate, resolved.values(), absorbed)
            if parent is None:
                leftovers.append(_as_unmatched(marker.fallback))
                continue
            resolved[parent.location] = _absorb(parent, marker.candidate)
            absorbed.add(parent.location)
        return tuple(sorted([*resolved.values(), *leftovers], key=lambda d: _order(d.location)))

    def _nearest_parent(
        self,
        fee: FeeCandidate,
        drafts: Iterable[TransactionDraft],
        absorbed: set[SourceLocation],
    ) -> TransactionDraft | None:
        compatible = [
            draft
            for draft in drafts
            if draft.location not in absorbed and self._is_compatible(draft, fee)
        ]
        return min(compatible, key=lambda draft: _closeness(draft, fee), default=None)

    def _is_compatible(self, parent: TransactionDraft, fee: FeeCandidate) -> bool:
        gap = abs((parent.transaction_date - fee.transaction_date).days)
        return (
            parent.is_foreign
            and parent.card_key == fee.card_key
            and fee.amount.currency is not None
            and parent.billing.currency == fee.amount.currency
            and gap <= self._window
        )


def _closeness(parent: TransactionDraft, fee: FeeCandidate) -> tuple[int, int, int, int, int]:
    return (
        abs((parent.transaction_date - fee.transaction_date).days),
        0 if parent.charge_date == fee.charge_date else 1,
        abs(parent.location.sheet_position - fee.location.sheet_position),
        abs(parent.location.row - fee.location.row),
        parent.location.row,
    )


def _order(location: SourceLocation) -> tuple[int, int]:
    return location.sheet_position, location.row


def _absorb(parent: TransactionDraft, fee: FeeCandidate) -> TransactionDraft:
    return replace(
        parent,
        billing=parent.billing + fee.amount,
        commission=parent.commission + fee.amount.amount,
    )


def _as_unmatched(fee_row: TransactionDraft) -> TransactionDraft:
    return replace(
        fee_row,
        is_foreign=False,
        review_reasons=fee_row.review_reasons | {ReviewReason.UNMATCHED_FOREIGN_FEE},
    )
