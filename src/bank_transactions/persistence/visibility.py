"""Shared SQL building blocks of every API read. Signatures are frozen by U0; U1 implements
them. Every query is built from `visible_transactions`, so a user can never reach another
user's rows."""

from typing import Any

from sqlalchemy import Row, Select, Subquery
from sqlalchemy.sql.elements import ColumnElement

from bank_transactions.domain.ids import UserId
from bank_transactions.persistence.records import TransactionViewRecord

__all__ = [
    "api_original_currency",
    "effective_category",
    "to_view_record",
    "transaction_view_select",
    "trip_membership",
    "visible_transactions",
    "visible_unknown_currency",
]


def visible_transactions(user_id: UserId) -> Select[Any]:
    """The transactions columns plus `card_id`, restricted to the user's claimed cards."""
    raise NotImplementedError


def effective_category(user_id: UserId) -> Subquery:
    """Columns: transaction_id, bank, custom, effective, source. Override beats the newest
    matching rule, then the mapping, then the bank category."""
    raise NotImplementedError


def trip_membership(user_id: UserId) -> Subquery:
    """Columns: transaction_id, trip_id, trip_public_id, trip_name, membership. One trip per
    transaction; a tag wins over automatic membership; exclusions are removed."""
    raise NotImplementedError


def visible_unknown_currency() -> ColumnElement[bool]:
    """The transaction has an UNKNOWN_CURRENCY review reason that users can see."""
    raise NotImplementedError


def api_original_currency() -> ColumnElement[str | None]:
    """The original currency as the API reports it (validated value, else the source one)."""
    raise NotImplementedError


def transaction_view_select(user_id: UserId) -> Select[Any]:
    """`visible_transactions` joined with category, trip, card and review data: one row per
    `TransactionViewRecord`."""
    raise NotImplementedError


def to_view_record(row: Row[Any]) -> TransactionViewRecord:
    raise NotImplementedError
