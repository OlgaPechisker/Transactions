"""Reads the "charge N of M" facts of one row: the monthly original, the plan total, and
whether the row is too unclear to join a plan."""

from dataclasses import dataclass

from bank_transactions.domain.enums import ReviewReason
from bank_transactions.domain.models import ParsedInstallmentRef
from bank_transactions.domain.money import Money
from bank_transactions.parsing.normalizers.text import parse_installment_note


@dataclass(frozen=True)
class InstallmentFields:
    ref: ParsedInstallmentRef | None
    original: Money | None
    review_reasons: frozenset[ReviewReason]


def read_installment_fields(
    note: str | None, source_amount: Money | None, billing: Money
) -> InstallmentFields:
    """`source_amount` is the issuer's transaction amount, which for installments is the whole
    purchase. The row's `original` becomes the monthly charge expressed in that currency, so the
    plan total is kept separately on the reference. An unusable note gives no reference, keeps
    the source amount as original and flags the row AMBIGUOUS_INSTALLMENT."""
    ambiguous = frozenset({ReviewReason.AMBIGUOUS_INSTALLMENT})
    position = parse_installment_note(note)
    if position is None or not 1 <= position[0] <= position[1]:
        return InstallmentFields(None, source_amount, ambiguous)
    ref = ParsedInstallmentRef(position[0], position[1], plan_total=source_amount)
    if source_amount is None:
        return InstallmentFields(ref, None, frozenset())
    if _currencies_conflict(source_amount, billing):
        return InstallmentFields(ref, None, ambiguous)
    return InstallmentFields(ref, Money(billing.amount, source_amount.currency), frozenset())


def _currencies_conflict(source_amount: Money, billing: Money) -> bool:
    known = source_amount.currency is not None and billing.currency is not None
    return known and source_amount.currency != billing.currency
