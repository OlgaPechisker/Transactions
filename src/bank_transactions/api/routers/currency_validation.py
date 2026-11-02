"""Currency validation queue and batches."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from bank_transactions.api.dependencies import (
    BatchIdPath,
    Composition,
    CsrfUser,
    CurrentUser,
    TransactionIdPath,
    sort_param,
)
from bank_transactions.api.schemas.currency_validation import CurrencyValidationBatchRequest
from bank_transactions.domain.enums import ValidationStatus
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import PageRequest, PendingValidationFilter, SortSpec
from bank_transactions.services.currency_validation_service import (
    BatchCommand,
    ValidationItemsQuery,
)

router = APIRouter(tags=["CurrencyValidation"])

VALIDATION_SORT_FIELDS = ("transactionDate", "merchant", "country", "billingAmount")


@router.get("/currency-validation-queue", operation_id="getCurrencyValidationQueue")
def get_currency_validation_queue(user: CurrentUser, comp: Composition) -> Any:
    comp.currency_validation.overview(user)
    raise NotImplementedError


@router.get("/currency-validation-queue/items", operation_id="listCurrencyValidationItems")
def list_currency_validation_items(
    user: CurrentUser,
    comp: Composition,
    sort: Annotated[SortSpec, Depends(sort_param(VALIDATION_SORT_FIELDS, "-transactionDate"))],
    status: ValidationStatus = ValidationStatus.PENDING,
    country: Annotated[list[str] | None, Query()] = None,
    merchant: Annotated[list[str] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, alias="pageSize")] = 40,
) -> Any:
    items_filter = PendingValidationFilter(
        countries=tuple(country or ()), merchants=tuple(merchant or ())
    )
    comp.currency_validation.items(
        user, ValidationItemsQuery(status, items_filter, sort, PageRequest(page, page_size))
    )
    raise NotImplementedError


@router.post(
    "/currency-validation-batches", operation_id="createCurrencyValidationBatch", status_code=201
)
def create_currency_validation_batch(
    body: CurrencyValidationBatchRequest, user: CsrfUser, comp: Composition
) -> Any:
    ids = None if body.transaction_ids is None else tuple(PublicId(i) for i in body.transaction_ids)
    selection = None
    if body.filter is not None:
        selection = PendingValidationFilter(
            tuple(body.filter.country),
            body.filter.date_from,
            body.filter.date_to,
            tuple(body.filter.merchant),
        )
    comp.currency_validation.create_batch(user, BatchCommand(body.currency, ids, selection))
    raise NotImplementedError


@router.delete(
    "/currency-validation-batches/{batchId}",
    operation_id="undoCurrencyValidationBatch",
    status_code=204,
)
def undo_currency_validation_batch(
    batch_id: BatchIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.currency_validation.undo_batch(user, batch_id)
    raise NotImplementedError


@router.delete(
    "/currency-validations/{transactionId}",
    operation_id="deleteCurrencyValidation",
    status_code=204,
)
def delete_currency_validation(
    transaction_id: TransactionIdPath, user: CsrfUser, comp: Composition
) -> None:
    comp.currency_validation.move_back(user, transaction_id)
    raise NotImplementedError
