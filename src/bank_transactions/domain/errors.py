"""Domain errors. Messages are sanitised: they never contain identifiers or file contents."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bank_transactions.domain.models import CardHintKey


class BankTransactionsError(Exception):
    """Base class for every error this package raises on purpose."""


class ParseError(BankTransactionsError):
    """A file-level structural failure: missing sheet, header or required column."""


class UnknownFormatError(BankTransactionsError):
    """No registered parser recognised the workbook."""


class IdentityUnresolvedError(BankTransactionsError):
    """One or more source cards cannot be bound to a database card without user input."""

    def __init__(self, keys: "list[CardHintKey] | tuple[CardHintKey, ...]") -> None:
        self.keys = tuple(keys)
        super().__init__(f"identity input required for {len(self.keys)} card(s)")


class InvalidIdentitySelectionError(BankTransactionsError):
    """A resolver answered with a selection the question did not allow."""


class PersistenceError(BankTransactionsError):
    """A storage failure translated from the database layer."""
