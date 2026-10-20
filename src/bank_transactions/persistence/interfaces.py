from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from types import TracebackType
from typing import Self

from bank_transactions.domain.ids import (
    AccountId,
    CardId,
    IdentifierToken,
    ImportRunId,
    InstallmentPlanId,
    OwnerId,
    TransactionId,
)
from bank_transactions.domain.keys import ComparisonKey, IssuerReferenceKey, OwnerMatchKey
from bank_transactions.persistence.commands import (
    ImportRunOutcome,
    NewAccount,
    NewCard,
    NewImportRun,
    NewInstallmentPlan,
    NewOwner,
    NewTransactionAggregate,
)
from bank_transactions.persistence.records import (
    AccountRecord,
    CardLookup,
    CardRecord,
    OwnerRecord,
)


class ImportRunRepository(ABC):
    """Audit data. Use it from its own committed UoW, never the domain UoW."""

    @abstractmethod
    def start_run(self, run: NewImportRun) -> ImportRunId:
        """Insert a RUNNING run with a NULL issuer; returns its id."""

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
    UoW (and the same file's other cards) see it."""

    @abstractmethod
    def list_all(self) -> tuple[OwnerRecord, ...]:
        """All owners ordered by id."""

    @abstractmethod
    def get(self, owner_id: OwnerId) -> OwnerRecord | None: ...

    @abstractmethod
    def find_by_match_key(self, match_key: OwnerMatchKey) -> OwnerRecord | None: ...

    @abstractmethod
    def create(self, owner: NewOwner) -> OwnerRecord: ...


class AccountRepository(ABC):
    @abstractmethod
    def list_all(self) -> tuple[AccountRecord, ...]:
        """All accounts ordered by id."""

    @abstractmethod
    def get(self, account_id: AccountId) -> AccountRecord | None: ...

    @abstractmethod
    def find_by_number(
        self, bank_code: str | None, account_number_enc: IdentifierToken
    ) -> tuple[AccountRecord, ...]:
        """Every account on the (bank_code, account_number_enc) ciphertext pair, ordered by id.
        Zero, one or several accounts may match (an unknown bank may reuse the same digits), so
        callers never take the first; they act only on exactly one consistent match."""

    @abstractmethod
    def create(self, account: NewAccount) -> AccountRecord: ...


class CardRepository(ABC):
    @abstractmethod
    def get(self, card_id: CardId) -> CardRecord | None: ...

    @abstractmethod
    def find_candidates(self, lookup: CardLookup) -> tuple[CardRecord, ...]:
        """Cards matching issuer + last4_enc (and account/owner when given), ordered by id.
        Last-4 is search evidence, so zero, one or several cards may match."""

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


class UnitOfWorkFactory(ABC):
    @abstractmethod
    def create(self) -> UnitOfWork:
        """A new UoW on its own session. The service opens independent ones for committed
        audit writes and for the identity + parse + persist work, which may share one so card
        ids flushed by the identity gate are visible to later steps; a dry run rolls it back."""
