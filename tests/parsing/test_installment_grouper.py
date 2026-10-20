from datetime import date
from decimal import Decimal

from bank_transactions.domain.enums import TransactionType
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.models import ParsedInstallmentRef
from bank_transactions.domain.money import ILS, USD, Money
from bank_transactions.parsing.shared.installment_grouper import InstallmentPlanBuilder


def installment(make_draft, row, number, *, total=10, plan_total="1300", reference=None, **over):
    ref = ParsedInstallmentRef(
        installment_number=number,
        total_installments=total,
        plan_total=None if plan_total is None else Money(Decimal(plan_total), over.pop("cur", ILS)),
        issuer_reference=reference,
    )
    return make_draft(
        row=row,
        installment=ref,
        merchant_name_raw=over.pop("merchant", "דר עמית"),
        transaction_type=TransactionType.INSTALLMENTS,
        billing=Money(Decimal("130"), ILS),
        **over,
    )


def test_charges_of_one_purchase_share_one_plan_with_the_plan_total_not_the_monthly_amount(
    make_draft,
):
    drafts = [installment(make_draft, 5, 3), installment(make_draft, 6, 4)]

    result = InstallmentPlanBuilder().build(drafts)

    assert len(result.plans) == 1
    plan = result.plans[0]
    assert plan.total == Money(Decimal("1300"), ILS)
    assert plan.total_installments == 10
    assert plan.merchant_name_norm == "דר עמית"
    assert plan.purchase_date == date(2026, 6, 1)
    assert plan.issuer_reference is None
    assert result.plan_for(drafts[0].location) is plan
    assert result.plan_for(drafts[1].location) is plan
    assert not result.is_ambiguous(drafts[0].location)


def test_different_totals_dates_merchants_counts_or_cards_make_separate_plans(
    make_draft, other_card_key
):
    drafts = [
        installment(make_draft, 5, 3),
        installment(make_draft, 6, 3, plan_total="1400"),
        installment(make_draft, 7, 3, transaction_date=date(2026, 5, 1)),
        installment(make_draft, 8, 3, merchant="אחר"),
        installment(make_draft, 9, 3, total=12),
        installment(make_draft, 10, 3, card_key=other_card_key),
    ]

    assert len(InstallmentPlanBuilder().build(drafts).plans) == 6


def test_plan_totals_are_compared_at_money_precision(make_draft):
    drafts = [installment(make_draft, 5, 3, plan_total="1300"), installment(make_draft, 6, 4)]
    drafts[1] = installment(make_draft, 6, 4, plan_total="1300.00")

    assert len(InstallmentPlanBuilder().build(drafts).plans) == 1


def test_a_shared_issuer_reference_identifies_the_plan_even_when_other_facts_differ(make_draft):
    drafts = [
        installment(make_draft, 5, 3, reference="PLAN-1"),
        installment(make_draft, 6, 4, reference="PLAN-1", transaction_date=date(2026, 6, 2)),
    ]

    result = InstallmentPlanBuilder().build(drafts)

    assert len(result.plans) == 1
    assert result.plans[0].issuer_reference == "PLAN-1"
    assert result.plans[0].purchase_date == date(2026, 6, 1)


def test_a_reference_group_that_disagrees_on_the_count_is_ambiguous(make_draft):
    drafts = [
        installment(make_draft, 5, 3, reference="PLAN-1"),
        installment(make_draft, 6, 4, reference="PLAN-1", total=12),
    ]

    result = InstallmentPlanBuilder().build(drafts)

    assert result.plans == ()
    assert all(result.is_ambiguous(draft.location) for draft in drafts)


def test_the_same_installment_number_twice_in_one_plan_is_ambiguous(make_draft):
    drafts = [installment(make_draft, 5, 3), installment(make_draft, 6, 3)]

    result = InstallmentPlanBuilder().build(drafts)

    assert result.plans == ()
    assert result.plan_for(drafts[0].location) is None
    assert all(result.is_ambiguous(draft.location) for draft in drafts)


def test_a_missing_plan_total_or_unknown_plan_currency_is_ambiguous(make_draft):
    no_total = installment(make_draft, 5, 3, plan_total=None)
    unknown_currency = installment(make_draft, 6, 3, cur=None)

    result = InstallmentPlanBuilder().build([no_total, unknown_currency])

    assert result.plans == ()
    assert result.is_ambiguous(no_total.location)
    assert result.is_ambiguous(unknown_currency.location)


def test_foreign_currency_plans_are_allowed(make_draft):
    draft = installment(make_draft, 5, 1, total=3, plan_total="300", cur=USD)

    assert InstallmentPlanBuilder().build([draft]).plans[0].total == Money(Decimal("300"), USD)


def test_rows_that_are_not_installments_are_ignored(make_draft):
    plain = make_draft(row=5)

    result = InstallmentPlanBuilder().build([plain])

    assert result.plans == ()
    assert result.plan_for(plain.location) is None
    assert not result.is_ambiguous(plain.location)


def test_an_unknown_location_is_neither_planned_nor_ambiguous():
    result = InstallmentPlanBuilder().build([])

    assert result.plan_for(SourceLocation(1, 1)) is None
    assert not result.is_ambiguous(SourceLocation(1, 1))
