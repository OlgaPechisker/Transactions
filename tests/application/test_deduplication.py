"""Duplicate handling against stored data: referenced rows are unique by key, reference-less rows
by multiplicity (an identical repeat inside one statement is a legitimate second purchase)."""

from collections.abc import Collection, Mapping, Sequence
from datetime import date
from decimal import Decimal

from bank_transactions.domain.ids import CardId, TransactionId
from bank_transactions.domain.keys import ComparisonKey, IssuerReferenceKey
from bank_transactions.domain.models import ParsedInstallmentRef
from bank_transactions.domain.money import ILS
from bank_transactions.identity.models import ResolvedCards
from bank_transactions.persistence.commands import NewTransactionAggregate
from bank_transactions.persistence.interfaces import TransactionRepository
from bank_transactions.services.deduplication import select_new_transactions

CARD_ID = CardId(7)


class StoredRows(TransactionRepository):
    def __init__(self, references=(), group_counts=None):
        self.references = frozenset(references)
        self.group_counts = dict(group_counts or {})
        self.reference_queries: list[frozenset[IssuerReferenceKey]] = []
        self.group_queries: list[frozenset[ComparisonKey]] = []

    def find_known_references(self, keys: Collection[IssuerReferenceKey]):
        self.reference_queries.append(frozenset(keys))
        return self.references & frozenset(keys)

    def count_matching_groups(self, keys: Collection[ComparisonKey]) -> Mapping[ComparisonKey, int]:
        self.group_queries.append(frozenset(keys))
        return {key: self.group_counts[key] for key in keys if key in self.group_counts}

    def insert_many(
        self, aggregates: Sequence[NewTransactionAggregate]
    ) -> tuple[TransactionId, ...]:
        raise AssertionError("deduplication never writes")


def comparison_key(**overrides):
    values = {
        "card_id": CARD_ID,
        "transaction_date": date(2026, 7, 3),
        "merchant_name_norm": "super pharm",
        "original_amount": Decimal("100.00"),
        "original_currency": ILS,
        "installment_number": None,
    }
    return ComparisonKey(**{**values, **overrides})


def select(transactions, stored, card_key):
    return select_new_transactions(transactions, ResolvedCards({card_key: CARD_ID}), stored)


def test_identical_reference_less_rows_are_all_new_when_nothing_is_stored(make_tx, card_key):
    rows = [make_tx() for _ in range(3)]

    assert select(rows, StoredRows(), card_key) == tuple(rows)


def test_three_identical_rows_against_three_stored_add_nothing(make_tx, card_key):
    rows = [make_tx() for _ in range(3)]
    stored = StoredRows(group_counts={comparison_key(): 3})

    assert select(rows, stored, card_key) == ()


def test_four_identical_rows_against_three_stored_add_exactly_one(make_tx, card_key):
    rows = [make_tx() for _ in range(4)]
    stored = StoredRows(group_counts={comparison_key(): 3})

    assert select(rows, stored, card_key) == (rows[3],)


def test_two_identical_rows_against_three_stored_add_nothing(make_tx, card_key):
    rows = [make_tx() for _ in range(2)]
    stored = StoredRows(group_counts={comparison_key(): 3})

    assert select(rows, stored, card_key) == ()


def test_rows_differing_in_any_key_part_are_separate_groups(make_tx, card_key):
    same = make_tx()
    other_day = make_tx(transaction_date=date(2026, 7, 4))
    other_merchant = make_tx(merchant_name_norm="mega sport")
    stored = StoredRows(group_counts={comparison_key(): 1})

    assert select([same, other_day, other_merchant], stored, card_key) == (
        other_day,
        other_merchant,
    )


def test_original_currency_and_amount_without_a_value_form_their_own_group(make_tx, card_key):
    unknown = make_tx(original=None)
    stored = StoredRows(
        group_counts={comparison_key(original_amount=None, original_currency=None): 1}
    )

    assert select([unknown, unknown], stored, card_key) == (unknown,)


def test_the_installment_number_is_part_of_the_reference_less_group(make_tx, card_key):
    first = make_tx(installment=ParsedInstallmentRef(1, 3))
    second = make_tx(installment=ParsedInstallmentRef(2, 3))
    stored = StoredRows(group_counts={comparison_key(installment_number=1): 1})

    assert select([first, second], stored, card_key) == (second,)


def test_surplus_rows_are_the_last_ones_in_file_order(make_tx, card_key):
    rows = [make_tx() for _ in range(3)]
    stored = StoredRows(group_counts={comparison_key(): 1})

    assert select(rows, stored, card_key) == (rows[1], rows[2])


def test_a_stored_reference_makes_the_row_a_duplicate(make_tx, card_key):
    known = make_tx(issuer_reference="V-100")
    fresh = make_tx(issuer_reference="V-200")
    stored = StoredRows(references=[IssuerReferenceKey(CARD_ID, date(2026, 7, 3), "V-100", None)])

    assert select([known, fresh], stored, card_key) == (fresh,)


def test_a_reference_repeated_inside_the_file_keeps_only_the_first_row(make_tx, card_key):
    first = make_tx(issuer_reference="V-100")
    copy = make_tx(issuer_reference="V-100")

    assert select([first, copy], StoredRows(), card_key) == (first,)


def test_the_installment_number_distinguishes_rows_sharing_a_reference(make_tx, card_key):
    first = make_tx(issuer_reference="V-100", installment=ParsedInstallmentRef(1, 3))
    second = make_tx(issuer_reference="V-100", installment=ParsedInstallmentRef(2, 3))
    stored = StoredRows(references=[IssuerReferenceKey(CARD_ID, date(2026, 7, 3), "V-100", 1)])

    assert select([first, second], stored, card_key) == (second,)


def test_a_missing_installment_number_equals_another_missing_one_only(make_tx, card_key):
    plain = make_tx(issuer_reference="V-100")
    installment = make_tx(issuer_reference="V-100", installment=ParsedInstallmentRef(1, 3))
    stored = StoredRows(references=[IssuerReferenceKey(CARD_ID, date(2026, 7, 3), "V-100", None)])

    assert select([plain, installment], stored, card_key) == (installment,)


def test_referenced_and_reference_less_rows_keep_file_order(make_tx, card_key):
    plain = make_tx()
    referenced = make_tx(issuer_reference="V-100")
    later_plain = make_tx(merchant_name_norm="mega sport")

    assert select([plain, referenced, later_plain], StoredRows(), card_key) == (
        plain,
        referenced,
        later_plain,
    )


def test_the_store_is_asked_once_per_kind_before_anything_is_decided(make_tx, card_key):
    rows = [make_tx(), make_tx(), make_tx(issuer_reference="V-1"), make_tx(issuer_reference="V-2")]
    stored = StoredRows()

    select(rows, stored, card_key)

    assert len(stored.reference_queries) == 1
    assert len(stored.group_queries) == 1
    assert len(stored.reference_queries[0]) == 2


def test_nothing_is_asked_of_the_store_when_there_are_no_rows(card_key):
    stored = StoredRows()

    assert select([], stored, card_key) == ()
    assert stored.reference_queries == [] and stored.group_queries == []
