"""Currency validation queue and batches."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from bank_transactions.api.dependencies import (
    BatchIdPath,
    Composition,
    CountryCode,
    CsrfUser,
    CurrentUser,
    TransactionIdPath,
    sort_param,
)
from bank_transactions.api.problems import problem_responses
from bank_transactions.api.schemas.currency_validation import (
    CurrencyOptionDto,
    CurrencyValidationBatchDto,
    CurrencyValidationBatchRequest,
    CurrencyValidationItemDto,
    CurrencyValidationItemsResponse,
    CurrencyValidationQueueDto,
    ValidationDto,
)
from bank_transactions.api.schemas.mappers import (
    format_amount,
    format_rate,
    format_timestamp,
    to_money,
    to_page_meta,
)
from bank_transactions.api.schemas.transactions import CountryOptionDto
from bank_transactions.domain.enums import ValidationStatus
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import PageRequest, PendingValidationFilter, SortSpec
from bank_transactions.services.currency_validation_service import (
    VALIDATION_SORT_FIELDS,
    BatchCommand,
    ValidationItemsQuery,
)
from bank_transactions.services.read_models import ValidationItemRecord

router = APIRouter(tags=["CurrencyValidation"])


def _implied_rate(billing: Decimal, original: Decimal | None) -> str | None:
    """Billing per unit of the original amount; undefined for a missing or zero original."""
    if original is None or original == 0:
        return None
    return format_rate(billing / original)


def _to_item(item: ValidationItemRecord) -> CurrencyValidationItemDto:
    record = item.transaction
    validation = None
    if item.validated_currency is not None:
        assert item.batch_public_id is not None and item.validated_at is not None
        validation = ValidationDto(
            currency=item.validated_currency,
            batch_id=item.batch_public_id,
            validated_at=format_timestamp(item.validated_at),
        )
    return CurrencyValidationItemDto(
        id=record.public_id,
        transaction_date=record.transaction_date.isoformat(),
        merchant=record.merchant,
        country=record.country,
        original_amount=format_amount(record.original_amount or Decimal(0)),
        billing=to_money(record.billing_amount, record.billing_currency),
        implied_rate=_implied_rate(record.billing_amount, record.original_amount),
        validation=validation,
    )


@router.get("/currency-validation-queue", operation_id="getCurrencyValidationQueue")
def get_currency_validation_queue(
    user: CurrentUser, comp: Composition
) -> CurrencyValidationQueueDto:
    overview = comp.currency_validation.overview(user)
    return CurrencyValidationQueueDto(
        total=overview.total,
        validated=overview.validated,
        pending=overview.pending,
        progress_percent=format(overview.progress_percent, "f"),
        countries=[
            CountryOptionDto(code=option.code, count=option.count)
            for option in overview.countries
            if option.code is not None
        ],
        currency_options=[
            CurrencyOptionDto(code=entry.code, name=entry.name) for entry in overview.currencies
        ],
    )


@router.get(
    "/currency-validation-queue/items",
    operation_id="listCurrencyValidationItems",
    responses=problem_responses(400),
)
def list_currency_validation_items(
    user: CurrentUser,
    comp: Composition,
    sort: Annotated[SortSpec, Depends(sort_param(VALIDATION_SORT_FIELDS, "-transactionDate"))],
    status: ValidationStatus = ValidationStatus.PENDING,
    country: Annotated[list[CountryCode] | None, Query()] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    merchant: Annotated[list[str] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, alias="pageSize")] = 40,
) -> CurrencyValidationItemsResponse:
    selection = PendingValidationFilter(
        countries=tuple(country or ()),
        date_from=date_from,
        date_to=date_to,
        merchants=tuple(merchant or ()),
    )
    result = comp.currency_validation.items(
        user, ValidationItemsQuery(status, selection, sort, PageRequest(page, page_size))
    )
    return CurrencyValidationItemsResponse(
        items=[_to_item(item) for item in result.items], meta=to_page_meta(result)
    )


@router.post(
    "/currency-validation-batches",
    operation_id="createCurrencyValidationBatch",
    status_code=201,
    responses=problem_responses(404, 409, 422),
)
def create_currency_validation_batch(
    body: CurrencyValidationBatchRequest,
    request: Request,
    response: Response,
    user: CsrfUser,
    comp: Composition,
) -> CurrencyValidationBatchDto:
    ids = None if body.transaction_ids is None else tuple(PublicId(i) for i in body.transaction_ids)
    selection = None
    if body.filter is not None:
        selection = PendingValidationFilter(
            tuple(body.filter.country),
            body.filter.date_from,
            body.filter.date_to,
            tuple(body.filter.merchant),
        )
    result = comp.currency_validation.create_batch(
        user, BatchCommand(body.currency, ids, selection)
    )
    response.headers["Location"] = f"{request.url.path}/{result.batch.public_id}"
    return CurrencyValidationBatchDto(
        id=result.batch.public_id,
        validated_count=result.batch.validated_count,
        transaction_ids=list(result.transaction_ids),
        created_at=format_timestamp(result.batch.created_at),
    )


@router.delete(
    "/currency-validation-batches/{batchId}",
    operation_id="undoCurrencyValidationBatch",
    status_code=204,
    responses=problem_responses(404, 409),
)
def undo_currency_validation_batch(
    batch_id: BatchIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.currency_validation.undo_batch(user, batch_id)


@router.delete(
    "/currency-validations/{transactionId}",
    operation_id="deleteCurrencyValidation",
    status_code=204,
    responses=problem_responses(404),
)
def delete_currency_validation(
    transaction_id: TransactionIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.currency_validation.move_back(user, transaction_id)
