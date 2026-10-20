"""A statement imported through the real mapper and repositories, then imported again."""

from datetime import date
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import IssuerCode, TransactionType
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.models import (
    CardHintKey,
    ParsedInstallmentPlan,
    ParsedInstallmentRef,
    ParsedTransaction,
)
from bank_transactions.domain.money import ILS, USD, Money
from bank_transactions.persistence.mappers import TransactionMapper

pytestmark = pytest.mark.postgres

CARD_KEY = CardHintKey(IssuerCode.MAX, "4396")
JULY_3 = date(2026, 7, 3)


def parsed(row: int, **overrides) -> ParsedTransaction:
    values = {
        "card_key": CARD_KEY,
        "location": SourceLocation(sheet_position=1, row=row),
        "transaction_date": JULY_3,
        "charge_date": date(2026, 8, 2),
        "billing": Money(Decimal("120.00"), ILS),
        "merchant_name_raw": "Super Pharm",
        "merchant_name_norm": "super pharm",
        "transaction_type": TransactionType.REGULAR,
    }
    return ParsedTransaction(**{**values, **overrides})


def statement() -> list[ParsedTransaction]:
    installment = {
        "transaction_type": TransactionType.INSTALLMENTS,
        "issuer_reference": "V-1",
        "merchant_name_norm": "fox",
        "merchant_name_raw": "Fox",
        "billing": Money(Decimal("130.00"), ILS),
        "installment": ParsedInstallmentRef(4, 10),
    }
    return [
        parsed(5, issuer_reference="R-1"),
        parsed(6, **installment),
        parsed(7, **{**installment, "installment": ParsedInstallmentRef(5, 10)}),
        parsed(8, original=Money(Decimal("19.9"), USD), billing=Money(Decimal("72.50"), ILS)),
        parsed(9, original=Money(Decimal("19.9"), USD), billing=Money(Decimal("72.50"), ILS)),
    ]


def fox_plan() -> ParsedInstallmentPlan:
    return ParsedInstallmentPlan(
        card_key=CARD_KEY,
        merchant_name_norm="fox",
        purchase_date=JULY_3,
        total=Money(Decimal("1300"), ILS),
        total_installments=10,
        issuer_reference="V-1",
    )


def import_statement(factory, card_id, run_id) -> None:
    with factory.create() as uow:
        plan = TransactionMapper.plan_command(fox_plan(), card_id)
        plan_id = uow.installment_plans.upsert_plans([plan])[plan]
        aggregates = [
            TransactionMapper.to_aggregate(
                tx, card_id, run_id, plan_id if tx.installment is not None else None
            )
            for tx in statement()
        ]
        uow.transactions.insert_many(aggregates)
        uow.commit()


def test_a_statement_is_stored_and_its_installments_share_one_plan(
    factory, seed_card, start_run, sql
):
    card = seed_card()

    import_statement(factory, card.card_id, start_run())

    assert sql("select count(*) from transactions")[0][0] == 5
    assert sql("select count(*) from installment_plans")[0][0] == 1
    assert sql("select count(distinct installment_plan_id) from transactions")[0][0] == 1


def test_a_second_import_finds_every_referenced_row_and_every_reference_less_group(
    factory, seed_card, start_run
):
    card = seed_card()
    import_statement(factory, card.card_id, start_run())

    incoming = statement()
    reference_keys = [TransactionMapper.reference_key(tx, card.card_id) for tx in incoming]
    comparison_keys = [TransactionMapper.comparison_key(tx, card.card_id) for tx in incoming]
    with factory.create() as uow:
        known = uow.transactions.find_known_references([k for k in reference_keys if k])
        counts = uow.transactions.count_matching_groups([k for k in comparison_keys if k])

    assert known == {k for k in reference_keys if k}
    assert len(known) == 3
    assert list(counts.values()) == [2]


def test_reimporting_the_plan_reuses_it_and_adds_no_second_plan(factory, seed_card, start_run, sql):
    card = seed_card()
    import_statement(factory, card.card_id, start_run())

    with factory.create() as uow:
        plan = TransactionMapper.plan_command(fox_plan(), card.card_id)
        uow.installment_plans.upsert_plans([plan])
        uow.commit()

    assert sql("select count(*) from installment_plans")[0][0] == 1
