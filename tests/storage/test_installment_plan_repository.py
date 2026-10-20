from datetime import date
from decimal import Decimal

import pytest

from bank_transactions.domain.errors import PersistenceError
from bank_transactions.domain.money import ILS, USD
from bank_transactions.persistence.commands import NewInstallmentPlan

pytestmark = pytest.mark.postgres


def plan(card_id, reference="V-100", **overrides) -> NewInstallmentPlan:
    values = {
        "card_id": card_id,
        "issuer_reference": reference,
        "merchant_name_norm": "fox home",
        "purchase_date": date(2026, 6, 14),
        "total_amount": Decimal("1300.00"),
        "currency": ILS,
        "total_installments": 10,
    }
    return NewInstallmentPlan(**{**values, **overrides})


def stored_plans(sql):
    return sql(
        "select issuer_reference, merchant_name_norm, total_amount, currency, total_installments "
        "from installment_plans order by id"
    )


def test_a_new_plan_gets_an_id_and_is_stored_as_given(factory, seed_card, sql):
    card = seed_card()

    with factory.create() as uow:
        command = plan(card.card_id)
        ids = uow.installment_plans.upsert_plans([command])
        uow.commit()

    assert ids[command] > 0
    assert stored_plans(sql) == [("V-100", "fox home", Decimal("1300.00"), "ILS", 10)]


def test_equal_commands_share_one_plan_and_distinct_commands_get_distinct_ids(
    factory, seed_card, sql
):
    card = seed_card()
    first = plan(card.card_id)
    twin = plan(card.card_id)
    other = plan(card.card_id, reference="V-200", merchant_name_norm="zara")

    with factory.create() as uow:
        ids = uow.installment_plans.upsert_plans([first, twin, other])
        uow.commit()

    assert ids[first] == ids[twin]
    assert ids[first] != ids[other]
    assert len(stored_plans(sql)) == 2


def test_upserting_an_existing_plan_returns_its_id_without_creating_another(
    factory, seed_card, sql
):
    card = seed_card()
    with factory.create() as uow:
        original = uow.installment_plans.upsert_plans([plan(card.card_id)])[plan(card.card_id)]
        uow.commit()

    with factory.create() as uow:
        again = uow.installment_plans.upsert_plans([plan(card.card_id)])[plan(card.card_id)]
        uow.commit()

    assert again == original
    assert len(stored_plans(sql)) == 1


def test_a_reference_less_plan_matches_the_existing_natural_plan(factory, seed_card, sql):
    card = seed_card()
    with factory.create() as uow:
        existing = uow.installment_plans.upsert_plans([plan(card.card_id)])[plan(card.card_id)]
        uow.commit()

    with factory.create() as uow:
        without_reference = plan(card.card_id, reference=None)
        resolved = uow.installment_plans.upsert_plans([without_reference])[without_reference]
        uow.commit()

    assert resolved == existing
    assert stored_plans(sql)[0][0] == "V-100"


def test_a_plan_with_a_reference_reuses_a_natural_plan_stored_without_one_and_never_updates_it(
    factory, seed_card, sql
):
    card = seed_card()
    with factory.create() as uow:
        bare = plan(card.card_id, reference=None)
        existing = uow.installment_plans.upsert_plans([bare])[bare]
        uow.commit()

    with factory.create() as uow:
        referenced = plan(card.card_id)
        resolved = uow.installment_plans.upsert_plans([referenced])[referenced]
        uow.commit()

    assert resolved == existing
    assert stored_plans(sql)[0][0] is None


def test_the_same_reference_with_a_different_natural_key_is_an_inconsistent_match(
    factory, seed_card, sql
):
    card = seed_card()
    with factory.create() as uow:
        uow.installment_plans.upsert_plans([plan(card.card_id)])
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError, match="inconsistent"):
        uow.installment_plans.upsert_plans([plan(card.card_id, total_amount=Decimal("1400"))])

    assert len(stored_plans(sql)) == 1


def test_the_same_natural_key_with_a_different_reference_is_an_inconsistent_match(
    factory, seed_card, sql
):
    card = seed_card()
    with factory.create() as uow:
        uow.installment_plans.upsert_plans([plan(card.card_id)])
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError, match="inconsistent"):
        uow.installment_plans.upsert_plans([plan(card.card_id, reference="V-999")])


def test_a_reference_and_a_natural_key_pointing_at_two_different_plans_are_rejected(
    factory, seed_card
):
    card = seed_card()
    with factory.create() as uow:
        uow.installment_plans.upsert_plans(
            [plan(card.card_id), plan(card.card_id, reference=None, total_amount=Decimal("500"))]
        )
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError, match="inconsistent"):
        uow.installment_plans.upsert_plans([plan(card.card_id, total_amount=Decimal("500"))])


def test_the_natural_key_stays_unique_even_when_references_differ_in_the_database(
    factory, seed_card, sql
):
    card = seed_card()
    with factory.create() as uow:
        uow.installment_plans.upsert_plans([plan(card.card_id)])
        uow.commit()

    with pytest.raises(Exception, match="uq_installment_plans_natural"):
        sql(
            "insert into installment_plans (card_id, issuer_reference, merchant_name_norm, "
            "purchase_date, total_amount, currency, total_installments) "
            "values (:card, 'V-OTHER', 'fox home', '2026-06-14', 1300.00, 'ILS', 10)",
            card=card.card_id,
        )


def test_plans_of_different_cards_and_currencies_do_not_collide(factory, seed_card, sql):
    first = seed_card(last4="1111", owner_name="Dana Levi", account_number="111")
    second = seed_card(last4="2222", owner_name="Avi Cohen", account_number="222")
    commands = [
        plan(first.card_id),
        plan(second.card_id),
        plan(first.card_id, reference="V-USD", currency=USD),
    ]

    with factory.create() as uow:
        ids = uow.installment_plans.upsert_plans(commands)
        uow.commit()

    assert len(set(ids.values())) == 3
    assert len(stored_plans(sql)) == 3


def test_an_empty_request_returns_an_empty_mapping(factory):
    with factory.create() as uow:
        assert dict(uow.installment_plans.upsert_plans([])) == {}


def test_plans_written_without_a_commit_are_not_persisted(factory, seed_card, sql):
    card = seed_card()

    with factory.create() as uow:
        uow.installment_plans.upsert_plans([plan(card.card_id)])

    assert stored_plans(sql) == []
