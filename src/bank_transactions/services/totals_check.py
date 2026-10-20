"""Declared-versus-calculated statement totals, the import's only data-quality check."""

from dataclasses import dataclass
from decimal import Decimal

from bank_transactions.domain.models import StatementTotals
from bank_transactions.domain.money import Currency, round_amount


@dataclass(frozen=True)
class TotalsComparison:
    """`available` is False when the file declared no totals: the check could not run, which is
    neither a pass nor a mismatch. `mismatched` lists currency buckets (None = unknown currency)."""

    available: bool
    mismatched: tuple[Currency | None, ...] = ()


def compare_totals(declared: StatementTotals, calculated: StatementTotals) -> TotalsComparison:
    if not declared.amounts:
        return TotalsComparison(available=False)
    declared_by_currency = declared.by_currency
    calculated_by_currency = calculated.by_currency
    currencies = {*declared_by_currency, *calculated_by_currency}
    mismatched = [
        currency
        for currency in currencies
        if _rounded(declared_by_currency.get(currency))
        != _rounded(calculated_by_currency.get(currency))
    ]
    return TotalsComparison(True, tuple(sorted(mismatched, key=_sort_key)))


def _rounded(amount: Decimal | None) -> Decimal:
    return round_amount(amount if amount is not None else Decimal(0))


def _sort_key(currency: Currency | None) -> str:
    return currency.code if currency else ""
