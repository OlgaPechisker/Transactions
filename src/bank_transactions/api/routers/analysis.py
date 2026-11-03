"""Spending analysis."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from bank_transactions.api.dependencies import (
    Composition,
    CountryCode,
    CurrencyOrUnknown,
    CurrentUser,
    include_param,
)
from bank_transactions.api.problems import problem_responses
from bank_transactions.api.schemas.analysis import (
    AnalysisGroupDto,
    AnalysisRangeDto,
    AnalysisTotalsDto,
    MatrixCellDto,
    MatrixColumnDto,
    MatrixDto,
    MatrixRowDto,
    MonthlyAverageDto,
    PreviousGroupDto,
    PreviousSeriesDto,
    SpendingAnalysisDto,
    TimePointDto,
    TimeSeriesDto,
)
from bank_transactions.api.schemas.mappers import CardPresent, to_money
from bank_transactions.domain.calendar import DateRange, Period
from bank_transactions.domain.enums import TransactionType
from bank_transactions.domain.errors import InvalidQueryError
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import (
    UNKNOWN,
    AnalysisInclude,
    AnalysisQuery,
    Bucket,
    CompareMode,
    Dimension,
    TransactionFilter,
)
from bank_transactions.services.analysis_service import (
    AnalysisGroupView,
    AnalysisTotalsView,
    MatrixView,
    SpendingAnalysisView,
    TimeSeriesView,
)
from bank_transactions.services.card_service import CardView
from bank_transactions.services.composition import ApiComposition
from bank_transactions.services.read_models import VisibleCardRecord

router = APIRouter(tags=["Analysis"])

LabelOf = Callable[[Dimension, str | None], str]

_DECIMAL_PATTERN = r"^-?\d+(\.\d+)?$"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Labels of the group without a value; the same as the transaction list groups.
_MISSING_LABELS = {
    Dimension.CATEGORY: "Uncategorized",
    Dimension.CUSTOM_CATEGORY: "No custom category",
    Dimension.COUNTRY: "Unknown country",
    Dimension.CARD: "No card",
    Dimension.CURRENCY: "No original currency",
    Dimension.TRIP: "No trip",
    Dimension.MERCHANT: "Unknown merchant",
    Dimension.TYPE: "Unknown type",
}


def analysis_filter_params(
    billing_currency: Annotated[str, Query(alias="billingCurrency", pattern=r"^[A-Z]{3}$")],
    category: Annotated[list[str] | None, Query()] = None,
    custom_category: Annotated[list[str] | None, Query(alias="customCategory")] = None,
    card_id: Annotated[list[str] | None, Query(alias="cardId")] = None,
    currency: Annotated[list[CurrencyOrUnknown] | None, Query()] = None,
    transaction_type: Annotated[list[TransactionType] | None, Query(alias="type")] = None,
    trip_id: Annotated[list[str] | None, Query(alias="tripId")] = None,
    country: Annotated[list[CountryCode] | None, Query()] = None,
    merchant: Annotated[list[str] | None, Query()] = None,
    min_amount: Annotated[
        str | None, Query(alias="minAmount", pattern=_DECIMAL_PATTERN, max_length=40)
    ] = None,
    max_amount: Annotated[
        str | None, Query(alias="maxAmount", pattern=_DECIMAL_PATTERN, max_length=40)
    ] = None,
) -> TransactionFilter:
    """Only the filters the analysis operation defines; the date range and the billing
    currency are the operation's own required parameters."""
    try:
        return TransactionFilter(
            categories=tuple(category or ()),
            custom_categories=tuple(custom_category or ()),
            card_ids=tuple(PublicId(value) for value in card_id or ()),
            currencies=tuple(currency or ()),
            billing_currency=billing_currency,
            min_amount=None if min_amount is None else Decimal(min_amount),
            max_amount=None if max_amount is None else Decimal(max_amount),
            merchants=tuple(merchant or ()),
            types=tuple(transaction_type or ()),
            countries=tuple(country or ()),
            trip_ids=tuple(trip_id or ()),
        )
    except ValueError:
        raise InvalidQueryError(
            InvalidQueryError.INVALID_FILTER, "A filter value is invalid."
        ) from None


def _label_of(
    dimension: Dimension,
    key: str,
    present_card: CardPresent,
    cards: dict[str, VisibleCardRecord],
    trips: dict[str, str],
) -> str:
    match dimension:
        case Dimension.CURRENCY if key == UNKNOWN:
            return "Unknown currency"
        case Dimension.MONTH:
            year, month = key.split("-")
            return f"{_MONTHS[int(month) - 1]} {year}"
        case Dimension.WEEK:
            return f"Week of {key}"
        case Dimension.CARD:
            return present_card(cards[key]).label if key in cards else key
        case Dimension.TRIP:
            return trips.get(key, key)
        case _:
            return key


def _label_function(view: SpendingAnalysisView, present_card: CardPresent) -> LabelOf:
    cards: dict[str, VisibleCardRecord] = {card.public_id: card for card in view.cards}
    trips: dict[str, str] = {trip.public_id: trip.name for trip in view.trips}

    def label_of(dimension: Dimension, key: str | None) -> str:
        if key is None:
            return _MISSING_LABELS[dimension]
        return _label_of(dimension, key, present_card, cards, trips)

    return label_of


def _range(days: DateRange) -> AnalysisRangeDto:
    return AnalysisRangeDto.model_validate(
        {"from": days.start.isoformat(), "to": days.end.isoformat()}
    )


def _totals(totals: AnalysisTotalsView, currency: str) -> AnalysisTotalsDto:
    average = totals.monthly_average
    return AnalysisTotalsDto(
        net=to_money(totals.net, currency),
        count=totals.count,
        monthly_average=MonthlyAverageDto(
            value=None if average.value is None else to_money(average.value, currency),
            complete_buckets=average.complete_months,
            partial_buckets=list(average.partial_months),
        ),
    )


def _point(period: Period, net: Decimal, currency: str) -> TimePointDto:
    return TimePointDto(
        key=period.key,
        start=period.start.isoformat(),
        end=period.end.isoformat(),
        net=to_money(net, currency),
        complete=period.complete,
    )


def _series_points(series: TimeSeriesView, currency: str) -> list[TimePointDto]:
    return [_point(point.period, point.net, currency) for point in series.points]


def _time_series(
    view: SpendingAnalysisView, series: TimeSeriesView, currency: str
) -> TimeSeriesDto:
    before = view.previous_time_series
    return TimeSeriesDto(
        bucket=view.query.bucket.value,
        average=None if series.average is None else to_money(series.average, currency),
        points=_series_points(series, currency),
        previous=None
        if before is None
        else PreviousSeriesDto(
            average=None if before.average is None else to_money(before.average, currency),
            points=_series_points(before, currency),
        ),
    )


def _group(group: AnalysisGroupView, currency: str, label: str) -> AnalysisGroupDto:
    previous = group.previous
    return AnalysisGroupDto(
        key=group.key,
        label=label,
        net=to_money(group.net, currency),
        count=group.count,
        previous=None
        if previous is None
        else PreviousGroupDto(net=to_money(previous.net, currency), count=previous.count),
    )


def _matrix(matrix: MatrixView, currency: str, label_of: LabelOf) -> MatrixDto:
    return MatrixDto(
        row_dimension=matrix.row_dimension.value,
        bucket=matrix.bucket.value,
        columns=[
            MatrixColumnDto(key=column.key, complete=column.complete) for column in matrix.columns
        ],
        rows=[
            MatrixRowDto(
                key=row.key,
                label=label_of(matrix.row_dimension, row.key),
                cells=[MatrixCellDto(net=to_money(cell, currency)) for cell in row.cells],
                total=to_money(row.total, currency),
            )
            for row in matrix.rows
        ],
        column_totals=[to_money(total, currency) for total in matrix.column_totals],
    )


def _to_dto(view: SpendingAnalysisView, present_card: CardPresent) -> SpendingAnalysisDto:
    currency = view.query.billing_currency
    label_of = _label_function(view, present_card)
    dto = SpendingAnalysisDto(
        range=_range(view.range),
        comparison_range=None if view.comparison_range is None else _range(view.comparison_range),
        totals=_totals(view.totals, currency),
        comparison_totals=None
        if view.comparison_totals is None
        else _totals(view.comparison_totals, currency),
    )
    if view.groups is not None:
        dimension = view.query.group_by
        dto.groups = [
            _group(group, currency, label_of(dimension, group.key)) for group in view.groups
        ]
    if view.time_series is not None:
        dto.time_series = _time_series(view, view.time_series, currency)
    if view.matrix is not None:
        dto.matrix = _matrix(view.matrix, currency, label_of)
    return dto


@router.get(
    "/spending-analysis",
    operation_id="getSpendingAnalysis",
    response_model_exclude_unset=True,
    responses=problem_responses(400),
)
def get_spending_analysis(
    user: CurrentUser,
    comp: Composition,
    filter: Annotated[TransactionFilter, Depends(analysis_filter_params)],
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    include: Annotated[
        frozenset[AnalysisInclude],
        Depends(include_param(AnalysisInclude, frozenset(AnalysisInclude))),
    ],
    compare: CompareMode = CompareMode.NONE,
    group_by: Annotated[Dimension, Query(alias="groupBy")] = Dimension.CATEGORY,
    bucket: Bucket = Bucket.MONTH,
    series_by: Annotated[Dimension, Query(alias="seriesBy")] = Dimension.CATEGORY,
) -> SpendingAnalysisDto:
    billing_currency = filter.billing_currency
    if billing_currency is None:
        raise InvalidQueryError(InvalidQueryError.INVALID_FILTER, "billingCurrency is required.")
    try:
        query = AnalysisQuery(
            filter,
            date_from,
            date_to,
            billing_currency,
            compare,
            group_by,
            bucket,
            series_by,
            include,
        )
    except ValueError as error:
        raise InvalidQueryError(InvalidQueryError.INVALID_FILTER, str(error)) from error
    view = comp.analysis.analyze(user, query)
    return _to_dto(view, _card_presenter(comp, user.user_display_name))


def _card_presenter(comp: ApiComposition, user_name: str) -> CardPresent:
    def present(record: VisibleCardRecord) -> CardView:
        return comp.card_presenter.present(record, user_name)

    return present
