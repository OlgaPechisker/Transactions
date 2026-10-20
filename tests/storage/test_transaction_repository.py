from datetime import date
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import Channel, ReviewReason, TransactionType
from bank_transactions.domain.errors import PersistenceError
from bank_transactions.domain.ids import CardId
from bank_transactions.domain.keys import ComparisonKey, IssuerReferenceKey
from bank_transactions.domain.money import EUR, ILS, USD
from bank_transactions.persistence.commands import NewForeignDetail, NewInstallmentPlan

pytestmark = pytest.mark.postgres

JULY_3 = date(2026, 7, 3)


@pytest.fixture
def card_and_run(seed_card, start_run):
    return seed_card().card_id, start_run()


def reference_key(card_id, reference="R1", installment=None, day=JULY_3) -> IssuerReferenceKey:
    return IssuerReferenceKey(card_id, day, reference, installment)


def comparison_key(card_id, **overrides) -> ComparisonKey:
    values = {
        "card_id": card_id,
        "transaction_date": JULY_3,
        "merchant_name_norm": "super pharm",
        "original_amount": None,
        "original_currency": None,
        "installment_number": None,
    }
    return ComparisonKey(**{**values, **overrides})


def test_ids_come_back_in_input_order_with_every_row_stored(
    factory, card_and_run, new_aggregate, sql
):
    card_id, run_id = card_and_run
    aggregates = [
        new_aggregate(card_id, run_id, merchant_name_raw=name, merchant_name_norm=name)
        for name in ("c-shop", "a-shop", "b-shop")
    ]

    with factory.create() as uow:
        ids = uow.transactions.insert_many(aggregates)
        uow.commit()

    stored = dict(sql("select id, merchant_name_raw from transactions"))
    assert [stored[i] for i in ids] == ["c-shop", "a-shop", "b-shop"]
    assert list(ids) == sorted(ids)


def test_an_empty_batch_inserts_nothing(factory):
    with factory.create() as uow:
        assert uow.transactions.insert_many([]) == ()


def test_every_transaction_field_round_trips_as_given(factory, card_and_run, new_aggregate, sql):
    card_id, run_id = card_and_run
    aggregate = new_aggregate(
        card_id,
        run_id,
        issuer_reference="R-77",
        billing_amount=Decimal("-45.50"),
        billing_currency=ILS,
        original_amount=Decimal("12.34"),
        original_currency=USD,
        merchant_city="Tel Aviv",
        merchant_country="IL",
        bank_category_raw="Pharmacy",
        transaction_type=TransactionType.IMMEDIATE_CHARGE,
        notes="note",
    )

    with factory.create() as uow:
        uow.transactions.insert_many([aggregate])
        uow.commit()

    row = sql(
        "select issuer_reference, transaction_date, charge_date, billing_amount, billing_currency,"
        " original_amount, original_currency, merchant_name_raw, merchant_name_norm,"
        " merchant_city, merchant_country, bank_category_raw, transaction_type, channel, notes,"
        " installment_plan_id, installment_number, import_run_id, card_id from transactions"
    )[0]
    assert tuple(row) == (
        "R-77",
        JULY_3,
        date(2026, 8, 2),
        Decimal("-45.50"),
        "ILS",
        Decimal("12.34"),
        "USD",
        "Super  Pharm",
        "super pharm",
        "Tel Aviv",
        "IL",
        "Pharmacy",
        "IMMEDIATE_CHARGE",
        None,
        "note",
        None,
        None,
        run_id,
        card_id,
    )


def test_review_reasons_and_foreign_detail_are_stored_with_their_transaction(
    factory, card_and_run, new_aggregate, sql
):
    card_id, run_id = card_and_run
    aggregate = new_aggregate(
        card_id,
        run_id,
        original_amount=Decimal("100"),
        original_currency=USD,
        aggregate_review_reasons=frozenset(
            {ReviewReason.UNKNOWN_CURRENCY, ReviewReason.AMBIGUOUS_INSTALLMENT}
        ),
        aggregate_foreign_detail=NewForeignDetail(
            Decimal("2.5"), Decimal("3.6500009"), Decimal("1.23456749")
        ),
    )

    with factory.create() as uow:
        (transaction_id,) = uow.transactions.insert_many([aggregate])
        uow.commit()

    reasons = sql(
        "select reason from transaction_review_reasons where transaction_id = :t order by reason",
        t=transaction_id,
    )
    detail = sql(
        "select commission_amount, stated_fx_rate, effective_fx_rate "
        "from foreign_transaction_details where transaction_id = :t",
        t=transaction_id,
    )
    assert sorted(row[0] for row in reasons) == ["AMBIGUOUS_INSTALLMENT", "UNKNOWN_CURRENCY"]
    assert [tuple(row) for row in detail] == [
        (Decimal("2.50"), Decimal("3.650001"), Decimal("1.234567"))
    ]


def test_a_foreign_detail_without_a_stated_rate_stores_null(
    factory, card_and_run, new_aggregate, sql
):
    card_id, run_id = card_and_run
    aggregate = new_aggregate(
        card_id,
        run_id,
        aggregate_foreign_detail=NewForeignDetail(Decimal("0"), None, Decimal("3.7")),
    )

    with factory.create() as uow:
        uow.transactions.insert_many([aggregate])
        uow.commit()

    assert tuple(
        sql("select stated_fx_rate, effective_fx_rate from foreign_transaction_details")[0]
    ) == (None, Decimal("3.700000"))


def test_a_transaction_without_reasons_or_foreign_detail_has_no_child_rows(
    factory, card_and_run, new_aggregate, sql
):
    card_id, run_id = card_and_run

    with factory.create() as uow:
        uow.transactions.insert_many([new_aggregate(card_id, run_id)])
        uow.commit()

    assert sql("select count(*) from transaction_review_reasons")[0][0] == 0
    assert sql("select count(*) from foreign_transaction_details")[0][0] == 0


def test_a_failing_batch_leaves_no_partial_aggregate_after_rollback(
    factory, card_and_run, new_aggregate, sql
):
    card_id, run_id = card_and_run
    good = new_aggregate(
        card_id,
        run_id,
        aggregate_review_reasons=frozenset({ReviewReason.UNKNOWN_CURRENCY}),
    )
    overflow = new_aggregate(card_id, run_id, billing_amount=Decimal("1000000000000.00"))

    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.transactions.insert_many([good, overflow])

    assert sql("select count(*) from transactions")[0][0] == 0
    assert sql("select count(*) from transaction_review_reasons")[0][0] == 0


def test_a_numeric_overflow_is_a_neutral_persistence_error(factory, card_and_run, new_aggregate):
    card_id, run_id = card_and_run
    overflow = new_aggregate(
        card_id,
        run_id,
        merchant_name_raw="SECRET-MERCHANT",
        billing_amount=Decimal("9999999999999.99"),
    )

    with factory.create() as uow, pytest.raises(PersistenceError) as raised:
        uow.transactions.insert_many([overflow])

    assert "SECRET-MERCHANT" not in str(raised.value)
    assert "9999999999999" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_the_largest_value_that_fits_numeric_14_2_is_stored_exactly(
    factory, card_and_run, new_aggregate, sql
):
    card_id, run_id = card_and_run
    largest = Decimal("999999999999.99")

    with factory.create() as uow:
        uow.transactions.insert_many([new_aggregate(card_id, run_id, billing_amount=largest)])
        uow.commit()

    assert sql("select billing_amount from transactions")[0][0] == largest


def test_a_transaction_for_an_unknown_card_is_rejected(factory, start_run, new_aggregate):
    run_id = start_run()

    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.transactions.insert_many([new_aggregate(CardId(424242), run_id)])


def test_uncommitted_transactions_do_not_survive_the_unit_of_work(
    factory, card_and_run, new_aggregate, sql
):
    card_id, run_id = card_and_run

    with factory.create() as uow:
        uow.transactions.insert_many([new_aggregate(card_id, run_id)])

    assert sql("select count(*) from transactions")[0][0] == 0


def test_a_known_non_installment_reference_is_found_and_an_unseen_one_is_not(
    factory, card_and_run, new_aggregate
):
    card_id, run_id = card_and_run
    with factory.create() as uow:
        uow.transactions.insert_many([new_aggregate(card_id, run_id, issuer_reference="R1")])
        uow.commit()

    asked = [reference_key(card_id, "R1"), reference_key(card_id, "R2")]
    with factory.create() as uow:
        assert uow.transactions.find_known_references(asked) == {reference_key(card_id, "R1")}


def test_reference_lookup_separates_installment_numbers_and_null_safely(
    factory, card_and_run, new_aggregate
):
    card_id, run_id = card_and_run
    with factory.create() as uow:
        uow.transactions.insert_many(
            [
                new_aggregate(card_id, run_id, issuer_reference="V1", installment_number=2),
                new_aggregate(card_id, run_id, issuer_reference="V2", installment_number=None),
            ]
        )
        uow.commit()

    asked = [
        reference_key(card_id, "V1", 2),
        reference_key(card_id, "V1", 3),
        reference_key(card_id, "V1", None),
        reference_key(card_id, "V2", None),
        reference_key(card_id, "V2", 1),
    ]
    with factory.create() as uow:
        assert uow.transactions.find_known_references(asked) == {
            reference_key(card_id, "V1", 2),
            reference_key(card_id, "V2", None),
        }


def test_reference_lookup_includes_card_and_transaction_date(
    factory, seed_card, start_run, new_aggregate
):
    first = seed_card(last4="1111", owner_name="Dana Levi", account_number="111")
    second = seed_card(last4="2222", owner_name="Avi Cohen", account_number="222")
    run_id = start_run()
    with factory.create() as uow:
        uow.transactions.insert_many([new_aggregate(first.card_id, run_id, issuer_reference="R1")])
        uow.commit()

    asked = [
        reference_key(second.card_id),
        reference_key(first.card_id, day=date(2026, 7, 4)),
        reference_key(first.card_id),
    ]
    with factory.create() as uow:
        assert uow.transactions.find_known_references(asked) == {reference_key(first.card_id)}


def test_known_reference_lookup_handles_more_keys_than_a_single_statement_chunk(
    factory, card_and_run, new_aggregate
):
    card_id, run_id = card_and_run
    with factory.create() as uow:
        uow.transactions.insert_many([new_aggregate(card_id, run_id, issuer_reference="R-7")])
        uow.commit()

    asked = [reference_key(card_id, f"R-{n}", n % 2 or None) for n in range(5000)]
    with factory.create() as uow:
        found = uow.transactions.find_known_references(asked)

    assert found == set()
    with factory.create() as uow:
        asked.append(reference_key(card_id, "R-7"))
        assert uow.transactions.find_known_references(asked) == {reference_key(card_id, "R-7")}


def test_an_empty_key_collection_finds_nothing(factory):
    with factory.create() as uow:
        assert uow.transactions.find_known_references([]) == frozenset()
        assert dict(uow.transactions.count_matching_groups([])) == {}


@pytest.mark.parametrize("transaction_type", list(TransactionType))
@pytest.mark.parametrize("channel", [*Channel, None])
def test_every_transaction_type_and_channel_value_is_storable(
    factory, card_and_run, new_aggregate, sql, transaction_type, channel
):
    card_id, run_id = card_and_run

    with factory.create() as uow:
        uow.transactions.insert_many(
            [new_aggregate(card_id, run_id, transaction_type=transaction_type, channel=channel)]
        )
        uow.commit()

    stored = tuple(sql("select transaction_type::text, channel::text from transactions")[0])
    assert stored == (transaction_type.value, None if channel is None else channel.value)


@pytest.mark.parametrize("reason", list(ReviewReason))
def test_every_review_reason_value_is_storable(factory, card_and_run, new_aggregate, sql, reason):
    card_id, run_id = card_and_run

    with factory.create() as uow:
        uow.transactions.insert_many(
            [new_aggregate(card_id, run_id, aggregate_review_reasons=frozenset({reason}))]
        )
        uow.commit()

    assert sql("select reason::text from transaction_review_reasons")[0][0] == reason.value


def test_a_transaction_links_to_its_installment_plan(factory, card_and_run, new_aggregate, sql):
    card_id, run_id = card_and_run
    command = NewInstallmentPlan(card_id, "V-1", "fox", JULY_3, Decimal("1300"), ILS, 10)

    with factory.create() as uow:
        plan_id = uow.installment_plans.upsert_plans([command])[command]
        uow.transactions.insert_many(
            [
                new_aggregate(
                    card_id,
                    run_id,
                    issuer_reference="V-1",
                    installment_plan_id=plan_id,
                    installment_number=4,
                    transaction_type=TransactionType.INSTALLMENTS,
                )
            ]
        )
        uow.commit()

    row = sql("select installment_plan_id, installment_number from transactions")[0]
    assert tuple(row) == (plan_id, 4)


def test_the_same_reference_twice_is_rejected_even_when_installment_number_is_null(
    factory, card_and_run, new_aggregate
):
    card_id, run_id = card_and_run
    with factory.create() as uow:
        uow.transactions.insert_many([new_aggregate(card_id, run_id, issuer_reference="R1")])
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.transactions.insert_many([new_aggregate(card_id, run_id, issuer_reference="R1")])


def test_installment_charges_sharing_a_reference_are_distinct_by_installment_number(
    factory, card_and_run, new_aggregate, sql
):
    card_id, run_id = card_and_run
    charges = [
        new_aggregate(card_id, run_id, issuer_reference="V1", installment_number=n)
        for n in (1, 2, 3)
    ]

    with factory.create() as uow:
        uow.transactions.insert_many(charges)
        uow.commit()

    assert sql("select count(*) from transactions")[0][0] == 3


def test_the_same_reference_and_installment_number_is_rejected(
    factory, card_and_run, new_aggregate
):
    card_id, run_id = card_and_run
    duplicate = new_aggregate(card_id, run_id, issuer_reference="V1", installment_number=2)

    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.transactions.insert_many([duplicate, duplicate])


def test_a_regular_reference_and_an_installment_charge_with_that_reference_coexist(
    factory, card_and_run, new_aggregate, sql
):
    card_id, run_id = card_and_run

    with factory.create() as uow:
        uow.transactions.insert_many(
            [
                new_aggregate(card_id, run_id, issuer_reference="V1"),
                new_aggregate(card_id, run_id, issuer_reference="V1", installment_number=1),
            ]
        )
        uow.commit()

    assert sql("select count(*) from transactions")[0][0] == 2


def test_reference_less_rows_may_repeat_because_they_are_counted_not_deduplicated(
    factory, card_and_run, new_aggregate, sql
):
    card_id, run_id = card_and_run

    with factory.create() as uow:
        uow.transactions.insert_many([new_aggregate(card_id, run_id) for _ in range(3)])
        uow.commit()

    assert sql("select count(*) from transactions")[0][0] == 3


def test_group_counts_cover_only_reference_less_rows_and_report_zero_for_missing_groups(
    factory, card_and_run, new_aggregate
):
    card_id, run_id = card_and_run
    with factory.create() as uow:
        uow.transactions.insert_many(
            [
                new_aggregate(card_id, run_id),
                new_aggregate(card_id, run_id),
                new_aggregate(card_id, run_id, issuer_reference="R1"),
            ]
        )
        uow.commit()

    present = comparison_key(card_id)
    absent = comparison_key(card_id, merchant_name_norm="elsewhere")
    with factory.create() as uow:
        counts = uow.transactions.count_matching_groups([present, absent])

    assert counts[present] == 2
    assert counts[absent] == 0


def test_group_counts_are_null_safe_on_original_amount_currency_and_installment(
    factory, card_and_run, new_aggregate
):
    card_id, run_id = card_and_run
    domestic = {}
    foreign = {"original_amount": Decimal("10.00"), "original_currency": USD}
    installment = {"installment_number": 2}
    with factory.create() as uow:
        uow.transactions.insert_many(
            [
                new_aggregate(card_id, run_id, **domestic),
                new_aggregate(card_id, run_id, **foreign),
                new_aggregate(card_id, run_id, **foreign),
                new_aggregate(card_id, run_id, **installment),
                new_aggregate(card_id, run_id, **installment),
                new_aggregate(card_id, run_id, **installment),
            ]
        )
        uow.commit()

    keys = {
        "domestic": comparison_key(card_id),
        "foreign": comparison_key(card_id, original_amount=Decimal("10"), original_currency=USD),
        "other_currency": comparison_key(
            card_id, original_amount=Decimal("10"), original_currency=EUR
        ),
        "other_amount": comparison_key(
            card_id, original_amount=Decimal("10.01"), original_currency=USD
        ),
        "installment": comparison_key(card_id, installment_number=2),
        "other_installment": comparison_key(card_id, installment_number=3),
    }
    with factory.create() as uow:
        counts = uow.transactions.count_matching_groups(list(keys.values()))

    assert {name: counts[key] for name, key in keys.items()} == {
        "domestic": 1,
        "foreign": 2,
        "other_currency": 0,
        "other_amount": 0,
        "installment": 3,
        "other_installment": 0,
    }


def test_group_counts_separate_cards_dates_and_merchants(
    factory, seed_card, start_run, new_aggregate
):
    first = seed_card(last4="1111", owner_name="Dana Levi", account_number="111")
    second = seed_card(last4="2222", owner_name="Avi Cohen", account_number="222")
    run_id = start_run()
    with factory.create() as uow:
        uow.transactions.insert_many([new_aggregate(first.card_id, run_id)])
        uow.commit()

    with factory.create() as uow:
        keys = [
            comparison_key(first.card_id),
            comparison_key(second.card_id),
            comparison_key(first.card_id, transaction_date=date(2026, 7, 4)),
        ]
        counts = uow.transactions.count_matching_groups(keys)

    assert [counts[key] for key in keys] == [1, 0, 0]


def test_group_counts_compare_stored_amounts_at_persisted_precision(
    factory, card_and_run, new_aggregate
):
    card_id, run_id = card_and_run
    with factory.create() as uow:
        uow.transactions.insert_many(
            [
                new_aggregate(
                    card_id, run_id, original_amount=Decimal("10.005"), original_currency=USD
                )
            ]
        )
        uow.commit()

    key = comparison_key(card_id, original_amount=Decimal("10.0050"), original_currency=USD)
    with factory.create() as uow:
        assert uow.transactions.count_matching_groups([key])[key] == 1


def test_group_counts_see_rows_flushed_earlier_in_the_same_unit_of_work(
    factory, card_and_run, new_aggregate
):
    card_id, run_id = card_and_run
    key = comparison_key(card_id)

    with factory.create() as uow:
        uow.transactions.insert_many([new_aggregate(card_id, run_id)])

        assert uow.transactions.count_matching_groups([key])[key] == 1
