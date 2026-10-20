"""Pure mapping from parsed transactions plus resolved ids to keys and insertion commands."""

from bank_transactions.domain.ids import CardId, ImportRunId, InstallmentPlanId
from bank_transactions.domain.keys import ComparisonKey, IssuerReferenceKey
from bank_transactions.domain.models import ParsedInstallmentPlan, ParsedTransaction
from bank_transactions.persistence.commands import (
    NewForeignDetail,
    NewInstallmentPlan,
    NewTransaction,
    NewTransactionAggregate,
)


class TransactionMapper:
    """Never mutates parsed objects and never touches a database."""

    @staticmethod
    def reference_key(tx: ParsedTransaction, card_id: CardId) -> IssuerReferenceKey | None:
        if tx.issuer_reference is None:
            return None
        return IssuerReferenceKey(
            card_id, tx.transaction_date, tx.issuer_reference, _installment_number(tx)
        )

    @staticmethod
    def comparison_key(tx: ParsedTransaction, card_id: CardId) -> ComparisonKey | None:
        if tx.issuer_reference is not None:
            return None
        original = tx.original
        return ComparisonKey(
            card_id,
            tx.transaction_date,
            tx.merchant_name_norm,
            original.amount if original else None,
            original.currency if original else None,
            _installment_number(tx),
        )

    @staticmethod
    def plan_command(plan: ParsedInstallmentPlan, card_id: CardId) -> NewInstallmentPlan:
        currency = plan.total.currency
        if currency is None:
            raise ValueError("an installment plan needs a known currency")
        return NewInstallmentPlan(
            card_id,
            plan.issuer_reference,
            plan.merchant_name_norm,
            plan.purchase_date,
            plan.total.amount,
            currency,
            plan.total_installments,
        )

    @staticmethod
    def to_aggregate(
        tx: ParsedTransaction,
        card_id: CardId,
        run_id: ImportRunId,
        plan_id: InstallmentPlanId | None = None,
    ) -> NewTransactionAggregate:
        original = tx.original
        transaction = NewTransaction(
            card_id=card_id,
            import_run_id=run_id,
            issuer_reference=tx.issuer_reference,
            transaction_date=tx.transaction_date,
            charge_date=tx.charge_date,
            billing_amount=tx.billing.amount,
            billing_currency=tx.billing.currency,
            original_amount=original.amount if original else None,
            original_currency=original.currency if original else None,
            merchant_name_raw=tx.merchant_name_raw,
            merchant_name_norm=tx.merchant_name_norm,
            merchant_city=tx.merchant_city,
            merchant_country=tx.merchant_country,
            bank_category_raw=tx.bank_category_raw,
            transaction_type=tx.transaction_type,
            channel=tx.channel,
            notes=tx.notes,
            installment_plan_id=plan_id,
            installment_number=_installment_number(tx),
        )
        return NewTransactionAggregate(transaction, tx.review_reasons, _foreign_detail(tx))


def _installment_number(tx: ParsedTransaction) -> int | None:
    return tx.installment.installment_number if tx.installment else None


def _foreign_detail(tx: ParsedTransaction) -> NewForeignDetail | None:
    if tx.fx is None:
        return None
    return NewForeignDetail(tx.fx.commission_amount, tx.fx.stated_fx_rate, tx.fx.effective_fx_rate)
