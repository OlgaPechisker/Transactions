"""Which review reasons an API user can see. Implemented by U5 (import API slice)."""

from bank_transactions.domain.enums import ReviewReason
from bank_transactions.domain.models import ParsedTransaction

__all__ = ["is_user_visible_unknown_currency"]


def is_user_visible_unknown_currency(transaction: ParsedTransaction) -> bool:
    """True when the transaction needs the user to confirm a currency the file did not state.

    Only a foreign row (it has FX details) whose original currency is unknown qualifies; a
    domestic row carrying UNKNOWN_CURRENCY and the internal reasons (UNMATCHED_FOREIGN_FEE,
    AMBIGUOUS_INSTALLMENT) are never user-visible. Same rule as the SQL
    `visibility.visible_unknown_currency`."""
    original = transaction.original
    return (
        ReviewReason.UNKNOWN_CURRENCY in transaction.review_reasons
        and transaction.fx is not None
        and original is not None
        and original.currency is None
    )
