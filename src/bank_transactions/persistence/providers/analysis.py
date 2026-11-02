"""Spending analysis aggregates. Stub: implemented by U9."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import UserId
from bank_transactions.domain.queries import AnalysisQuery, Bucket, Dimension
from bank_transactions.persistence.interfaces import AnalysisRepository
from bank_transactions.persistence.records import (
    AnalysisGroupRecord,
    MatrixCellRecord,
    SeriesPointRecord,
)


class SqlAnalysisRepository(AnalysisRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def groups(
        self, user_id: UserId, query: AnalysisQuery, dimension: Dimension
    ) -> tuple[AnalysisGroupRecord, ...]:
        raise NotImplementedError

    def series(
        self,
        user_id: UserId,
        query: AnalysisQuery,
        bucket: Bucket,
        series_dimension: Dimension | None,
    ) -> tuple[SeriesPointRecord, ...]:
        raise NotImplementedError

    def matrix(
        self, user_id: UserId, query: AnalysisQuery, row_dimension: Dimension, bucket: Bucket
    ) -> tuple[MatrixCellRecord, ...]:
        raise NotImplementedError
