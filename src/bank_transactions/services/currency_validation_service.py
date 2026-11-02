"""Currency validation queue and batches. Stub: implemented by U10."""

from dataclasses import dataclass

from bank_transactions.domain.enums import ValidationStatus
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import (
    Page,
    PageRequest,
    PendingValidationFilter,
    SortSpec,
)
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import (
    BatchRecord,
    CountryOptionRecord,
    ValidationItemRecord,
)
from bank_transactions.ports.clock import Clock
from bank_transactions.ports.currency_catalogue import CurrencyCatalogue, CurrencyEntry
from bank_transactions.services.interfaces import AuthContext


@dataclass(frozen=True)
class ValidationOverview:
    pending: int
    validated: int
    countries: tuple[CountryOptionRecord, ...]
    currencies: tuple[CurrencyEntry, ...]

    @property
    def total(self) -> int:
        return self.pending + self.validated


@dataclass(frozen=True)
class ValidationItemsQuery:
    status: ValidationStatus
    filter: PendingValidationFilter
    sort: SortSpec
    page: PageRequest


@dataclass(frozen=True)
class BatchCommand:
    """Exactly one of `transaction_ids` and `filter` is set."""

    currency: str
    transaction_ids: tuple[PublicId, ...] | None = None
    filter: PendingValidationFilter | None = None


@dataclass(frozen=True)
class BatchResult:
    batch: BatchRecord
    transaction_ids: tuple[PublicId, ...]


class CurrencyValidationService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, catalogue: CurrencyCatalogue, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._catalogue = catalogue
        self._clock = clock

    def overview(self, context: AuthContext) -> ValidationOverview:
        raise NotImplementedError

    def items(
        self, context: AuthContext, query: ValidationItemsQuery
    ) -> Page[ValidationItemRecord]:
        raise NotImplementedError

    def create_batch(self, context: AuthContext, command: BatchCommand) -> BatchResult:
        """Raises ConflictError(ALREADY_VALIDATED), NotFoundError or ValidationFailedError."""
        raise NotImplementedError

    def undo_batch(self, context: AuthContext, batch_id: PublicId) -> None:
        """Raises ConflictError(BATCH_CHANGED) when a member moved since."""
        raise NotImplementedError

    def move_back(self, context: AuthContext, transaction_id: PublicId) -> None:
        raise NotImplementedError
