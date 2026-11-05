"""Transaction list, detail and filter options (read side)."""

from dataclasses import dataclass, replace

from bank_transactions.domain.enums import ReviewReason
from bank_transactions.domain.errors import InvalidQueryError, NotFoundError
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import (
    Dimension,
    Page,
    PageRequest,
    SortSpec,
    TransactionFilter,
    TransactionInclude,
)
from bank_transactions.persistence.interfaces import Isolation, UnitOfWorkFactory
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    FilterOptionsRecord,
    GroupRecord,
    TransactionViewRecord,
    TripRecord,
    VisibleCardRecord,
)
from bank_transactions.services.interfaces import AuthContext

__all__ = [
    "TRANSACTION_SORT_FIELDS",
    "FilterOptionsView",
    "TransactionPage",
    "TransactionService",
]

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


# The repository orders by a dimension's group key for the sort field "group:<dimension>". Same
# value as GROUP_SORT_PREFIX in providers/transaction_query.py, which this layer must not import.
_GROUP_SORT_PREFIX = "group:"


@dataclass(frozen=True)
class TransactionPage:
    """`summary` and `groups` are present only when requested through `include`. `cards` and
    `trips` carry what the group labels of a card / trip grouping need; they are empty for other
    groupings."""

    page: Page[TransactionViewRecord]
    summary: BillingSummaryRecord | None = None
    groups: tuple[GroupRecord, ...] | None = None
    cards: tuple[VisibleCardRecord, ...] = ()
    trips: tuple[TripRecord, ...] = ()


@dataclass(frozen=True)
class FilterOptionsView:
    """`options` carries everything but the trips, which come from TripRepository.list; its
    `cards` come from VisibleCardRepository.list_visible."""

    options: FilterOptionsRecord
    trips: tuple[TripRecord, ...]


class TransactionService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list(
        self,
        context: AuthContext,
        filter: TransactionFilter,
        group_by: Dimension | None,
        include: frozenset[TransactionInclude],
        sort: SortSpec,
        page: PageRequest,
    ) -> TransactionPage:
        """Raises InvalidQueryError for inconsistent filters or unknown sort fields. The page,
        summary and groups come from one snapshot. Groups need `groupBy`; without it they are
        left out."""
        _validate_filter(filter)
        _validate_sort(sort)
        ordering = sort
        if group_by is not None:
            ordering = SortSpec(((_GROUP_SORT_PREFIX + group_by.value, False), *sort.fields))
        user_id = context.user_id
        with self._uow_factory.create(isolation=Isolation.REPEATABLE_READ) as uow:
            query = uow.transaction_query
            records = query.page(user_id, filter, ordering, page)
            summary = (
                query.summary(user_id, filter) if TransactionInclude.SUMMARY in include else None
            )
            groups = None
            cards: tuple[VisibleCardRecord, ...] = ()
            trips: tuple[TripRecord, ...] = ()
            if group_by is not None and TransactionInclude.GROUPS in include:
                groups = query.groups(user_id, filter, group_by)
                if group_by is Dimension.CARD:
                    cards = uow.visible_cards.list_visible(user_id)
                elif group_by is Dimension.TRIP:
                    trips = uow.trips.list(user_id)
        return TransactionPage(records, summary, groups, cards, trips)

    def get(self, context: AuthContext, transaction_id: PublicId) -> TransactionViewRecord:
        """Raises NotFoundError when the transaction is not visible."""
        with self._uow_factory.create() as uow:
            record = uow.transaction_query.get(context.user_id, transaction_id)
        if record is None:
            raise NotFoundError("the transaction does not exist")
        return record

    def filter_options(self, context: AuthContext) -> FilterOptionsView:
        with self._uow_factory.create(isolation=Isolation.REPEATABLE_READ) as uow:
            counts = uow.transaction_query.filter_options(context.user_id)
            cards = uow.visible_cards.list_visible(context.user_id)
            trips = uow.trips.list(context.user_id)
        return FilterOptionsView(replace(counts, cards=cards), trips)


def _validate_filter(filter: TransactionFilter) -> None:
    has_amount = filter.min_amount is not None or filter.max_amount is not None
    if has_amount and filter.billing_currency is None:
        raise InvalidQueryError(
            InvalidQueryError.INVALID_FILTER, "minAmount and maxAmount require billingCurrency"
        )
    if any(reason is not ReviewReason.UNKNOWN_CURRENCY for reason in filter.review_reasons):
        raise InvalidQueryError(
            InvalidQueryError.INVALID_FILTER, "reviewReason accepts UNKNOWN_CURRENCY only"
        )


def _validate_sort(sort: SortSpec) -> None:
    if any(field not in TRANSACTION_SORT_FIELDS for field, _ in sort.fields):
        raise InvalidQueryError(InvalidQueryError.INVALID_SORT, "sort has an unsupported field")
