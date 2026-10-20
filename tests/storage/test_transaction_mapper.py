from datetime import date
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import (
    Channel,
    IssuerCode,
    ReviewReason,
    TransactionType,
)
from bank_transactions.domain.ids import CardId, ImportRunId, InstallmentPlanId
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.keys import ComparisonKey, IssuerReferenceKey
from bank_transactions.domain.models import (
    CardHintKey,
    ParsedFxDetail,
    ParsedInstallmentPlan,
    ParsedInstallmentRef,
    ParsedTransaction,
)
from bank_transactions.domain.money import ILS, USD, Money
from bank_transactions.persistence.commands import NewInstallmentPlan
from bank_transactions.persistence.mappers import TransactionMapper

CARD_KEY = CardHintKey(IssuerCode.MAX, "4396")
CARD_ID = CardId(7)
RUN_ID = ImportRunId(3)


def parsed(**overrides) -> ParsedTransaction:
    values = {
        "card_key": CARD_KEY,
        "location": SourceLocation(sheet_position=1, row=5),
        "transaction_date": date(2026, 7, 3),
        "charge_date": date(2026, 8, 2),
        "billing": Money(Decimal("120.005"), ILS),
        "merchant_name_raw": "Super  Pharm",
        "merchant_name_norm": "super pharm",
        "transaction_type": TransactionType.REGULAR,
    }
    return ParsedTransaction(**{**values, **overrides})


def test_a_referenced_transaction_has_a_reference_key_and_no_comparison_key():
    tx = parsed(issuer_reference="V123")

    assert TransactionMapper.reference_key(tx, CARD_ID) == IssuerReferenceKey(
        CARD_ID, date(2026, 7, 3), "V123", None
    )
    assert TransactionMapper.comparison_key(tx, CARD_ID) is None


def test_an_installment_charge_keeps_its_number_in_the_reference_key():
    tx = parsed(
        issuer_reference="V123",
        transaction_type=TransactionType.INSTALLMENTS,
        installment=ParsedInstallmentRef(4, 10),
    )

    key = TransactionMapper.reference_key(tx, CARD_ID)

    assert key == IssuerReferenceKey(CARD_ID, date(2026, 7, 3), "V123", 4)


def test_a_reference_less_transaction_has_the_six_field_comparison_key():
    tx = parsed(original=Money(Decimal("19.9"), USD))

    assert TransactionMapper.reference_key(tx, CARD_ID) is None
    assert TransactionMapper.comparison_key(tx, CARD_ID) == ComparisonKey(
        CARD_ID, date(2026, 7, 3), "super pharm", Decimal("19.90"), USD, None
    )


def test_a_transaction_without_original_amount_compares_with_nulls():
    key = TransactionMapper.comparison_key(parsed(), CARD_ID)

    assert key == ComparisonKey(CARD_ID, date(2026, 7, 3), "super pharm", None, None, None)


def test_an_installment_charge_without_reference_compares_by_installment_number():
    tx = parsed(
        transaction_type=TransactionType.INSTALLMENTS,
        installment=ParsedInstallmentRef(4, 10),
        original=Money(Decimal("130.00"), ILS),
    )

    key = TransactionMapper.comparison_key(tx, CARD_ID)

    assert key is not None
    assert key.installment_number == 4


def test_plan_command_carries_the_card_and_plan_total():
    plan = ParsedInstallmentPlan(
        card_key=CARD_KEY,
        merchant_name_norm="mega store",
        purchase_date=date(2026, 4, 2),
        total=Money(Decimal("1300"), ILS),
        total_installments=10,
        issuer_reference="P9",
    )

    assert TransactionMapper.plan_command(plan, CARD_ID) == NewInstallmentPlan(
        CARD_ID, "P9", "mega store", date(2026, 4, 2), Decimal("1300.00"), ILS, 10
    )


def test_to_aggregate_maps_every_field_with_the_resolved_ids():
    tx = parsed(
        issuer_reference="V123",
        original=Money(Decimal("100"), USD),
        merchant_city="NEW YORK",
        merchant_country="US",
        bank_category_raw="Food",
        channel=Channel.INTERNET,
        notes="note",
        review_reasons=frozenset({ReviewReason.UNKNOWN_CURRENCY}),
    )

    aggregate = TransactionMapper.to_aggregate(tx, CARD_ID, RUN_ID)

    mapped = aggregate.transaction
    assert (mapped.card_id, mapped.import_run_id) == (CARD_ID, RUN_ID)
    assert mapped.issuer_reference == "V123"
    assert (mapped.transaction_date, mapped.charge_date) == (date(2026, 7, 3), date(2026, 8, 2))
    assert (mapped.billing_amount, mapped.billing_currency) == (Decimal("120.01"), ILS)
    assert (mapped.original_amount, mapped.original_currency) == (Decimal("100.00"), USD)
    assert (mapped.merchant_name_raw, mapped.merchant_name_norm) == (
        "Super  Pharm",
        "super pharm",
    )
    assert (mapped.merchant_city, mapped.merchant_country) == ("NEW YORK", "US")
    assert mapped.bank_category_raw == "Food"
    assert mapped.transaction_type is TransactionType.REGULAR
    assert mapped.channel is Channel.INTERNET
    assert mapped.notes == "note"
    assert mapped.installment_plan_id is None
    assert mapped.installment_number is None
    assert aggregate.review_reasons == frozenset({ReviewReason.UNKNOWN_CURRENCY})
    assert aggregate.foreign_detail is None


def test_to_aggregate_links_the_installment_plan_and_number():
    tx = parsed(
        transaction_type=TransactionType.INSTALLMENTS,
        installment=ParsedInstallmentRef(4, 10),
    )

    mapped = TransactionMapper.to_aggregate(tx, CARD_ID, RUN_ID, InstallmentPlanId(11)).transaction

    assert mapped.installment_plan_id == InstallmentPlanId(11)
    assert mapped.installment_number == 4


def test_to_aggregate_maps_the_foreign_detail_with_persisted_precision():
    tx = parsed(
        billing=Money(Decimal("123.4567891"), ILS),
        original=Money(Decimal("100"), USD),
        fx=ParsedFxDetail(
            commission_amount=Decimal("1.005"),
            stated_fx_rate=Decimal("3.6500005"),
            effective_fx_rate=Decimal("1.234567891"),
        ),
    )

    detail = TransactionMapper.to_aggregate(tx, CARD_ID, RUN_ID).foreign_detail

    assert detail is not None
    assert detail.commission_amount == Decimal("1.01")
    assert detail.stated_fx_rate == Decimal("3.650001")
    assert detail.effective_fx_rate == Decimal("1.234568")


def test_mapping_never_mutates_the_parsed_transaction():
    tx = parsed(issuer_reference="V1", installment=ParsedInstallmentRef(1, 2))
    snapshot = repr(tx)

    TransactionMapper.reference_key(tx, CARD_ID)
    TransactionMapper.comparison_key(tx, CARD_ID)
    TransactionMapper.to_aggregate(tx, CARD_ID, RUN_ID, InstallmentPlanId(2))

    assert repr(tx) == snapshot
    with pytest.raises(AttributeError):
        tx.card_key = CardHintKey(IssuerCode.CAL, "0538")  # type: ignore[misc]


def test_the_mapper_module_does_not_import_sqlalchemy():
    import subprocess
    import sys

    script = (
        "import sys; import bank_transactions.persistence.mappers;"
        "print([m for m in sys.modules if m.split('.')[0] in ('sqlalchemy', 'psycopg')])"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert completed.stdout.strip() == "[]"
