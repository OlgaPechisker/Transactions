"""Spending analysis: totals, groups, time series and matrix over signed billing amounts in one
billing currency. The repository returns only non-empty groups and buckets; this service fills the
empty buckets with zero (calendar rules live in `domain/calendar.py`)."""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date
from decimal import Decimal

from bank_transactions.domain.calendar import (
    DateRange,
    MonthlyAverage,
    Period,
    bucket_key,
    count_buckets,
    monthly_average,
    periods,
    previous_range,
    year_over_year_range,
)
from bank_transactions.domain.errors import InvalidQueryError
from bank_transactions.domain.ids import UserId
from bank_transactions.domain.queries import (
    AnalysisInclude,
    AnalysisQuery,
    Bucket,
    CompareMode,
    Dimension,
)
from bank_transactions.persistence.interfaces import (
    AnalysisRepository,
    Isolation,
    UnitOfWorkFactory,
)
from bank_transactions.persistence.records import (
    AnalysisGroupRecord,
    MatrixCellRecord,
    SeriesPointRecord,
    TripRecord,
    VisibleCardRecord,
)
from bank_transactions.ports.clock import Clock
from bank_transactions.services.interfaces import AuthContext

__all__ = [
    "MAX_BUCKETS",
    "AnalysisGroupView",
    "AnalysisService",
    "AnalysisTotalsView",
    "MatrixRowView",
    "MatrixView",
    "PreviousGroupView",
    "SpendingAnalysisView",
    "TimePointView",
    "TimeSeriesView",
]

MAX_BUCKETS = 1000
_PERIODS = frozenset({Dimension.DAY, Dimension.WEEK, Dimension.MONTH, Dimension.YEAR})
_DEFAULT_ROW_DIMENSION = Dimension.CATEGORY


@dataclass(frozen=True)
class AnalysisTotalsView:
    net: Decimal
    count: int
    monthly_average: MonthlyAverage


@dataclass(frozen=True)
class PreviousGroupView:
    net: Decimal
    count: int


@dataclass(frozen=True)
class AnalysisGroupView:
    """`key` is None for the rows without a value in the dimension. `previous` is set whenever
    the query compares, zero for a group that has no rows in the comparison range."""

    key: str | None
    net: Decimal
    count: int
    previous: PreviousGroupView | None


@dataclass(frozen=True)
class TimePointView:
    period: Period
    net: Decimal


@dataclass(frozen=True)
class TimeSeriesView:
    """One point per bucket of the range. `average` is the monthly average for month buckets
    and None for every other bucket."""

    points: tuple[TimePointView, ...]
    average: Decimal | None


@dataclass(frozen=True)
class MatrixRowView:
    key: str | None
    cells: tuple[Decimal, ...]
    total: Decimal


@dataclass(frozen=True)
class MatrixView:
    """`cells` of a row line up with `columns`; `column_totals` sum the rows per column."""

    row_dimension: Dimension
    bucket: Bucket
    columns: tuple[Period, ...]
    rows: tuple[MatrixRowView, ...]
    column_totals: tuple[Decimal, ...]


@dataclass(frozen=True)
class SpendingAnalysisView:
    """The sections the query did not `include` are None. `previous_time_series` is set when
    comparing. `cards` and `trips` carry what the labels of a card / trip dimension need."""

    query: AnalysisQuery
    range: DateRange
    comparison_range: DateRange | None
    totals: AnalysisTotalsView
    comparison_totals: AnalysisTotalsView | None
    groups: tuple[AnalysisGroupView, ...] | None = None
    time_series: TimeSeriesView | None = None
    previous_time_series: TimeSeriesView | None = None
    matrix: MatrixView | None = None
    cards: tuple[VisibleCardRecord, ...] = ()
    trips: tuple[TripRecord, ...] = ()


@dataclass(frozen=True)
class _Slice:
    """The aggregates of one date range."""

    totals: AnalysisTotalsView
    groups: tuple[AnalysisGroupRecord, ...] | None
    time_series: TimeSeriesView | None


class AnalysisService:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def analyze(self, context: AuthContext, query: AnalysisQuery) -> SpendingAnalysisView:
        """Raises InvalidQueryError when the range cannot be compared or has too many buckets.
        Every aggregate comes from one snapshot."""
        user_id = context.user_id
        today = self._clock.now().astimezone(UTC).date()
        current = DateRange(query.date_from, query.date_to)
        comparison = _comparison_range(query.compare, current)
        wants_groups = AnalysisInclude.GROUPS in query.include
        wants_series = AnalysisInclude.TIME_SERIES in query.include
        wants_matrix = AnalysisInclude.MATRIX in query.include
        if wants_series or wants_matrix:
            _check_bucket_count(query.bucket, current, comparison)
        row_dimension = _row_dimension(query)
        with self._uow_factory.create(isolation=Isolation.REPEATABLE_READ) as uow:
            repository = uow.analysis
            now = self._slice(
                repository, user_id, query, current, today, wants_groups, wants_series
            )
            before = (
                None
                if comparison is None
                else self._slice(
                    repository, user_id, query, comparison, today, wants_groups, wants_series
                )
            )
            cells = (
                repository.matrix(user_id, query, row_dimension, query.bucket)
                if wants_matrix
                else ()
            )
            labelled = _dimensions_to_label(query, wants_groups, wants_matrix, row_dimension)
            cards = uow.visible_cards.list_visible(user_id) if Dimension.CARD in labelled else ()
            trips = uow.trips.list(user_id) if Dimension.TRIP in labelled else ()
        columns = periods(current, query.bucket, today)
        return SpendingAnalysisView(
            query=query,
            range=current,
            comparison_range=comparison,
            totals=now.totals,
            comparison_totals=None if before is None else before.totals,
            groups=None
            if now.groups is None
            else _group_views(now.groups, None if before is None else before.groups),
            time_series=now.time_series,
            previous_time_series=None if before is None else before.time_series,
            matrix=_matrix(row_dimension, query.bucket, columns, cells) if wants_matrix else None,
            cards=cards,
            trips=trips,
        )

    def _slice(
        self,
        repository: AnalysisRepository,
        user_id: UserId,
        query: AnalysisQuery,
        days: DateRange,
        today: date,
        wants_groups: bool,
        wants_series: bool,
    ) -> _Slice:
        ranged = replace(query, date_from=days.start, date_to=days.end)
        months = repository.series(user_id, ranged, Bucket.MONTH, None)
        totals = AnalysisTotalsView(
            net=sum((month.net for month in months), Decimal(0)),
            count=sum(month.count for month in months),
            monthly_average=monthly_average({m.bucket_start: m.net for m in months}, days, today),
        )
        groups = repository.groups(user_id, ranged, query.group_by) if wants_groups else None
        series = None
        if wants_series:
            is_monthly = query.bucket is Bucket.MONTH
            points = (
                months if is_monthly else repository.series(user_id, ranged, query.bucket, None)
            )
            series = TimeSeriesView(
                _time_points(periods(days, query.bucket, today), points, query.bucket),
                totals.monthly_average.value if is_monthly else None,
            )
        return _Slice(totals, groups, series)


def _comparison_range(compare: CompareMode, current: DateRange) -> DateRange | None:
    try:
        match compare:
            case CompareMode.NONE:
                return None
            case CompareMode.PREVIOUS:
                return previous_range(current)
            case CompareMode.YOY:
                return year_over_year_range(current)
    except ValueError:
        raise InvalidQueryError(
            InvalidQueryError.INVALID_FILTER, "The comparison range is out of the calendar."
        ) from None


def _check_bucket_count(bucket: Bucket, current: DateRange, comparison: DateRange | None) -> None:
    ranges = (current,) if comparison is None else (current, comparison)
    if any(count_buckets(days, bucket) > MAX_BUCKETS for days in ranges):
        raise InvalidQueryError(
            InvalidQueryError.INVALID_FILTER,
            f"The range has more than {MAX_BUCKETS} {bucket.value} buckets.",
        )


def _row_dimension(query: AnalysisQuery) -> Dimension:
    """The matrix rows follow `group_by`; for a period grouping (which the columns already
    show) they follow `series_by`."""
    if query.group_by not in _PERIODS:
        return query.group_by
    return query.series_by or _DEFAULT_ROW_DIMENSION


def _dimensions_to_label(
    query: AnalysisQuery, wants_groups: bool, wants_matrix: bool, row_dimension: Dimension
) -> frozenset[Dimension]:
    return frozenset(
        ([query.group_by] if wants_groups else []) + ([row_dimension] if wants_matrix else [])
    )


def _key_order(key: str | None) -> tuple[bool, str]:
    return (key is None, key or "")


def _group_views(
    current: tuple[AnalysisGroupRecord, ...],
    previous: tuple[AnalysisGroupRecord, ...] | None,
) -> tuple[AnalysisGroupView, ...]:
    """Groups ordered by key, the missing group last. When comparing, a group present in only
    one of the two ranges is listed with zeros for the other."""
    now = {group.key: group for group in current}
    before = None if previous is None else {group.key: group for group in previous}
    keys = set(now) if before is None else set(now) | set(before)
    views: list[AnalysisGroupView] = []
    for key in sorted(keys, key=_key_order):
        group = now.get(key)
        earlier = None if before is None else before.get(key)
        views.append(
            AnalysisGroupView(
                key,
                group.net if group else Decimal(0),
                group.count if group else 0,
                None
                if before is None
                else PreviousGroupView(
                    earlier.net if earlier else Decimal(0), earlier.count if earlier else 0
                ),
            )
        )
    return tuple(views)


def _time_points(
    skeleton: Iterable[Period], records: Iterable[SeriesPointRecord], bucket: Bucket
) -> tuple[TimePointView, ...]:
    """Every bucket of the range, zero where the repository found no rows."""
    nets = {bucket_key(record.bucket_start, bucket): record.net for record in records}
    return tuple(TimePointView(period, nets.get(period.key, Decimal(0))) for period in skeleton)


def _matrix(
    row_dimension: Dimension,
    bucket: Bucket,
    columns: tuple[Period, ...],
    records: Iterable[MatrixCellRecord],
) -> MatrixView:
    nets: dict[str | None, dict[str, Decimal]] = {}
    for record in records:
        nets.setdefault(record.row_key, {})[bucket_key(record.bucket_start, bucket)] = record.net
    rows: list[MatrixRowView] = []
    for key in sorted(nets, key=_key_order):
        cells = tuple(nets[key].get(column.key, Decimal(0)) for column in columns)
        rows.append(MatrixRowView(key, cells, sum(cells, Decimal(0))))
    totals = tuple(
        sum((row.cells[index] for row in rows), Decimal(0)) for index in range(len(columns))
    )
    return MatrixView(row_dimension, bucket, columns, tuple(rows), totals)
