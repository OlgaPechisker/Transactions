"""Trips and their membership."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from bank_transactions.api.dependencies import (
    Composition,
    CsrfUser,
    CurrentUser,
    TransactionIdPath,
    TripIdPath,
    single_sort_param,
)
from bank_transactions.api.problems import problem_responses
from bank_transactions.api.schemas.common import TransactionListItemDto
from bank_transactions.api.schemas.mappers import to_billing_summary, to_transaction_list_item
from bank_transactions.api.schemas.trips import (
    CreateTripRequest,
    TripCandidatesResponse,
    TripCategoryDto,
    TripDayDto,
    TripDetailDto,
    TripDetailStatsDto,
    TripListResponse,
    TripSummaryDto,
    TripSummaryStatsDto,
)
from bank_transactions.domain.queries import SortSpec
from bank_transactions.services.read_models import (
    TransactionViewRecord,
    TripRecord,
    TripSummaryRecord,
)
from bank_transactions.services.trip_service import NewTripCommand, TripDetailView

router = APIRouter(tags=["Trips"])

TRIP_SORT_FIELDS = ("startDate", "name")
UNCATEGORIZED_LABEL = "Uncategorized"


def _days(trip: TripRecord) -> int:
    return (trip.end_date - trip.start_date).days + 1


def _to_summary(record: TripSummaryRecord) -> TripSummaryDto:
    trip = record.trip
    return TripSummaryDto(
        id=trip.public_id,
        name=trip.name,
        currency=trip.currency,
        start_date=trip.start_date.isoformat(),
        end_date=trip.end_date.isoformat(),
        days=_days(trip),
        stats=TripSummaryStatsDto(
            **to_billing_summary(record.summary).model_dump(),
            tagged_count=record.tagged_count,
            auto_count=record.auto_count,
        ),
    )


def _list_items(
    records: tuple[TransactionViewRecord, ...], comp: Composition, display_name: str
) -> list[TransactionListItemDto]:
    return [
        to_transaction_list_item(
            record, lambda card: comp.card_presenter.present(card, display_name)
        )
        for record in records
    ]


def _detail_dto(view: TripDetailView, comp: Composition, display_name: str) -> TripDetailDto:
    trip, breakdown, members = view.trip, view.breakdown, view.members
    return TripDetailDto(
        id=trip.public_id,
        name=trip.name,
        currency=trip.currency,
        start_date=trip.start_date.isoformat(),
        end_date=trip.end_date.isoformat(),
        days=_days(trip),
        stats=TripDetailStatsDto(
            **to_billing_summary(view.summary).model_dump(),
            unknown_currency_count=view.summary.unknown_currency_count,
            outside_range=to_billing_summary(breakdown.outside_range),
        ),
        by_category=[
            TripCategoryDto(
                **to_billing_summary(group.summary).model_dump(),
                category=group.key,
                label=UNCATEGORIZED_LABEL if group.key is None else group.key,
            )
            for group in breakdown.by_category
        ],
        by_day=[
            TripDayDto(**to_billing_summary(day.summary).model_dump(), date=day.day.isoformat())
            for day in breakdown.by_day
        ],
        tagged=_list_items(members.tagged, comp, display_name),
        auto=_list_items(members.auto, comp, display_name),
        excluded=_list_items(members.excluded, comp, display_name),
    )


@router.get("/trips", operation_id="listTrips", responses=problem_responses(400))
def list_trips(
    user: CurrentUser,
    comp: Composition,
    sort: Annotated[SortSpec, Depends(single_sort_param(TRIP_SORT_FIELDS, "-startDate"))],
) -> TripListResponse:
    return TripListResponse(items=[_to_summary(record) for record in comp.trips.list(user, sort)])


@router.post(
    "/trips", operation_id="createTrip", status_code=201, responses=problem_responses(409, 422)
)
def create_trip(
    body: CreateTripRequest,
    request: Request,
    response: Response,
    user: CsrfUser,
    comp: Composition,
) -> TripDetailDto:
    command = NewTripCommand(body.name, body.currency, body.start_date, body.end_date)
    view = comp.trips.create(user, command)
    response.headers["Location"] = f"{request.url.path}/{view.trip.public_id}"
    return _detail_dto(view, comp, user.user_display_name)


@router.get("/trips/{tripId}", operation_id="getTrip", responses=problem_responses(404))
def get_trip(trip_id: TripIdPath, user: CurrentUser, comp: Composition) -> TripDetailDto:
    return _detail_dto(comp.trips.get(user, trip_id), comp, user.user_display_name)


@router.get(
    "/trips/{tripId}/candidates",
    operation_id="listTripCandidates",
    responses=problem_responses(404),
)
def list_trip_candidates(
    trip_id: TripIdPath,
    user: CurrentUser,
    comp: Composition,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 8,
) -> TripCandidatesResponse:
    records = comp.trips.candidates(user, trip_id, q, limit)
    return TripCandidatesResponse(items=_list_items(records, comp, user.user_display_name))


@router.put(
    "/trips/{tripId}/tagged-transactions/{transactionId}",
    operation_id="tagTransaction",
    status_code=204,
    responses=problem_responses(404),
)
def tag_transaction(
    trip_id: TripIdPath, transaction_id: TransactionIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.trips.tag(user, trip_id, transaction_id)


@router.delete(
    "/trips/{tripId}/tagged-transactions/{transactionId}",
    operation_id="untagTransaction",
    status_code=204,
    responses=problem_responses(404),
)
def untag_transaction(
    trip_id: TripIdPath, transaction_id: TransactionIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.trips.untag(user, trip_id, transaction_id)


@router.put(
    "/trips/{tripId}/excluded-transactions/{transactionId}",
    operation_id="excludeTransaction",
    status_code=204,
    responses=problem_responses(404, 409),
)
def exclude_transaction(
    trip_id: TripIdPath, transaction_id: TransactionIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.trips.exclude(user, trip_id, transaction_id)


@router.delete(
    "/trips/{tripId}/excluded-transactions/{transactionId}",
    operation_id="restoreTransaction",
    status_code=204,
    responses=problem_responses(404),
)
def restore_transaction(
    trip_id: TripIdPath, transaction_id: TransactionIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.trips.restore(user, trip_id, transaction_id)
