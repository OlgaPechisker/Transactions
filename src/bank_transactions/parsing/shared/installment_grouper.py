"""Groups parsed "charge N of M" references into installment plans."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.models import CardHintKey, ParsedInstallmentPlan
from bank_transactions.domain.money import Currency, round_amount
from bank_transactions.parsing.shared.draft import TransactionDraft

_AttributeKey = tuple[CardHintKey, str, date, Decimal, Currency, int]


@dataclass(frozen=True)
class InstallmentPlans:
    """The plans of a statement and, per charge, the plan it belongs to or why it has none."""

    plans: tuple[ParsedInstallmentPlan, ...]
    by_location: Mapping[SourceLocation, ParsedInstallmentPlan]
    ambiguous: frozenset[SourceLocation]

    def __post_init__(self) -> None:
        object.__setattr__(self, "by_location", MappingProxyType(dict(self.by_location)))

    def plan_for(self, location: SourceLocation) -> ParsedInstallmentPlan | None:
        return self.by_location.get(location)

    def is_ambiguous(self, location: SourceLocation) -> bool:
        return location in self.ambiguous


class InstallmentPlanBuilder:
    """A plan is identified by the issuer's plan reference when a charge carries one, otherwise
    by card + merchant + purchase date + plan total + currency + installment count. Groups whose
    charges contradict each other (repeated installment number, differing count or total, no
    usable total) get no plan: their charges are reported as ambiguous instead."""

    def build(self, drafts: Sequence[TransactionDraft]) -> InstallmentPlans:
        groups: dict[object, list[TransactionDraft]] = {}
        ambiguous: set[SourceLocation] = set()
        for draft in drafts:
            if draft.installment is None:
                continue
            key = self._group_key(draft)
            if key is None:
                ambiguous.add(draft.location)
                continue
            groups.setdefault(key, []).append(draft)
        plans: list[ParsedInstallmentPlan] = []
        by_location: dict[SourceLocation, ParsedInstallmentPlan] = {}
        for members in groups.values():
            plan = self._plan(members)
            if plan is None:
                ambiguous.update(member.location for member in members)
                continue
            plans.append(plan)
            by_location.update({member.location: plan for member in members})
        return InstallmentPlans(tuple(plans), by_location, frozenset(ambiguous))

    @staticmethod
    def _group_key(draft: TransactionDraft) -> object | None:
        ref = draft.installment
        if ref is None or ref.plan_total is None or ref.plan_total.currency is None:
            return None
        if ref.issuer_reference:
            return ("reference", draft.card_key, ref.issuer_reference)
        attributes: _AttributeKey = (
            draft.card_key,
            draft.merchant_name_norm,
            draft.transaction_date,
            round_amount(ref.plan_total.amount),
            ref.plan_total.currency,
            ref.total_installments,
        )
        return ("attributes", attributes)

    @staticmethod
    def _plan(members: list[TransactionDraft]) -> ParsedInstallmentPlan | None:
        refs = [member.installment for member in members if member.installment is not None]
        first = refs[0]
        numbers = [ref.installment_number for ref in refs]
        consistent = all(
            ref.total_installments == first.total_installments
            and ref.plan_total is not None
            and first.plan_total is not None
            and ref.plan_total.currency == first.plan_total.currency
            and round_amount(ref.plan_total.amount) == round_amount(first.plan_total.amount)
            for ref in refs
        )
        if len(set(numbers)) != len(numbers) or not consistent or first.plan_total is None:
            return None
        earliest = min(members, key=lambda member: member.transaction_date)
        return ParsedInstallmentPlan(
            card_key=earliest.card_key,
            merchant_name_norm=earliest.merchant_name_norm,
            purchase_date=earliest.transaction_date,
            total=first.plan_total,
            total_installments=first.total_installments,
            issuer_reference=first.issuer_reference,
        )
