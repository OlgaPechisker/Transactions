"""Transaction list, detail and filter options (read side)."""

from datetime import date
from typing import Annotated

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
from bank_transactions.api.problems import problem_responses
from bank_transactions.api.schemas.common import GroupDto, TransactionDetailDto
from bank_transactions.api.schemas.mappers import (
    CardPresent,
    to_billing_summary,
    to_card_ref_dto,
    to_group,
    to_page_meta,
    to_transaction_detail,
    to_transaction_list_item,
)
from bank_transactions.api.schemas.transactions import (
    BankCategoryOptionDto,
    CountryOptionDto,
    CurrencyCountDto,
    CustomCategoryOptionDto,
    DateRangeDto,
    FilterOptionsDto,
    TransactionListResponse,
    TransactionSummaryDto,
    TripOptionDto,
)
from bank_transactions.domain.queries import UNKNOWN, Dimension, SortSpec, TransactionInclude
from bank_transactions.services.card_service import CardView
from bank_transactions.services.composition import ApiComposition
from bank_transactions.services.read_models import (
    BillingSummaryRecord,
    GroupRecord,
    VisibleCardRecord,
)
from bank_transactions.services.transaction_service import (
    TRANSACTION_SORT_FIELDS,
    TransactionPage,
)

router = APIRouter(tags=["Transactions"])

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Labels of the group without a value (§1.9; the other dimensions follow the OQ-2 defaults).
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


def _to_summary(summary: BillingSummaryRecord) -> TransactionSummaryDto:
    return TransactionSummaryDto(
        **to_billing_summary(summary).model_dump(),
        unknown_currency_count=summary.unknown_currency_count,
    )


def _label_of(
    dimension: Dimension, key: str, present_card: CardPresent, result: TransactionPage
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
            cards: dict[str, VisibleCardRecord] = {card.public_id: card for card in result.cards}
            return present_card(cards[key]).label if key in cards else key
        case Dimension.TRIP:
            names: dict[str, str] = {trip.public_id: trip.name for trip in result.trips}
            return names.get(key, key)
        case _:
            return key


def _to_groups(
    groups: tuple[GroupRecord, ...],
    dimension: Dimension,
    present_card: CardPresent,
    result: TransactionPage,
) -> list[GroupDto]:
    return [
        to_group(
            group,
            _MISSING_LABELS[dimension]
            if group.key is None
            else _label_of(dimension, group.key, present_card, result),
        )
        for group in groups
    ]


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


@router.get(
    "/transactions",
    operation_id="listTransactions",
    response_model_exclude_unset=True,
    responses=problem_responses(400),
)
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
) -> TransactionListResponse:
    result = comp.transactions.list(user, filter, group_by, include, sort, page)
    present_card = _card_presenter(comp, user.user_display_name)
    response = TransactionListResponse(
        items=[to_transaction_list_item(record, present_card) for record in result.page.items],
        meta=to_page_meta(result.page),
    )
    if result.summary is not None:
        response.summary = _to_summary(result.summary)
    if result.groups is not None and group_by is not None:
        response.groups = _to_groups(result.groups, group_by, present_card, result)
    return response


def _card_presenter(comp: ApiComposition, user_name: str) -> CardPresent:
    def present(record: VisibleCardRecord) -> CardView:
        return comp.card_presenter.present(record, user_name)

    return present


@router.get(
    "/transactions/{transactionId}",
    operation_id="getTransaction",
    responses=problem_responses(404),
)
def get_transaction(
    transaction_id: TransactionIdPath, user: CurrentUser, comp: Composition
) -> TransactionDetailDto:
    record = comp.transactions.get(user, transaction_id)
    return to_transaction_detail(record, _card_presenter(comp, user.user_display_name))


@router.get("/filter-options", operation_id="getFilterOptions")
def get_filter_options(user: CurrentUser, comp: Composition) -> FilterOptionsDto:
    view = comp.transactions.filter_options(user)
    options = view.options
    present_card = _card_presenter(comp, user.user_display_name)
    return FilterOptionsDto(
        categories=[
            BankCategoryOptionDto(bank=o.name, count=o.count) for o in options.bank_categories
        ],
        custom_categories=[
            CustomCategoryOptionDto(name=o.name, count=o.count) for o in options.custom_categories
        ],
        cards=[to_card_ref_dto(present_card(card)) for card in options.cards],
        currencies=[
            CurrencyCountDto(code=o.code, count=o.count) for o in options.original_currencies
        ],
        unknown_currency_count=options.unknown_currency_count,
        billing_currencies=[
            CurrencyCountDto(code=o.code, count=o.count) for o in options.billing_currencies
        ],
        unknown_billing_currency_count=options.unknown_billing_currency_count,
        types=[kind.value for kind in options.types],
        countries=[CountryOptionDto(code=o.code, count=o.count) for o in options.countries],
        trips=[TripOptionDto(id=trip.public_id, name=trip.name) for trip in view.trips],
        date_range=DateRangeDto(min=_iso(options.date_min), max=_iso(options.date_max)),
    )
