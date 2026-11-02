"""Transaction list, detail and filter options. Stub: implemented by U6."""

from dataclasses import dataclass

from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import (
    Dimension,
    Page,
    PageRequest,
    SortSpec,
    TransactionFilter,
    TransactionInclude,
)
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    FilterOptionsRecord,
    GroupRecord,
    TransactionViewRecord,
    TripRecord,
)
from bank_transactions.services.interfaces import AuthContext


@dataclass(frozen=True)
class TransactionPage:
    """`summary` and `groups` are present only when requested through `include`."""

    page: Page[TransactionViewRecord]
    summary: BillingSummaryRecord | None = None
    groups: tuple[GroupRecord, ...] | None = None


@dataclass(frozen=True)
class FilterOptionsView:
    """`options` carries everything but the trips, which come from TripRepository.list."""

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
        """Raises InvalidQueryError for inconsistent filters or unknown sort fields."""
        raise NotImplementedError

    def get(self, context: AuthContext, transaction_id: PublicId) -> TransactionViewRecord:
        """Raises NotFoundError when the transaction is not visible."""
        raise NotImplementedError

    def filter_options(self, context: AuthContext) -> FilterOptionsView:
        raise NotImplementedError
