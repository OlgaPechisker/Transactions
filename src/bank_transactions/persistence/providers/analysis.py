"""Spending analysis aggregates: signed billing sums in one billing currency over the caller's
visible transactions.

The filters and the group keys are those of the transaction list (`transaction_query`), so a
group key can be sent back to `GET /transactions` as the matching filter value. Only non-empty
groups and buckets come back; the service fills the gaps."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from bank_transactions.domain.ids import UserId
from bank_transactions.domain.queries import AnalysisQuery, Bucket, Dimension, TransactionFilter
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import AnalysisRepository
from bank_transactions.persistence.providers.transaction_query import _conditions, _group_key
from bank_transactions.persistence.records import (
    AnalysisGroupRecord,
    MatrixCellRecord,
    SeriesPointRecord,
)
from bank_transactions.persistence.visibility import transaction_view_select

__all__ = ["SqlAnalysisRepository"]

KeyExpression = Callable[[Any], ColumnElement[Any]]


def _effective_filter(query: AnalysisQuery) -> TransactionFilter:
    """The query's range and billing currency replace the ones of its filter."""
    return replace(
        query.filter,
        date_from=query.date_from,
        date_to=query.date_to,
        billing_currency=query.billing_currency,
    )


def _bucket_start(bucket: Bucket) -> KeyExpression:
    """The first day of the transaction date's bucket; PostgreSQL weeks start on Monday."""
    return lambda c: cast(func.date_trunc(bucket.value, c.transaction_date), Date)


class SqlAnalysisRepository(AnalysisRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _aggregate(
        self, user_id: UserId, query: AnalysisQuery, keys: Mapping[str, KeyExpression]
    ) -> Sequence[Any]:
        """Rows of (the labelled key columns, rows, net), ordered by key with nulls last. The
        keys are computed in an inner select and grouped by column in the outer one, because
        PostgreSQL rejects a GROUP BY expression whose bind parameters differ from the selected
        one."""
        view = transaction_view_select(user_id).subquery("v")
        filtered = (
            select(
                view.c.billing_amount,
                *(expression(view.c).label(label) for label, expression in keys.items()),
            )
            .where(*_conditions(view, _effective_filter(query)))
            .subquery("filtered")
        )
        columns = [filtered.c[label] for label in keys]
        statement = (
            select(
                *columns,
                func.count().label("rows"),
                func.sum(filtered.c.billing_amount).label("net"),
            )
            .group_by(*columns)
            .order_by(*(column.asc().nulls_last() for column in columns))
        )
        return self._session.execute(statement).all()

    @translates_database_errors
    def groups(
        self, user_id: UserId, query: AnalysisQuery, dimension: Dimension
    ) -> tuple[AnalysisGroupRecord, ...]:
        rows = self._aggregate(user_id, query, {"key": lambda c: _group_key(c, dimension)})
        return tuple(AnalysisGroupRecord(row.key, row.rows, Decimal(row.net)) for row in rows)

    @translates_database_errors
    def series(
        self,
        user_id: UserId,
        query: AnalysisQuery,
        bucket: Bucket,
        series_dimension: Dimension | None,
    ) -> tuple[SeriesPointRecord, ...]:
        keys: dict[str, KeyExpression] = {"bucket_start": _bucket_start(bucket)}
        if series_dimension is not None:
            keys["series_key"] = lambda c: _group_key(c, series_dimension)
        rows = self._aggregate(user_id, query, keys)
        return tuple(
            SeriesPointRecord(
                row.bucket_start,
                row.series_key if series_dimension is not None else None,
                row.rows,
                Decimal(row.net),
            )
            for row in rows
        )

    @translates_database_errors
    def matrix(
        self, user_id: UserId, query: AnalysisQuery, row_dimension: Dimension, bucket: Bucket
    ) -> tuple[MatrixCellRecord, ...]:
        rows = self._aggregate(
            user_id,
            query,
            {
                "row_key": lambda c: _group_key(c, row_dimension),
                "bucket_start": _bucket_start(bucket),
            },
        )
        return tuple(
            MatrixCellRecord(row.row_key, row.bucket_start, row.rows, Decimal(row.net))
            for row in rows
        )
