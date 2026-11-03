"""The analysis service: calendar rules around the repository aggregates, query validation and
one snapshot per analysis."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from tests.fakes.auth import make_auth_context
from tests.fakes.clock import FakeClock
from tests.fakes.unit_of_work import InMemoryUnitOfWorkFactory

from bank_transactions.domain.calendar import bucket_key, bucket_start
from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.errors import InvalidQueryError
from bank_transactions.domain.ids import CardId, IdentifierToken, PublicId, TripId
from bank_transactions.domain.queries import (
    AnalysisInclude,
    AnalysisQuery,
    Bucket,
    CompareMode,
    Dimension,
    TransactionFilter,
)
from bank_transactions.persistence.interfaces import AnalysisRepository, Isolation
from bank_transactions.persistence.records import (
    AnalysisGroupRecord,
    MatrixCellRecord,
    SeriesPointRecord,
    TripRecord,
    VisibleCardRecord,
)
from bank_transactions.services.analysis_service import AnalysisService

ANN = make_auth_context(user_id=1)
EVERYTHING = frozenset(AnalysisInclude)
CARD = VisibleCardRecord(
    CardId(1), PublicId("crd_1"), IssuerCode.MAX, IdentifierToken(b"enc"), None, None
)
TRIP = TripRecord(TripId(1), PublicId("trp_1"), "Rome", "EUR", date(2025, 7, 1), date(2025, 7, 9))
START = date(2025, 1, 1)  # a Wednesday
MONDAY = date(2025, 1, 6)
_PERIOD_BUCKETS = {
    Dimension.DAY: Bucket.DAY,
    Dimension.WEEK: Bucket.WEEK,
    Dimension.MONTH: Bucket.MONTH,
    Dimension.YEAR: Bucket.YEAR,
}


class Ledger(AnalysisRepository):
    """Signed billing rows (day, category, merchant, net) answered like the SQL provider does:
    only the groups and buckets that have rows, within the query's range."""

    def __init__(self, rows: list[tuple[date, str | None, str, str]]) -> None:
        self.rows = rows
        self.matrix_rows: list[Dimension] = []

    def _inside(self, query: AnalysisQuery):
        return [r for r in self.rows if query.date_from <= r[0] <= query.date_to]

    def groups(self, user_id, query, dimension):
        totals: dict[str | None, list[Decimal]] = defaultdict(list)
        for day, category, _, net in self._inside(query):
            key = category
            if dimension in _PERIOD_BUCKETS:
                key = bucket_key(
                    bucket_start(day, _PERIOD_BUCKETS[dimension]), _PERIOD_BUCKETS[dimension]
                )
            totals[key].append(Decimal(net))
        return tuple(
            AnalysisGroupRecord(key, len(nets), sum(nets, Decimal(0)))
            for key, nets in totals.items()
        )

    def series(self, user_id, query, bucket, series_dimension):
        totals: dict[date, list[Decimal]] = defaultdict(list)
        for day, _, _, net in self._inside(query):
            totals[bucket_start(day, bucket)].append(Decimal(net))
        return tuple(
            SeriesPointRecord(start, None, len(nets), sum(nets, Decimal(0)))
            for start, nets in totals.items()
        )

    def matrix(self, user_id, query, row_dimension, bucket):
        self.matrix_rows.append(row_dimension)
        cells: dict[tuple[str | None, date], list[Decimal]] = defaultdict(list)
        for day, category, merchant, net in self._inside(query):
            row = merchant if row_dimension is Dimension.MERCHANT else category
            cells[row, bucket_start(day, bucket)].append(Decimal(net))
        return tuple(
            MatrixCellRecord(row, start, len(nets), sum(nets, Decimal(0)))
            for (row, start), nets in cells.items()
        )


class FakeVisibleCards:
    def __init__(self) -> None:
        self.calls = 0

    def list_visible(self, user_id):
        self.calls += 1
        return (CARD,)


class FakeTrips:
    def __init__(self) -> None:
        self.calls = 0

    def list(self, user_id):
        self.calls += 1
        return (TRIP,)


ROWS = [
    (date(2025, 1, 5), "Food", "Cafe", "-100.00"),
    (date(2025, 1, 20), "Food", "Cafe", "-40.00"),
    (date(2025, 1, 21), None, "Cafe", "-10.00"),
    (date(2025, 3, 2), "Food", "Market", "-60.00"),
    (date(2025, 3, 3), "Fun", "Cinema", "25.00"),
    (date(2024, 12, 15), "Food", "Cafe", "-30.00"),
]


@pytest.fixture
def ledger() -> Ledger:
    return Ledger(list(ROWS))


@pytest.fixture
def cards() -> FakeVisibleCards:
    return FakeVisibleCards()


@pytest.fixture
def trips() -> FakeTrips:
    return FakeTrips()


@pytest.fixture
def factory(ledger, cards, trips) -> InMemoryUnitOfWorkFactory:
    factory = InMemoryUnitOfWorkFactory()
    factory.template.analysis = ledger
    factory.template.visible_cards = cards
    factory.template.trips = trips
    return factory


@pytest.fixture
def service(factory) -> AnalysisService:
    return AnalysisService(factory, FakeClock(datetime(2025, 6, 10, 12, 0, tzinfo=UTC)))


def query(**overrides) -> AnalysisQuery:
    values = {
        "filter": TransactionFilter(),
        "date_from": date(2025, 1, 1),
        "date_to": date(2025, 3, 31),
        "billing_currency": "ILS",
        "include": EVERYTHING,
    }
    return AnalysisQuery(**{**values, **overrides})


def test_the_whole_analysis_comes_from_one_repeatable_read_snapshot(service, factory):
    service.analyze(ANN, query())

    assert [isolation for _, isolation in factory.created] == [Isolation.REPEATABLE_READ]


def test_totals_sum_every_row_of_the_range_with_its_sign(service):
    result = service.analyze(ANN, query())

    assert (result.totals.net, result.totals.count) == (Decimal("-185.00"), 5)
    assert result.comparison_range is None
    assert result.comparison_totals is None


def test_empty_months_are_zero_filled_in_the_time_series(service):
    result = service.analyze(ANN, query())

    assert result.time_series is not None
    points = [(p.period.key, p.net) for p in result.time_series.points]
    assert points == [
        ("2025-01", Decimal("-150.00")),
        ("2025-02", Decimal("0")),
        ("2025-03", Decimal("-35.00")),
    ]
    assert [p.period.complete for p in result.time_series.points] == [True, True, True]


def test_the_monthly_average_counts_only_active_complete_months(service):
    result = service.analyze(ANN, query())

    average = result.totals.monthly_average
    assert average.value == Decimal("-92.50")
    assert average.complete_months == 2
    assert average.partial_months == ()
    assert result.time_series is not None
    assert result.time_series.average == Decimal("-92.50")


def test_the_current_month_is_partial_and_left_out_of_the_average(service):
    result = service.analyze(ANN, query(date_to=date(2025, 6, 30)))

    average = result.totals.monthly_average
    assert average.complete_months == 2
    assert average.partial_months == ("2025-06",)


def test_today_is_the_utc_date(factory):
    # 23:30 on March 31 in UTC-5 is already April 1 in UTC
    now = datetime(2025, 3, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    service = AnalysisService(factory, FakeClock(now))

    result = service.analyze(ANN, query())

    assert result.time_series is not None
    assert [p.period.complete for p in result.time_series.points] == [True, True, True]


def test_only_month_buckets_carry_an_average(service):
    weekly = service.analyze(ANN, query(bucket=Bucket.WEEK))

    assert weekly.time_series is not None
    assert weekly.time_series.average is None
    assert weekly.totals.monthly_average.complete_months == 2


def test_weeks_are_keyed_by_their_monday_clipped_and_zero_filled(service):
    result = service.analyze(
        ANN, query(date_from=date(2025, 3, 1), date_to=date(2025, 3, 16), bucket=Bucket.WEEK)
    )

    assert result.time_series is not None
    points = result.time_series.points
    assert [(p.period.key, p.net, p.period.complete) for p in points] == [
        ("2025-02-24", Decimal("-60.00"), False),
        ("2025-03-03", Decimal("25.00"), True),
        ("2025-03-10", Decimal("0"), True),
    ]
    assert points[0].period.start == date(2025, 3, 1)


def test_groups_are_ordered_by_key_with_the_missing_group_last(service):
    result = service.analyze(ANN, query())

    assert result.groups is not None
    assert [(g.key, g.net, g.count) for g in result.groups] == [
        ("Food", Decimal("-200.00"), 3),
        ("Fun", Decimal("25.00"), 1),
        (None, Decimal("-10.00"), 1),
    ]
    assert all(g.previous is None for g in result.groups)


def test_comparing_with_the_previous_range_uses_the_same_number_of_days_before(service):
    result = service.analyze(
        ANN,
        query(date_from=date(2025, 3, 1), date_to=date(2025, 3, 31), compare=CompareMode.PREVIOUS),
    )

    assert result.comparison_range is not None
    assert (result.comparison_range.start, result.comparison_range.end) == (
        date(2025, 1, 29),
        date(2025, 2, 28),
    )
    assert result.comparison_totals is not None
    assert (result.comparison_totals.net, result.comparison_totals.count) == (Decimal(0), 0)


def test_compared_groups_cover_both_ranges_with_zeros_for_the_missing_side(service):
    result = service.analyze(
        ANN,
        query(date_from=date(2025, 1, 1), date_to=date(2025, 1, 31), compare=CompareMode.YOY),
    )

    assert result.groups is not None
    by_key = {g.key: g for g in result.groups}
    assert by_key["Food"].net == Decimal("-140.00")
    assert by_key["Food"].previous is not None
    assert (by_key["Food"].previous.net, by_key["Food"].previous.count) == (Decimal(0), 0)
    assert result.comparison_range is not None
    assert result.comparison_range.start == date(2024, 1, 1)


def test_groups_of_the_comparison_range_only_are_listed_with_zero_current(service):
    result = service.analyze(
        ANN,
        query(date_from=date(2025, 1, 1), date_to=date(2025, 1, 31), compare=CompareMode.PREVIOUS),
    )

    assert result.groups is not None
    food = next(g for g in result.groups if g.key == "Food")
    assert food.net == Decimal("-140.00")
    assert food.previous is not None
    assert (food.previous.net, food.previous.count) == (Decimal("-30.00"), 1)
    assert result.comparison_totals is not None
    assert result.comparison_totals.net == Decimal("-30.00")


def test_period_groups_are_compared_with_the_period_at_the_same_position(service):
    result = service.analyze(
        ANN,
        query(
            group_by=Dimension.MONTH,
            date_from=date(2025, 1, 1),
            date_to=date(2025, 3, 31),
            compare=CompareMode.PREVIOUS,
        ),
    )

    assert result.groups is not None
    assert [
        (g.key, g.net, g.count, g.previous.net, g.previous.count)
        for g in result.groups
        if g.previous is not None
    ] == [
        ("2025-01", Decimal("-150.00"), 3, Decimal(0), 0),
        ("2025-03", Decimal("-35.00"), 2, Decimal("-30.00"), 1),
    ]
    assert len(result.groups) == 2


def test_period_groups_never_list_a_period_of_the_comparison_range_only(service, ledger):
    ledger.rows += [
        (date(2024, 2, 10), "Food", "Cafe", "-20.00"),
        (date(2024, 3, 5), "Food", "Cafe", "-20.00"),
    ]

    result = service.analyze(
        ANN,
        query(
            group_by=Dimension.MONTH,
            date_from=date(2025, 1, 1),
            date_to=date(2025, 3, 31),
            compare=CompareMode.YOY,
        ),
    )

    assert result.groups is not None
    assert [g.key for g in result.groups] == ["2025-01", "2025-03"]
    assert [(g.previous.net, g.previous.count) for g in result.groups if g.previous] == [
        (Decimal(0), 0),
        (Decimal("-20.00"), 1),
    ]


def test_a_year_group_is_compared_with_the_year_of_the_comparison_range(service):
    result = service.analyze(
        ANN,
        query(
            group_by=Dimension.YEAR,
            date_from=date(2025, 1, 1),
            date_to=date(2025, 3, 31),
            compare=CompareMode.PREVIOUS,
        ),
    )

    assert result.groups is not None
    assert [g.key for g in result.groups] == ["2025"]
    assert result.groups[0].previous is not None
    assert (result.groups[0].previous.net, result.groups[0].previous.count) == (
        Decimal("-30.00"),
        1,
    )


def test_a_group_only_in_the_comparison_range_has_zero_current(service):
    result = service.analyze(
        ANN,
        query(date_from=date(2025, 2, 1), date_to=date(2025, 3, 31), compare=CompareMode.PREVIOUS),
    )

    assert result.groups is not None
    none_group = next(g for g in result.groups if g.key is None)
    assert (none_group.net, none_group.count) == (Decimal(0), 0)
    assert none_group.previous is not None
    assert (none_group.previous.net, none_group.previous.count) == (Decimal("-10.00"), 1)


def test_the_comparison_series_has_its_own_zero_filled_buckets(service):
    result = service.analyze(
        ANN,
        query(date_from=date(2025, 1, 1), date_to=date(2025, 1, 31), compare=CompareMode.PREVIOUS),
    )

    assert result.previous_time_series is not None
    points = result.previous_time_series.points
    assert [(p.period.key, p.net, p.period.complete) for p in points] == [
        ("2024-12", Decimal("-30.00"), True)
    ]


def test_year_over_year_maps_a_leap_day_to_february_28(service):
    result = service.analyze(
        ANN,
        query(date_from=date(2024, 2, 29), date_to=date(2024, 3, 10), compare=CompareMode.YOY),
    )

    assert result.comparison_range is not None
    assert (result.comparison_range.start, result.comparison_range.end) == (
        date(2023, 2, 28),
        date(2023, 3, 10),
    )


@pytest.mark.parametrize("compare", [CompareMode.PREVIOUS, CompareMode.YOY])
def test_a_comparison_before_the_first_calendar_year_is_an_invalid_filter(
    service, factory, compare
):
    with pytest.raises(InvalidQueryError) as raised:
        service.analyze(
            ANN, query(date_from=date(1, 1, 1), date_to=date(1, 1, 31), compare=compare)
        )

    assert raised.value.code == InvalidQueryError.INVALID_FILTER
    assert factory.created == []


@pytest.mark.parametrize(
    ("bucket", "first", "last"),
    [
        (Bucket.DAY, START, START + timedelta(days=1000)),
        (Bucket.WEEK, MONDAY, MONDAY + timedelta(weeks=1000)),
        (Bucket.MONTH, START, date(2108, 5, 1)),
    ],
)
def test_more_than_a_thousand_buckets_is_an_invalid_filter(service, factory, bucket, first, last):
    with pytest.raises(InvalidQueryError) as raised:
        service.analyze(ANN, query(date_from=first, date_to=last, bucket=bucket))

    assert raised.value.code == InvalidQueryError.INVALID_FILTER
    assert factory.created == []


@pytest.mark.parametrize(
    ("bucket", "first", "last"),
    [
        (Bucket.DAY, START, START + timedelta(days=999)),
        (Bucket.WEEK, MONDAY, MONDAY + timedelta(weeks=999)),
        (Bucket.MONTH, START, date(2108, 4, 30)),
    ],
)
def test_a_thousand_buckets_are_allowed(service, bucket, first, last):
    result = service.analyze(ANN, query(date_from=first, date_to=last, bucket=bucket))

    assert result.time_series is not None
    assert len(result.time_series.points) == 1000


def test_the_bucket_limit_also_applies_to_the_comparison_range(service, factory):
    # a thousand days, but the same dates a year earlier include a leap day
    days = {"date_from": START, "date_to": START + timedelta(days=999), "bucket": Bucket.DAY}
    service.analyze(ANN, query(**days))

    with pytest.raises(InvalidQueryError) as raised:
        service.analyze(ANN, query(**days, compare=CompareMode.YOY))

    assert raised.value.code == InvalidQueryError.INVALID_FILTER


def test_a_long_range_is_fine_when_no_buckets_are_shown(service):
    result = service.analyze(
        ANN,
        query(
            date_from=date(2000, 1, 1),
            date_to=date(2025, 12, 31),
            bucket=Bucket.DAY,
            include=frozenset({AnalysisInclude.GROUPS}),
        ),
    )

    assert result.groups is not None
    assert result.totals.monthly_average.complete_months == 3
    assert result.time_series is None
    assert result.matrix is None


@pytest.mark.parametrize("compare", [CompareMode.NONE, CompareMode.PREVIOUS])
@pytest.mark.parametrize("bucket", [Bucket.DAY, Bucket.MONTH])
def test_a_range_of_more_than_a_thousand_months_is_an_invalid_filter_even_without_buckets(
    service, factory, bucket, compare
):
    with pytest.raises(InvalidQueryError) as raised:
        service.analyze(
            ANN,
            query(
                date_from=date(1, 1, 1),
                date_to=date(9999, 12, 31),
                bucket=bucket,
                compare=compare,
                include=frozenset({AnalysisInclude.GROUPS}),
            ),
        )

    assert raised.value.code == InvalidQueryError.INVALID_FILTER
    assert factory.created == []


@pytest.mark.parametrize(
    "include",
    [
        frozenset(),
        frozenset({AnalysisInclude.GROUPS}),
        frozenset({AnalysisInclude.TIME_SERIES}),
        frozenset({AnalysisInclude.MATRIX}),
    ],
)
def test_only_the_requested_sections_are_returned(service, include):
    result = service.analyze(ANN, query(include=include))

    assert (
        result.groups is not None,
        result.time_series is not None,
        result.matrix is not None,
    ) == (
        AnalysisInclude.GROUPS in include,
        AnalysisInclude.TIME_SERIES in include,
        AnalysisInclude.MATRIX in include,
    )
    assert result.totals.count == 5


def test_matrix_rows_are_the_group_dimension_with_zero_filled_cells(service):
    result = service.analyze(ANN, query())

    assert result.matrix is not None
    assert result.matrix.row_dimension is Dimension.CATEGORY
    assert [column.key for column in result.matrix.columns] == ["2025-01", "2025-02", "2025-03"]
    assert [row.key for row in result.matrix.rows] == ["Food", "Fun", None]
    food = result.matrix.rows[0]
    assert food.cells == (Decimal("-140.00"), Decimal(0), Decimal("-60.00"))
    assert food.total == Decimal("-200.00")
    assert result.matrix.column_totals == (Decimal("-150.00"), Decimal(0), Decimal("-35.00"))


def test_a_period_grouping_takes_the_matrix_rows_from_series_by(service, ledger):
    result = service.analyze(ANN, query(group_by=Dimension.MONTH, series_by=Dimension.MERCHANT))

    assert result.matrix is not None
    assert result.matrix.row_dimension is Dimension.MERCHANT
    assert ledger.matrix_rows == [Dimension.MERCHANT]
    assert [row.key for row in result.matrix.rows] == ["Cafe", "Cinema", "Market"]


def test_series_by_is_ignored_when_the_group_is_not_a_period(service):
    result = service.analyze(ANN, query(group_by=Dimension.CATEGORY, series_by=Dimension.MERCHANT))

    assert result.matrix is not None
    assert result.matrix.row_dimension is Dimension.CATEGORY


def test_cards_and_trips_are_loaded_only_for_a_card_or_trip_dimension(service, cards, trips):
    service.analyze(ANN, query())
    assert (cards.calls, trips.calls) == (0, 0)

    by_card = service.analyze(ANN, query(group_by=Dimension.CARD))
    assert (cards.calls, trips.calls) == (1, 0)
    assert by_card.cards == (CARD,)

    by_trip = service.analyze(ANN, query(group_by=Dimension.TRIP))
    assert (cards.calls, trips.calls) == (1, 1)
    assert by_trip.trips == (TRIP,)


def test_the_matrix_rows_can_need_the_cards_even_when_groups_are_not_shown(service, cards):
    service.analyze(
        ANN,
        query(
            group_by=Dimension.WEEK,
            series_by=Dimension.CARD,
            include=frozenset({AnalysisInclude.MATRIX}),
        ),
    )

    assert cards.calls == 1
