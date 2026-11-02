"""Transaction list, detail and filter options (read side)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from bank_transactions.api.dependencies import (
    Composition,
    CurrentUser,
    FilterParams,
    Paging,
    TransactionIdPath,
    include_param,
    sort_param,
)
from bank_transactions.api.schemas.common import TransactionDetailDto
from bank_transactions.domain.queries import Dimension, SortSpec, TransactionInclude

router = APIRouter(tags=["Transactions"])

TRANSACTION_SORT_FIELDS = (
    "transactionDate",
    "chargeDate",
    "merchant",
    "country",
    "city",
    "category",
    "bankCategory",
    "card",
    "type",
    "originalAmount",
    "originalCurrency",
    "billingAmount",
    "effectiveRate",
    "commission",
    "trip",
    "notes",
    "reviewCount",
)


@router.get("/transactions", operation_id="listTransactions")
def list_transactions(
    user: CurrentUser,
    comp: Composition,
    filter: FilterParams,
    page: Paging,
    sort: Annotated[SortSpec, Depends(sort_param(TRANSACTION_SORT_FIELDS, "-transactionDate"))],
    include: Annotated[
        frozenset[TransactionInclude],
        Depends(include_param(TransactionInclude, frozenset({TransactionInclude.SUMMARY}))),
    ],
    group_by: Annotated[Dimension | None, Query(alias="groupBy")] = None,
) -> Any:
    comp.transactions.list(user, filter, group_by, include, sort, page)
    raise NotImplementedError


@router.get("/transactions/{transactionId}", operation_id="getTransaction")
def get_transaction(
    transaction_id: TransactionIdPath, user: CurrentUser, comp: Composition
) -> TransactionDetailDto:
    comp.transactions.get(user, transaction_id)
    raise NotImplementedError


@router.get("/filter-options", operation_id="getFilterOptions")
def get_filter_options(user: CurrentUser, comp: Composition) -> Any:
    comp.transactions.filter_options(user)
    raise NotImplementedError
