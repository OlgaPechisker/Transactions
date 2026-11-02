"""Spending analysis. Stub: implemented by U9."""

from dataclasses import dataclass
from decimal import Decimal

from bank_transactions.domain.queries import AnalysisQuery
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import (
    AnalysisGroupRecord,
    MatrixCellRecord,
    SeriesPointRecord,
)
from bank_transactions.ports.clock import Clock
from bank_transactions.services.interfaces import AuthContext


@dataclass(frozen=True)
class AnalysisTotalsView:
    count: int
    net: Decimal
    monthly_average: Decimal | None


@dataclass(frozen=True)
class SpendingAnalysisView:
    query: AnalysisQuery
    totals: AnalysisTotalsView
    comparison: AnalysisTotalsView | None
    groups: tuple[AnalysisGroupRecord, ...] | None = None
    time_series: tuple[SeriesPointRecord, ...] | None = None
    matrix: tuple[MatrixCellRecord, ...] | None = None


class AnalysisService:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def analyze(self, context: AuthContext, query: AnalysisQuery) -> SpendingAnalysisView:
        """Raises InvalidQueryError for inconsistent parameters."""
        raise NotImplementedError
