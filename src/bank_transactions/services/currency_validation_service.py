"""Currency validation queue and batches."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

from bank_transactions.domain.enums import ValidationStatus
from bank_transactions.domain.errors import (
    ConflictError,
    FieldError,
    InvalidQueryError,
    NotFoundError,
    ValidationFailedError,
)
from bank_transactions.domain.ids import PublicId, TransactionId, UserId
from bank_transactions.domain.queries import (
    Page,
    PageRequest,
    PendingValidationFilter,
    SortSpec,
)
from bank_transactions.persistence.commands import NewValidationBatch
from bank_transactions.persistence.interfaces import (
    CurrencyValidationRepository,
    Isolation,
    UnitOfWorkFactory,
)
from bank_transactions.persistence.records import (
    BatchRecord,
    CountryOptionRecord,
    ValidationItemRecord,
)
from bank_transactions.ports.clock import Clock
from bank_transactions.ports.currency_catalogue import CurrencyCatalogue, CurrencyEntry
from bank_transactions.services.interfaces import AuthContext

__all__ = [
    "VALIDATION_SORT_FIELDS",
    "BatchCommand",
    "BatchResult",
    "CurrencyValidationService",
    "ValidationItemsQuery",
    "ValidationOverview",
]

VALIDATION_SORT_FIELDS = (
    "transactionDate",
    "merchant",
    "country",
    "originalAmount",
    "billingAmount",
)


@dataclass(frozen=True)
class ValidationOverview:
    pending: int
    validated: int
    countries: tuple[CountryOptionRecord, ...]
    currencies: tuple[CurrencyEntry, ...]

    @property
    def total(self) -> int:
        return self.pending + self.validated

    @property
    def progress_percent(self) -> Decimal:
        """Validated share of the queue with one decimal; 0.0 for an empty queue."""
        if self.total == 0:
            return Decimal("0.0")
        return (Decimal(self.validated) * 100 / self.total).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )


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
        with self._uow_factory.create(isolation=Isolation.REPEATABLE_READ) as uow:
            repository = uow.currency_validation
            user_id = context.user_id
            pending = repository.count_pending(user_id)
            validated = repository.count_validated(user_id)
            countries = repository.pending_countries(user_id)
        return ValidationOverview(pending, validated, countries, self._catalogue.entries())

    def items(
        self, context: AuthContext, query: ValidationItemsQuery
    ) -> Page[ValidationItemRecord]:
        """Raises InvalidQueryError for an unknown sort field or a `from` after `to`."""
        if any(field not in VALIDATION_SORT_FIELDS for field, _ in query.sort.fields):
            raise InvalidQueryError(InvalidQueryError.INVALID_SORT, "sort has an unsupported field")
        if _range_is_reversed(query.filter):
            raise InvalidQueryError(InvalidQueryError.INVALID_FILTER, "from is after to")
        with self._uow_factory.create(isolation=Isolation.REPEATABLE_READ) as uow:
            return uow.currency_validation.page_items(
                context.user_id, query.status, query.filter, query.sort, query.page
            )

    def create_batch(self, context: AuthContext, command: BatchCommand) -> BatchResult:
        """Raises ConflictError(ALREADY_VALIDATED), NotFoundError or ValidationFailedError.
        Either every selected row is validated in one batch or nothing changes."""
        self._check_command(command)
        user_id = context.user_id
        now = self._clock.now()
        with self._uow_factory.create() as uow:
            query, repository = uow.transaction_query, uow.currency_validation
            if command.transaction_ids is not None:
                requested = tuple(dict.fromkeys(command.transaction_ids))
                locked = {ref.public_id: ref for ref in query.lock_visible(user_id, requested)}
                if len(locked) != len(requested):
                    raise NotFoundError("a transaction does not exist")
                targets = tuple(locked[public_id] for public_id in requested)
                self._require_pending(repository, user_id, [ref.transaction_id for ref in targets])
            else:
                selection = command.filter
                if selection is None:
                    raise ValueError("a batch command needs transaction ids or a filter")
                locked_rows = query.lock_pending_matching(user_id, selection)
                # The rows were matched before their locks were granted; keep those still pending.
                pending = repository.pending_among(user_id, [r.transaction_id for r in locked_rows])
                targets = tuple(ref for ref in locked_rows if ref.transaction_id in pending)
            batch = repository.create_batch(NewValidationBatch(user_id, command.currency, now))
            transaction_ids = [ref.transaction_id for ref in targets]
            if transaction_ids:
                repository.add_members(batch.batch_id, transaction_ids)
                repository.apply(user_id, batch.batch_id, transaction_ids, command.currency)
            uow.commit()
        return BatchResult(
            replace(batch, validated_count=len(targets)),
            tuple(ref.public_id for ref in targets),
        )

    def undo_batch(self, context: AuthContext, batch_id: PublicId) -> None:
        """Raises ConflictError(BATCH_CHANGED) when a member moved since."""
        with self._uow_factory.create() as uow:
            repository = uow.currency_validation
            batch = repository.lock_batch(context.user_id, batch_id)
            if batch is None:
                raise NotFoundError("the batch does not exist")
            if batch.undone_at is not None:
                return
            members = repository.members_state(batch.batch_id)
            if any(
                member.validation_batch_id != batch.batch_id
                or member.original_currency != batch.currency
                for member in members
            ):
                raise ConflictError(
                    ConflictError.BATCH_CHANGED, "a transaction of the batch changed since"
                )
            repository.undo(batch.batch_id, self._clock.now())
            uow.commit()

    def move_back(self, context: AuthContext, transaction_id: PublicId) -> None:
        """Returns a validated transaction to the queue; any other visible one is left as is."""
        with self._uow_factory.create() as uow:
            internal_id = uow.transaction_query.visible_id(context.user_id, transaction_id)
            if internal_id is None:
                raise NotFoundError("the transaction does not exist")
            if uow.currency_validation.lock_validation(internal_id):
                uow.currency_validation.reset(internal_id)
                uow.commit()

    def _check_command(self, command: BatchCommand) -> None:
        errors: list[FieldError] = []
        if command.currency not in self._catalogue.codes():
            errors.append(FieldError("currency", "INVALID_CURRENCY", "the currency is not valid"))
        if command.transaction_ids is None and command.filter is None:
            errors.append(
                FieldError("transactionIds", "TARGET_REQUIRED", "give transactionIds or a filter")
            )
        elif command.transaction_ids is not None and command.filter is not None:
            errors.append(
                FieldError("filter", "ONE_TARGET_ONLY", "give transactionIds or a filter, not both")
            )
        elif command.filter is not None and _range_is_reversed(command.filter):
            errors.append(FieldError("filter.to", "BEFORE_FROM_DATE", "to is before from"))
        if errors:
            raise ValidationFailedError(errors)

    @staticmethod
    def _require_pending(
        repository: CurrencyValidationRepository,
        user_id: UserId,
        transaction_ids: Sequence[TransactionId],
    ) -> None:
        """Called once the rows are locked, so the state read here cannot change before commit."""
        if repository.validated_among(user_id, transaction_ids):
            raise ConflictError(
                ConflictError.ALREADY_VALIDATED, "a transaction is already validated"
            )
        if len(repository.pending_among(user_id, transaction_ids)) != len(transaction_ids):
            raise ValidationFailedError.single(
                "transactionIds", "NOT_PENDING", "a transaction has no unknown currency"
            )


def _range_is_reversed(selection: PendingValidationFilter) -> bool:
    return (
        selection.date_from is not None
        and selection.date_to is not None
        and selection.date_from > selection.date_to
    )
