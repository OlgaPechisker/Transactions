"""Which review reasons an API user can see. Implemented by U5 (import API slice)."""

from bank_transactions.domain.models import ParsedTransaction

__all__ = ["is_user_visible_unknown_currency"]


def is_user_visible_unknown_currency(transaction: ParsedTransaction) -> bool:
    """True when the transaction needs the user to confirm a currency the file did not state."""
    raise NotImplementedError
