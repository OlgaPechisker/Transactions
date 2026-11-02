"""Trips and their membership."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from bank_transactions.api.dependencies import (
    Composition,
    CsrfUser,
    CurrentUser,
    TransactionIdPath,
    TripIdPath,
    sort_param,
)
from bank_transactions.api.schemas.trips import CreateTripRequest
from bank_transactions.domain.queries import SortSpec
from bank_transactions.services.trip_service import NewTripCommand

router = APIRouter(tags=["Trips"])

TRIP_SORT_FIELDS = ("startDate", "name")


@router.get("/trips", operation_id="listTrips")
def list_trips(
    user: CurrentUser,
    comp: Composition,
    sort: Annotated[SortSpec, Depends(sort_param(TRIP_SORT_FIELDS, "-startDate"))],
) -> Any:
    comp.trips.list(user, sort)
    raise NotImplementedError


@router.post("/trips", operation_id="createTrip", status_code=201)
def create_trip(body: CreateTripRequest, user: CsrfUser, comp: Composition) -> Any:
    command = NewTripCommand(body.name, body.currency, body.start_date, body.end_date)
    comp.trips.create(user, command)
    raise NotImplementedError


@router.get("/trips/{tripId}", operation_id="getTrip")
def get_trip(trip_id: TripIdPath, user: CurrentUser, comp: Composition) -> Any:
    comp.trips.get(user, trip_id)
    raise NotImplementedError


@router.get("/trips/{tripId}/candidates", operation_id="listTripCandidates")
def list_trip_candidates(
    trip_id: TripIdPath,
    user: CurrentUser,
    comp: Composition,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 8,
) -> Any:
    comp.trips.candidates(user, trip_id, q, limit)
    raise NotImplementedError


@router.put(
    "/trips/{tripId}/tagged-transactions/{transactionId}",
    operation_id="tagTransaction",
    status_code=204,
)
def tag_transaction(
    trip_id: TripIdPath, transaction_id: TransactionIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.trips.tag(user, trip_id, transaction_id)
    raise NotImplementedError


@router.delete(
    "/trips/{tripId}/tagged-transactions/{transactionId}",
    operation_id="untagTransaction",
    status_code=204,
)
def untag_transaction(
    trip_id: TripIdPath, transaction_id: TransactionIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.trips.untag(user, trip_id, transaction_id)
    raise NotImplementedError


@router.put(
    "/trips/{tripId}/excluded-transactions/{transactionId}",
    operation_id="excludeTransaction",
    status_code=204,
)
def exclude_transaction(
    trip_id: TripIdPath, transaction_id: TransactionIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.trips.exclude(user, trip_id, transaction_id)
    raise NotImplementedError


@router.delete(
    "/trips/{tripId}/excluded-transactions/{transactionId}",
    operation_id="restoreTransaction",
    status_code=204,
)
def restore_transaction(
    trip_id: TripIdPath, transaction_id: TransactionIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.trips.restore(user, trip_id, transaction_id)
    raise NotImplementedError
