"""Effective FX rate and FX detail arithmetic."""

from decimal import Decimal

from bank_transactions.domain.models import ParsedFxDetail
from bank_transactions.domain.money import Currency, Money, effective_fx_rate


def currencies_differ(billing: Currency | None, original: Currency | None) -> bool:
    """True when both currencies are known and differ: a foreign charge even on a domestic sheet."""
    return billing is not None and original is not None and billing != original


class EffectiveRateCalculator:
    """Builds the FX detail of a foreign row. The effective rate is the signed billing/original
    ratio at full precision (the domain's `effective_fx_rate`); rounding is a persistence
    concern."""

    def detail(
        self,
        billing: Money,
        original: Money | None,
        commission: Decimal,
        stated_rate: Decimal | None,
    ) -> ParsedFxDetail:
        if billing.currency is None:
            raise ValueError("a foreign row needs a known billing currency")
        if original is None or original.is_zero():
            raise ValueError("a foreign row needs a non-zero original amount")
        return ParsedFxDetail(
            commission_amount=commission,
            stated_fx_rate=stated_rate,
            effective_fx_rate=effective_fx_rate(billing.amount, original.amount),
        )
