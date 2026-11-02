"""Spending analysis."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from bank_transactions.api.dependencies import (
    Composition,
    CurrentUser,
    FilterParams,
    include_param,
)
from bank_transactions.domain.errors import InvalidQueryError
from bank_transactions.domain.queries import (
    AnalysisInclude,
    AnalysisQuery,
    Bucket,
    CompareMode,
    Dimension,
)

router = APIRouter(tags=["Analysis"])


@router.get("/spending-analysis", operation_id="getSpendingAnalysis")
def get_spending_analysis(
    user: CurrentUser,
    comp: Composition,
    filter: FilterParams,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    billing_currency: Annotated[str, Query(alias="billingCurrency", pattern=r"^[A-Z]{3}$")],
    include: Annotated[
        frozenset[AnalysisInclude],
        Depends(include_param(AnalysisInclude, frozenset(AnalysisInclude))),
    ],
    compare: CompareMode = CompareMode.NONE,
    group_by: Annotated[Dimension, Query(alias="groupBy")] = Dimension.CATEGORY,
    bucket: Bucket = Bucket.MONTH,
    series_by: Annotated[Dimension, Query(alias="seriesBy")] = Dimension.CATEGORY,
) -> Any:
    try:
        query = AnalysisQuery(
            filter, date_from, date_to, billing_currency, compare, group_by, bucket, series_by,
            include,
        )
    except ValueError as error:
        raise InvalidQueryError(InvalidQueryError.INVALID_FILTER, str(error)) from error
    comp.analysis.analyze(user, query)
    raise NotImplementedError
