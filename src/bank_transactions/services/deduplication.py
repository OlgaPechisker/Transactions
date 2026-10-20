"""Which parsed rows are new compared with what is already stored."""

from collections import Counter
from collections.abc import Sequence

from bank_transactions.domain.keys import ComparisonKey, IssuerReferenceKey
from bank_transactions.domain.models import ParsedTransaction
from bank_transactions.identity.models import ResolvedCards
from bank_transactions.persistence.interfaces import TransactionRepository
from bank_transactions.persistence.mappers import TransactionMapper


def select_new_transactions(
    transactions: Sequence[ParsedTransaction],
    cards: ResolvedCards,
    stored: TransactionRepository,
) -> tuple[ParsedTransaction, ...]:
    """Rows to insert, in file order. Both stored-data questions are asked before any decision.

    A row with an issuer reference is new when its key (including the installment number, where
    "none" equals "none") is neither stored nor seen earlier in the file. Reference-less rows
    are grouped across the whole file: a group with N rows against E stored keeps the first E as
    duplicates and inserts the remaining N-E, so identical purchases stay legitimate repeats.
    """
    keyed = [(tx, *_keys(tx, cards)) for tx in transactions]
    known = _known_references(keyed, stored)
    stored_counts = _stored_group_counts(keyed, stored)
    seen_references: set[IssuerReferenceKey] = set()
    seen_in_group: Counter[ComparisonKey] = Counter()
    selected: list[ParsedTransaction] = []
    for tx, reference, group in keyed:
        if reference is not None:
            if reference not in known and reference not in seen_references:
                selected.append(tx)
            seen_references.add(reference)
        elif group is not None:
            if seen_in_group[group] >= stored_counts.get(group, 0):
                selected.append(tx)
            seen_in_group[group] += 1
    return tuple(selected)


def _keys(
    tx: ParsedTransaction, cards: ResolvedCards
) -> tuple[IssuerReferenceKey | None, ComparisonKey | None]:
    card_id = cards.card_id_for(tx.card_key)
    return (
        TransactionMapper.reference_key(tx, card_id),
        TransactionMapper.comparison_key(tx, card_id),
    )


def _known_references(
    keyed: list[tuple[ParsedTransaction, IssuerReferenceKey | None, ComparisonKey | None]],
    stored: TransactionRepository,
) -> frozenset[IssuerReferenceKey]:
    keys = {reference for _, reference, _ in keyed if reference is not None}
    return stored.find_known_references(keys) if keys else frozenset()


def _stored_group_counts(
    keyed: list[tuple[ParsedTransaction, IssuerReferenceKey | None, ComparisonKey | None]],
    stored: TransactionRepository,
) -> dict[ComparisonKey, int]:
    keys = {group for _, _, group in keyed if group is not None}
    return dict(stored.count_matching_groups(keys)) if keys else {}
