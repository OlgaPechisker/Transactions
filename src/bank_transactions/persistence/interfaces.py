from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from enum import StrEnum
from types import TracebackType
from typing import Self

from bank_transactions.domain.enums import IssuerCode, LoginKeyKind, ValidationStatus
from bank_transactions.domain.ids import (
    AccountId,
    BatchId,
    CardId,
    CustomCategoryId,
    IdentifierToken,
    ImportRunId,
    InstallmentPlanId,
    OwnerId,
    PublicId,
    SessionId,
    TransactionId,
    TripId,
    UserId,
)
from bank_transactions.domain.keys import ComparisonKey, IssuerReferenceKey, OwnerMatchKey
from bank_transactions.domain.queries import (
    AnalysisQuery,
    Bucket,
    Dimension,
    Page,
    PageRequest,
    PendingValidationFilter,
    SortSpec,
    TransactionFilter,
)
from bank_transactions.persistence.commands import (
    ImportRunOutcome,
    NewAccount,
    NewCard,
    NewCustomCategory,
    NewImportRun,
    NewInstallmentPlan,
    NewLoginFailure,
    NewOwner,
    NewRule,
    NewSession,
    NewTransactionAggregate,
    NewTrip,
    NewUser,
    NewValidationBatch,
)
from bank_transactions.persistence.records import (
    AccountRecord,
    AnalysisGroupRecord,
    BatchMemberState,
    BatchRecord,
    BillingSummaryRecord,
    CardLookup,
    CardRecord,
    ClaimRecord,
    CountryOptionRecord,
    CustomCategoryRecord,
    FilterOptionsRecord,
    GroupRecord,
    ImportSummaryRecord,
    MappingRecord,
    MatrixCellRecord,
    NamedCountRecord,
    OwnerRecord,
    PlaceholderOwnerRecord,
    RuleRecord,
    SeriesPointRecord,
    SessionRecord,
    TransactionRef,
    TransactionViewRecord,
    TripBreakdownRecord,
    TripMembersRecord,
    TripRecord,
    TripSummaryRecord,
    UserRecord,
    ValidationItemRecord,
    VisibleCardRecord,
)


class ImportRunRepository(ABC):
    """Audit data. Use it from its own committed UoW, never the domain UoW."""

    @abstractmethod
    def start_run(self, run: NewImportRun) -> ImportRunId:
        """Insert a RUNNING run with a NULL issuer; returns its id."""

    def public_id(self, run_id: ImportRunId) -> PublicId:
        """The run's opaque public id (`ImportResult.id`, the `Location` header). Assigned by
        `start_run` once U1 adds the column; additive so existing callers and fakes keep working."""
        raise NotImplementedError

    @abstractmethod
    def finish_run(self, run_id: ImportRunId, outcome: ImportRunOutcome) -> None:
        """Set the terminal status, finish time, issuer (if known), counts and summary."""

    @abstractmethod
    def sweep_stuck_runs(
        self, started_before: datetime, finished_at: datetime, error_summary: str
    ) -> int:
        """Mark RUNNING runs with started_at < started_before as FAILED; returns how many."""


class OwnerRepository(ABC):
    """create() flushes and returns the record with its real id so later lookups in the same
    UoW (and the same file's other cards) see it.

    `user_id` on the lookups is the API scope. The CLI pipeline leaves it None; until U1
    implements the scoped SQL, a concrete repository rejects a non-None value."""

    @abstractmethod
    def list_all(self, user_id: UserId | None = None) -> tuple[OwnerRecord, ...]:
        """All owners ordered by id."""

    @abstractmethod
    def get(self, owner_id: OwnerId, user_id: UserId | None = None) -> OwnerRecord | None: ...

    @abstractmethod
    def find_by_match_key(
        self, match_key: OwnerMatchKey, user_id: UserId | None = None
    ) -> OwnerRecord | None: ...

    @abstractmethod
    def create(self, owner: NewOwner) -> OwnerRecord: ...

    def find_placeholder(self, user_id: UserId) -> PlaceholderOwnerRecord | None:
        """The user's nameless owner, if one exists. Implemented by U1."""
        raise NotImplementedError

    def create_placeholder(self, user_id: UserId) -> PlaceholderOwnerRecord:
        """Create the user's nameless owner. Implemented by U1."""
        raise NotImplementedError


class AccountRepository(ABC):
    @abstractmethod
    def list_all(self, user_id: UserId | None = None) -> tuple[AccountRecord, ...]:
        """All accounts ordered by id."""

    @abstractmethod
    def get(self, account_id: AccountId, user_id: UserId | None = None) -> AccountRecord | None: ...

    @abstractmethod
    def find_by_number(
        self,
        bank_code: str | None,
        account_number_enc: IdentifierToken,
        user_id: UserId | None = None,
    ) -> tuple[AccountRecord, ...]:
        """Every account on the (bank_code, account_number_enc) ciphertext pair, ordered by id.
        Zero, one or several accounts may match (an unknown bank may reuse the same digits), so
        callers never take the first; they act only on exactly one consistent match."""

    @abstractmethod
    def create(self, account: NewAccount) -> AccountRecord: ...

    def find_placeholder(self, owner_id: OwnerId) -> AccountRecord | None:
        """The nameless account of a placeholder owner. Implemented by U1."""
        raise NotImplementedError

    def create_placeholder(self, owner_id: OwnerId) -> AccountRecord:
        """Create the nameless account of a placeholder owner. Implemented by U1."""
        raise NotImplementedError


class CardRepository(ABC):
    @abstractmethod
    def get(self, card_id: CardId, user_id: UserId | None = None) -> CardRecord | None: ...

    @abstractmethod
    def find_candidates(self, lookup: CardLookup) -> tuple[CardRecord, ...]:
        """Cards matching issuer + last4_enc (and account/owner/user when given), ordered by
        id. Last-4 is search evidence, so zero, one or several cards may match."""

    @abstractmethod
    def create(self, card: NewCard) -> CardRecord: ...


class InstallmentPlanRepository(ABC):
    @abstractmethod
    def upsert_plans(
        self, plans: Collection[NewInstallmentPlan]
    ) -> Mapping[NewInstallmentPlan, InstallmentPlanId]:
        """Insert missing plans, never update existing ones, and return the id of every
        requested plan (equal commands share one id). An existing plan matched by one
        natural key but contradicting the other is a PersistenceError."""


class TransactionRepository(ABC):
    @abstractmethod
    def find_known_references(
        self, keys: Collection[IssuerReferenceKey]
    ) -> frozenset[IssuerReferenceKey]:
        """The subset of keys already stored (NULL-safe on installment_number)."""

    @abstractmethod
    def count_matching_groups(self, keys: Collection[ComparisonKey]) -> Mapping[ComparisonKey, int]:
        """Stored reference-less rows per key (NULL-safe); absent groups may be omitted or 0."""

    @abstractmethod
    def insert_many(
        self, aggregates: Sequence[NewTransactionAggregate]
    ) -> tuple[TransactionId, ...]:
        """Insert transactions with their review reasons and FX details, in input order."""


class UserRepository(ABC):
    @abstractmethod
    def find_by_email_key(self, email_key: str) -> UserRecord | None: ...

    @abstractmethod
    def get(self, user_id: UserId) -> UserRecord | None: ...

    @abstractmethod
    def lock(self, user_id: UserId) -> None:
        """`SELECT ... FOR UPDATE` on the user row; serialises one user's writers."""

    @abstractmethod
    def create(self, user: NewUser) -> UserRecord: ...

    @abstractmethod
    def update_password_hash(self, user_id: UserId, password_hash: str) -> None: ...


class SessionRepository(ABC):
    @abstractmethod
    def create(self, session: NewSession) -> SessionRecord: ...

    @abstractmethod
    def find_active_by_digest(self, token_digest: bytes, now: datetime) -> SessionRecord | None:
        """The session with this digest that is neither revoked nor expired at `now`."""

    @abstractmethod
    def revoke(self, session_id: SessionId, revoked_at: datetime) -> None:
        """Idempotent: an already revoked session keeps its first revocation time."""


class LoginThrottleRepository(ABC):
    @abstractmethod
    def count_failures(self, kind: LoginKeyKind, key_digest: bytes, since: datetime) -> int: ...

    @abstractmethod
    def record_failure(self, failure: NewLoginFailure) -> None: ...

    @abstractmethod
    def purge_before(self, before: datetime) -> int:
        """Delete failures older than `before`; returns how many."""


class ClaimRepository(ABC):
    @abstractmethod
    def find(self, issuer_code: IssuerCode, last4_enc: IdentifierToken) -> ClaimRecord | None: ...

    @abstractmethod
    def create_or_get(
        self, user_id: UserId, issuer_code: IssuerCode, last4_enc: IdentifierToken
    ) -> ClaimRecord:
        """Claim the key for the user or return the user's existing claim.

        Raises DataAlreadyOwnedError when another user already holds the claim.
        """

    @abstractmethod
    def lock(self, issuer_code: IssuerCode, last4_enc: IdentifierToken) -> ClaimRecord | None:
        """`SELECT ... FOR UPDATE` on an existing claim."""


class VisibleCardRepository(ABC):
    """Cards a user can see through a claim."""

    @abstractmethod
    def list_visible(self, user_id: UserId) -> tuple[VisibleCardRecord, ...]: ...

    @abstractmethod
    def get_visible(self, user_id: UserId, public_id: PublicId) -> VisibleCardRecord | None: ...

    @abstractmethod
    def set_display_name(
        self, user_id: UserId, public_id: PublicId, display_name: str | None
    ) -> VisibleCardRecord | None:
        """One scoped UPDATE ... RETURNING; None when the card is not visible."""


class ImportHistoryRepository(ABC):
    @abstractmethod
    def page(
        self, user_id: UserId, page: PageRequest, descending: bool
    ) -> Page[ImportSummaryRecord]:
        """Committed SUCCESS/PARTIAL runs that contributed visible transactions."""


class TransactionQueryRepository(ABC):
    """Read side of the transaction list. `public_id` values are the API ids."""

    @abstractmethod
    def page(
        self, user_id: UserId, filter: TransactionFilter, sort: SortSpec, page: PageRequest
    ) -> Page[TransactionViewRecord]: ...

    @abstractmethod
    def summary(self, user_id: UserId, filter: TransactionFilter) -> BillingSummaryRecord: ...

    @abstractmethod
    def groups(
        self, user_id: UserId, filter: TransactionFilter, dimension: Dimension
    ) -> tuple[GroupRecord, ...]: ...

    @abstractmethod
    def get(self, user_id: UserId, public_id: PublicId) -> TransactionViewRecord | None: ...

    @abstractmethod
    def visible_id(self, user_id: UserId, public_id: PublicId) -> TransactionId | None: ...

    @abstractmethod
    def lock_visible(
        self, user_id: UserId, public_ids: Sequence[PublicId]
    ) -> tuple[TransactionRef, ...]:
        """`SELECT ... FOR UPDATE ORDER BY id`; invisible ids are simply absent."""

    @abstractmethod
    def lock_pending_matching(
        self, user_id: UserId, filter: PendingValidationFilter
    ) -> tuple[TransactionRef, ...]:
        """Lock the pending currency-validation rows matching the filter."""

    @abstractmethod
    def filter_options(self, user_id: UserId) -> FilterOptionsRecord: ...

    @abstractmethod
    def bank_category_counts(self, user_id: UserId) -> tuple[NamedCountRecord, ...]: ...

    @abstractmethod
    def has_bank_category(self, user_id: UserId, bank_category: str) -> bool: ...


class CategoryRepository(ABC):
    @abstractmethod
    def list(self, user_id: UserId) -> tuple[CustomCategoryRecord, ...]: ...

    @abstractmethod
    def find_by_key(self, user_id: UserId, name_key: str) -> CustomCategoryRecord | None: ...

    @abstractmethod
    def create(self, category: NewCustomCategory) -> CustomCategoryRecord:
        """A duplicate key raises ConflictError(CATEGORY_EXISTS)."""


class OverrideRepository(ABC):
    @abstractmethod
    def upsert(
        self, user_id: UserId, transaction_id: TransactionId, category_id: CustomCategoryId
    ) -> None: ...

    @abstractmethod
    def delete(self, user_id: UserId, transaction_id: TransactionId) -> None: ...


class MappingRepository(ABC):
    @abstractmethod
    def list(self, user_id: UserId) -> tuple[MappingRecord, ...]: ...

    @abstractmethod
    def upsert(
        self, user_id: UserId, bank_category: str, category_id: CustomCategoryId
    ) -> MappingRecord: ...

    @abstractmethod
    def delete(self, user_id: UserId, bank_category: str) -> bool:
        """True when a mapping existed."""


class RuleRepository(ABC):
    @abstractmethod
    def list_newest_first(self, user_id: UserId) -> tuple[RuleRecord, ...]: ...

    @abstractmethod
    def create(self, rule: NewRule) -> RuleRecord:
        """The rule and its terms in one unit of work."""

    @abstractmethod
    def delete(self, user_id: UserId, public_id: PublicId) -> bool:
        """True when the rule existed. Its terms cascade."""


class TripRepository(ABC):
    @abstractmethod
    def list_with_stats(self, user_id: UserId, sort: SortSpec) -> tuple[TripSummaryRecord, ...]: ...

    @abstractmethod
    def list(self, user_id: UserId) -> tuple[TripRecord, ...]: ...

    @abstractmethod
    def get(self, user_id: UserId, public_id: PublicId) -> TripRecord | None: ...

    @abstractmethod
    def get_id(self, user_id: UserId, public_id: PublicId) -> TripId | None: ...

    @abstractmethod
    def exists_overlap(self, user_id: UserId, start_date: date, end_date: date) -> bool:
        """Inclusive overlap: start <= other.end and end >= other.start."""

    @abstractmethod
    def create(self, trip: NewTrip) -> TripRecord: ...

    @abstractmethod
    def members(self, user_id: UserId, trip_id: TripId) -> TripMembersRecord: ...

    @abstractmethod
    def breakdowns(self, user_id: UserId, trip_id: TripId) -> TripBreakdownRecord: ...

    @abstractmethod
    def candidates(
        self,
        user_id: UserId,
        trip_id: TripId,
        window: tuple[date, date] | None,
        query: str | None,
        limit: int,
    ) -> tuple[TransactionViewRecord, ...]:
        """Visible non-members, inside `window` when no query is given."""

    @abstractmethod
    def is_auto_member(
        self, user_id: UserId, trip_id: TripId, transaction_id: TransactionId
    ) -> bool:
        """The same predicate as the automatic members of `members`."""


class TagRepository(ABC):
    @abstractmethod
    def upsert(self, transaction_id: TransactionId, trip_id: TripId) -> None: ...

    @abstractmethod
    def delete(self, transaction_id: TransactionId, trip_id: TripId) -> None:
        """Only removes a tag that points to this trip."""


class ExclusionRepository(ABC):
    @abstractmethod
    def insert_if_absent(self, trip_id: TripId, transaction_id: TransactionId) -> None: ...

    @abstractmethod
    def delete(self, trip_id: TripId, transaction_id: TransactionId) -> None: ...

    @abstractmethod
    def exists(self, trip_id: TripId, transaction_id: TransactionId) -> bool: ...


class AnalysisRepository(ABC):
    """Signed billing sums in `query.billing_currency` over `query.filter` and date range."""

    @abstractmethod
    def groups(
        self, user_id: UserId, query: AnalysisQuery, dimension: Dimension
    ) -> tuple[AnalysisGroupRecord, ...]: ...

    @abstractmethod
    def series(
        self,
        user_id: UserId,
        query: AnalysisQuery,
        bucket: Bucket,
        series_dimension: Dimension | None,
    ) -> tuple[SeriesPointRecord, ...]: ...

    @abstractmethod
    def matrix(
        self, user_id: UserId, query: AnalysisQuery, row_dimension: Dimension, bucket: Bucket
    ) -> tuple[MatrixCellRecord, ...]: ...


class CurrencyValidationRepository(ABC):
    @abstractmethod
    def count_pending(self, user_id: UserId) -> int: ...

    @abstractmethod
    def count_validated(self, user_id: UserId) -> int: ...

    @abstractmethod
    def pending_countries(self, user_id: UserId) -> tuple[CountryOptionRecord, ...]: ...

    @abstractmethod
    def page_items(
        self,
        user_id: UserId,
        status: ValidationStatus,
        filter: PendingValidationFilter,
        sort: SortSpec,
        page: PageRequest,
    ) -> Page[ValidationItemRecord]: ...

    @abstractmethod
    def validated_among(
        self, user_id: UserId, transaction_ids: Sequence[TransactionId]
    ) -> frozenset[TransactionId]: ...

    @abstractmethod
    def pending_among(
        self, user_id: UserId, transaction_ids: Sequence[TransactionId]
    ) -> frozenset[TransactionId]: ...

    @abstractmethod
    def create_batch(self, batch: NewValidationBatch) -> BatchRecord: ...

    @abstractmethod
    def add_members(self, batch_id: BatchId, transaction_ids: Sequence[TransactionId]) -> None: ...

    @abstractmethod
    def apply(
        self,
        user_id: UserId,
        batch_id: BatchId,
        transaction_ids: Sequence[TransactionId],
        currency: str,
    ) -> None:
        """Set the original currency, drop the UNKNOWN_CURRENCY reason and record the
        validations. `source_original_currency` is never touched."""

    @abstractmethod
    def lock_batch(self, user_id: UserId, public_id: PublicId) -> BatchRecord | None: ...

    @abstractmethod
    def members_state(self, batch_id: BatchId) -> tuple[BatchMemberState, ...]:
        """Locks the member transactions."""

    @abstractmethod
    def undo(self, batch_id: BatchId, undone_at: datetime) -> None: ...

    @abstractmethod
    def lock_validation(self, transaction_id: TransactionId) -> bool:
        """Lock the transaction; True when it currently has a validation."""

    @abstractmethod
    def reset(self, transaction_id: TransactionId) -> None:
        """Undo one validation: currency back to NULL, reason restored, validation deleted."""


class ReviewCounterRepository(ABC):
    @abstractmethod
    def count_visible_unknown_currency(self, user_id: UserId) -> int: ...


class UnitOfWork(ABC):
    """One database transaction. Leaving the context without commit() rolls back."""

    @property
    @abstractmethod
    def import_runs(self) -> ImportRunRepository: ...

    @property
    @abstractmethod
    def owners(self) -> OwnerRepository: ...

    @property
    @abstractmethod
    def accounts(self) -> AccountRepository: ...

    @property
    @abstractmethod
    def cards(self) -> CardRepository: ...

    @property
    @abstractmethod
    def installment_plans(self) -> InstallmentPlanRepository: ...

    @property
    @abstractmethod
    def transactions(self) -> TransactionRepository: ...

    @property
    @abstractmethod
    def users(self) -> UserRepository: ...

    @property
    @abstractmethod
    def sessions(self) -> SessionRepository: ...

    @property
    @abstractmethod
    def login_throttle(self) -> LoginThrottleRepository: ...

    @property
    @abstractmethod
    def claims(self) -> ClaimRepository: ...

    @property
    @abstractmethod
    def visible_cards(self) -> VisibleCardRepository: ...

    @property
    @abstractmethod
    def import_history(self) -> ImportHistoryRepository: ...

    @property
    @abstractmethod
    def transaction_query(self) -> TransactionQueryRepository: ...

    @property
    @abstractmethod
    def categories(self) -> CategoryRepository: ...

    @property
    @abstractmethod
    def overrides(self) -> OverrideRepository: ...

    @property
    @abstractmethod
    def mappings(self) -> MappingRepository: ...

    @property
    @abstractmethod
    def rules(self) -> RuleRepository: ...

    @property
    @abstractmethod
    def trips(self) -> TripRepository: ...

    @property
    @abstractmethod
    def tags(self) -> TagRepository: ...

    @property
    @abstractmethod
    def exclusions(self) -> ExclusionRepository: ...

    @property
    @abstractmethod
    def analysis(self) -> AnalysisRepository: ...

    @property
    @abstractmethod
    def currency_validation(self) -> CurrencyValidationRepository: ...

    @property
    @abstractmethod
    def review_counter(self) -> ReviewCounterRepository: ...

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None:
        """Undo uncommitted work; a no-op after commit()."""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.rollback()


class Isolation(StrEnum):
    READ_COMMITTED = "READ_COMMITTED"
    REPEATABLE_READ = "REPEATABLE_READ"


class UnitOfWorkFactory(ABC):
    @abstractmethod
    def create(self, isolation: Isolation = Isolation.READ_COMMITTED) -> UnitOfWork:
        """A new UoW on its own session. The service opens independent ones for committed
        audit writes and for the identity + parse + persist work, which may share one so card
        ids flushed by the identity gate are visible to later steps; a dry run rolls it back.
        REPEATABLE_READ gives a read-only request one consistent snapshot."""
