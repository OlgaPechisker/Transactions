"""Fakes for the import service: an in-memory database with real unit-of-work semantics (private
working copy, write-back on commit, discarded on rollback), a hash-based identifier protector and
scripted workbook/parser/resolver collaborators. Helpers are exposed through the `world` fixture
because importing from sibling test modules is not possible with importlib import mode."""

import copy
import hashlib
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.fakes import RepositorySlots

from bank_transactions.domain.enums import (
    IdentifierType,
    ImportStatus,
    IssuerCode,
    TransactionType,
)
from bank_transactions.domain.errors import (
    IdentityUnresolvedError,
    PersistenceError,
    UnknownFormatError,
)
from bank_transactions.domain.ids import (
    AccountId,
    CardId,
    IdentifierToken,
    ImportRunId,
    InstallmentPlanId,
    OwnerId,
    TransactionId,
)
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.keys import ComparisonKey, IssuerReferenceKey, OwnerMatchKey
from bank_transactions.domain.models import (
    AccountHint,
    CardHint,
    CardHintKey,
    OwnerHint,
    ParsedTransaction,
    StatementContext,
    StatementParseResult,
    StatementTotals,
)
from bank_transactions.domain.money import ILS, Money
from bank_transactions.domain.workbook import WorkbookView
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import IdentityQuestion, IdentitySelection
from bank_transactions.parsing.interfaces import StatementParser
from bank_transactions.persistence.commands import (
    ImportRunOutcome,
    NewAccount,
    NewCard,
    NewImportRun,
    NewInstallmentPlan,
    NewOwner,
    NewTransactionAggregate,
)
from bank_transactions.persistence.interfaces import (
    AccountRepository,
    CardRepository,
    ImportRunRepository,
    InstallmentPlanRepository,
    OwnerRepository,
    TransactionRepository,
    UnitOfWork,
    UnitOfWorkFactory,
)
from bank_transactions.persistence.records import (
    AccountRecord,
    CardLookup,
    CardRecord,
    OwnerRecord,
)
from bank_transactions.ports.identifier_protector import IdentifierProtector
from bank_transactions.ports.parser_selector import ParserSelector
from bank_transactions.ports.workbook_reader import WorkbookReader

CARD_KEY = CardHintKey(IssuerCode.MAX, "4396")
START = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def card_key() -> CardHintKey:
    return CARD_KEY


@pytest.fixture
def make_tx() -> Callable[..., ParsedTransaction]:
    counter = iter(range(2, 10_000))

    def build(**overrides: object) -> ParsedTransaction:
        values: dict[str, object] = {
            "card_key": CARD_KEY,
            "location": SourceLocation(1, next(counter)),
            "transaction_date": date(2026, 7, 3),
            "charge_date": date(2026, 8, 2),
            "billing": Money(Decimal("100.00"), ILS),
            "merchant_name_raw": "Super Pharm",
            "merchant_name_norm": "super pharm",
            "transaction_type": TransactionType.REGULAR,
            "original": Money(Decimal("100.00"), ILS),
        }
        return ParsedTransaction(**{**values, **overrides})  # type: ignore[arg-type]

    return build


class FakeProtector(IdentifierProtector):
    """Tokens are opaque hashes, so a plaintext identifier can never hide inside one."""

    def __init__(self) -> None:
        self._plain: dict[IdentifierToken, tuple[IdentifierType, str]] = {}

    def encrypt(self, kind: IdentifierType, value: str) -> IdentifierToken:
        digest = hashlib.sha256(f"{kind.value}|{value}".encode()).hexdigest()[:20]
        token = IdentifierToken(f"tok-{digest}".encode())
        self._plain[token] = (kind, value)
        return token

    def decrypt(self, kind: IdentifierType, token: IdentifierToken) -> str:
        stored_kind, value = self._plain[token]
        if stored_kind is not kind:
            raise ValueError("token of another identifier type")
        return value


@dataclass
class RunRow:
    new: NewImportRun
    status: ImportStatus = ImportStatus.RUNNING
    outcome: ImportRunOutcome | None = None
    error_summary: str | None = None


@dataclass
class AuditState:
    runs: dict[int, RunRow] = field(default_factory=dict)


@dataclass
class DomainState:
    owners: dict[int, OwnerRecord] = field(default_factory=dict)
    accounts: dict[int, AccountRecord] = field(default_factory=dict)
    cards: dict[int, CardRecord] = field(default_factory=dict)
    plans: dict[int, NewInstallmentPlan] = field(default_factory=dict)
    transactions: list[NewTransactionAggregate] = field(default_factory=list)


class FakeDatabase:
    def __init__(self) -> None:
        self.audit = AuditState()
        self.domain = DomainState()
        self.fail_domain_commit: Exception | None = None
        self.fail_insert: Exception | None = None
        self.fail_start_run: Exception | None = None
        self.finish_calls = 0
        self.uows_created = 0


def _next_id(rows: Mapping[int, object] | Sequence[object]) -> int:
    return len(rows) + 1


class FakeRuns(ImportRunRepository):
    def __init__(self, uow: "FakeUnitOfWork") -> None:
        self._uow = uow

    def start_run(self, run: NewImportRun) -> ImportRunId:
        if self._uow.db.fail_start_run is not None:
            raise self._uow.db.fail_start_run
        _require_aware(run.started_at)
        runs = self._uow.audit.runs
        run_id = _next_id(runs)
        runs[run_id] = RunRow(run)
        self._uow.touch("audit")
        return ImportRunId(run_id)

    def finish_run(self, run_id: ImportRunId, outcome: ImportRunOutcome) -> None:
        self._uow.db.finish_calls += 1
        _require_aware(outcome.finished_at)
        row = self._uow.audit.runs[run_id]
        if row.status is not ImportStatus.RUNNING:
            raise PersistenceError("the import run was already finished")
        row.status, row.outcome, row.error_summary = (
            outcome.status,
            outcome,
            outcome.error_summary,
        )
        self._uow.touch("audit")

    def sweep_stuck_runs(
        self, started_before: datetime, finished_at: datetime, error_summary: str
    ) -> int:
        stuck = [
            row
            for row in self._uow.audit.runs.values()
            if row.status is ImportStatus.RUNNING and row.new.started_at < started_before
        ]
        for row in stuck:
            row.status, row.error_summary = ImportStatus.FAILED, error_summary
        if stuck:
            self._uow.touch("audit")
        return len(stuck)


class FakeOwners(OwnerRepository):
    def __init__(self, uow: "FakeUnitOfWork") -> None:
        self._uow = uow

    def list_all(self) -> tuple[OwnerRecord, ...]:
        return tuple(self._uow.domain.owners.values())

    def get(self, owner_id: OwnerId) -> OwnerRecord | None:
        return self._uow.domain.owners.get(owner_id)

    def find_by_match_key(self, match_key: OwnerMatchKey) -> OwnerRecord | None:
        return next((o for o in self.list_all() if o.match_key == match_key), None)

    def create(self, owner: NewOwner) -> OwnerRecord:
        if self.find_by_match_key(owner.match_key) is not None:
            raise PersistenceError("a stored record already has the same unique values")
        owners = self._uow.domain.owners
        record = OwnerRecord(OwnerId(_next_id(owners)), owner.display_name, owner.match_key)
        owners[record.owner_id] = record
        self._uow.touch("domain")
        return record


class FakeAccounts(AccountRepository):
    def __init__(self, uow: "FakeUnitOfWork") -> None:
        self._uow = uow

    def list_all(self) -> tuple[AccountRecord, ...]:
        return tuple(self._uow.domain.accounts.values())

    def get(self, account_id: AccountId) -> AccountRecord | None:
        return self._uow.domain.accounts.get(account_id)

    def find_by_number(
        self, bank_code: str | None, account_number_enc: IdentifierToken
    ) -> tuple[AccountRecord, ...]:
        return tuple(
            a
            for a in self.list_all()
            if a.bank_code == bank_code and a.account_number_enc == account_number_enc
        )

    def create(self, account: NewAccount) -> AccountRecord:
        domain = self._uow.domain
        if account.owner_id not in domain.owners:
            raise PersistenceError("a referenced record is missing or belongs to a different owner")
        if (
            account.bank_code is not None
            and account.account_number_enc is not None
            and self.find_by_number(account.bank_code, account.account_number_enc)
        ):
            raise PersistenceError("a stored record already has the same unique values")
        record = AccountRecord(
            AccountId(_next_id(domain.accounts)),
            account.owner_id,
            account.bank_code,
            account.account_number_enc,
            account.display_name,
        )
        domain.accounts[record.account_id] = record
        self._uow.touch("domain")
        return record


class FakeCards(CardRepository):
    def __init__(self, uow: "FakeUnitOfWork") -> None:
        self._uow = uow

    def get(self, card_id: CardId) -> CardRecord | None:
        return self._uow.domain.cards.get(card_id)

    def find_candidates(self, lookup: CardLookup) -> tuple[CardRecord, ...]:
        return tuple(
            c
            for c in self._uow.domain.cards.values()
            if c.issuer_code is lookup.issuer_code
            and c.last4_enc == lookup.last4_enc
            and (lookup.account_id is None or c.account_id == lookup.account_id)
            and (lookup.owner_id is None or c.owner_id == lookup.owner_id)
        )

    def create(self, card: NewCard) -> CardRecord:
        domain = self._uow.domain
        account = domain.accounts.get(card.account_id)
        if account is None or account.owner_id != card.owner_id:
            raise PersistenceError("a referenced record is missing or belongs to a different owner")
        record = CardRecord(
            CardId(_next_id(domain.cards)),
            card.owner_id,
            card.account_id,
            card.issuer_code,
            card.last4_enc,
            card.display_name,
        )
        domain.cards[record.card_id] = record
        self._uow.touch("domain")
        return record


class FakePlans(InstallmentPlanRepository):
    def __init__(self, uow: "FakeUnitOfWork") -> None:
        self._uow = uow

    def upsert_plans(
        self, plans: Collection[NewInstallmentPlan]
    ) -> Mapping[NewInstallmentPlan, InstallmentPlanId]:
        stored = self._uow.domain.plans
        result: dict[NewInstallmentPlan, InstallmentPlanId] = {}
        for plan in plans:
            existing = next((i for i, p in stored.items() if p == plan), None)
            if existing is None:
                existing = _next_id(stored)
                stored[existing] = plan
                self._uow.touch("domain")
            result[plan] = InstallmentPlanId(existing)
        return result


class FakeTransactions(TransactionRepository):
    def __init__(self, uow: "FakeUnitOfWork") -> None:
        self._uow = uow

    def find_known_references(
        self, keys: Collection[IssuerReferenceKey]
    ) -> frozenset[IssuerReferenceKey]:
        return frozenset(keys) & self._stored_references()

    def count_matching_groups(self, keys: Collection[ComparisonKey]) -> Mapping[ComparisonKey, int]:
        counts: dict[ComparisonKey, int] = {}
        for aggregate in self._uow.domain.transactions:
            tx = aggregate.transaction
            if tx.issuer_reference is not None:
                continue
            key = ComparisonKey(
                tx.card_id,
                tx.transaction_date,
                tx.merchant_name_norm,
                tx.original_amount,
                tx.original_currency,
                tx.installment_number,
            )
            if key in keys:
                counts[key] = counts.get(key, 0) + 1
        return counts

    def insert_many(
        self, aggregates: Sequence[NewTransactionAggregate]
    ) -> tuple[TransactionId, ...]:
        if self._uow.db.fail_insert is not None:
            raise self._uow.db.fail_insert
        stored = self._stored_references()
        for aggregate in aggregates:
            key = _reference_key(aggregate)
            if key is not None and key in stored:
                raise PersistenceError("a stored record already has the same unique values")
            if key is not None:
                stored = stored | {key}
        transactions = self._uow.domain.transactions
        first = len(transactions) + 1
        transactions.extend(aggregates)
        self._uow.touch("domain")
        return tuple(TransactionId(first + offset) for offset in range(len(aggregates)))

    def _stored_references(self) -> frozenset[IssuerReferenceKey]:
        keys = (_reference_key(a) for a in self._uow.domain.transactions)
        return frozenset(key for key in keys if key is not None)


def _reference_key(aggregate: NewTransactionAggregate) -> IssuerReferenceKey | None:
    tx = aggregate.transaction
    if tx.issuer_reference is None:
        return None
    return IssuerReferenceKey(
        tx.card_id, tx.transaction_date, tx.issuer_reference, tx.installment_number
    )


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None:
        raise PersistenceError("timestamps must be timezone-aware")


class FakeUnitOfWork(RepositorySlots, UnitOfWork):
    """Works on a private copy of the committed state; commit writes back only what it touched,
    so an audit UoW and a domain UoW opened side by side never overwrite each other."""

    def __init__(self, db: FakeDatabase) -> None:
        self.db = db
        self.audit = copy.deepcopy(db.audit)
        self.domain = copy.deepcopy(db.domain)
        self._touched: set[str] = set()

    def touch(self, area: str) -> None:
        self._touched.add(area)

    @property
    def import_runs(self) -> ImportRunRepository:
        return FakeRuns(self)

    @property
    def owners(self) -> OwnerRepository:
        return FakeOwners(self)

    @property
    def accounts(self) -> AccountRepository:
        return FakeAccounts(self)

    @property
    def cards(self) -> CardRepository:
        return FakeCards(self)

    @property
    def installment_plans(self) -> InstallmentPlanRepository:
        return FakePlans(self)

    @property
    def transactions(self) -> TransactionRepository:
        return FakeTransactions(self)

    def commit(self) -> None:
        if "domain" in self._touched and self.db.fail_domain_commit is not None:
            raise self.db.fail_domain_commit
        if "audit" in self._touched:
            self.db.audit = self.audit
        if "domain" in self._touched:
            self.db.domain = self.domain

    def rollback(self) -> None:
        self._touched.clear()


class FakeUnitOfWorkFactory(UnitOfWorkFactory):
    def __init__(self, db: FakeDatabase) -> None:
        self._db = db

    def create(self) -> UnitOfWork:
        self._db.uows_created += 1
        return FakeUnitOfWork(self._db)


class FakeReader(WorkbookReader):
    def __init__(self) -> None:
        self.opened: list[Path] = []
        self.on_open: Callable[[], None] | None = None
        self.failure: BaseException | None = None

    def open(self, path: Path) -> WorkbookView:
        self.opened.append(path)
        if self.on_open is not None:
            self.on_open()
        if self.failure is not None:
            raise self.failure
        return WorkbookView(())

    def open_bytes(self, data: bytes) -> WorkbookView:
        return WorkbookView(())


class FakeParser(StatementParser):
    issuer_code = IssuerCode.MAX

    def __init__(self, context: StatementContext, result: StatementParseResult) -> None:
        self.context = context
        self.result = result
        self.context_failure: BaseException | None = None
        self.result_failure: BaseException | None = None
        self.on_read_transactions: Callable[[], None] | None = None
        self.transaction_reads = 0

    def sniff(self, wb: WorkbookView) -> float:
        return 1.0

    def read_context(self, wb: WorkbookView) -> StatementContext:
        if self.context_failure is not None:
            raise self.context_failure
        return self.context

    def read_transactions(self, wb: WorkbookView, ctx: StatementContext) -> StatementParseResult:
        self.transaction_reads += 1
        if self.on_read_transactions is not None:
            self.on_read_transactions()
        if self.result_failure is not None:
            raise self.result_failure
        return self.result


class FakeSelector(ParserSelector):
    def __init__(self, parser: FakeParser) -> None:
        self.parser = parser
        self.unknown = False

    def select(self, wb: WorkbookView) -> StatementParser:
        if self.unknown:
            raise UnknownFormatError("no parser recognised the workbook")
        return self.parser


class ScriptedResolver(IdentityResolver):
    """Answers every question through `decide`; the default refuses like a non-interactive run."""

    def __init__(self) -> None:
        self.questions: list[IdentityQuestion] = []
        self.decide: Callable[[IdentityQuestion], IdentitySelection] = self._refuse

    @staticmethod
    def _refuse(question: IdentityQuestion) -> IdentitySelection:
        raise IdentityUnresolvedError([question.hint.card.key])

    def complete(self, question: IdentityQuestion) -> IdentitySelection:
        self.questions.append(question)
        return self.decide(question)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.db = FakeDatabase()
        self.protector = FakeProtector()
        self.resolver = ScriptedResolver()
        self.reader = FakeReader()
        self.clock = Clock()
        self.parser = FakeParser(self.context(), self.result([]))
        self.selector = FakeSelector(self.parser)
        self.factory = FakeUnitOfWorkFactory(self.db)

    def service(self):
        from bank_transactions.services.import_service import StatementImportService

        return StatementImportService(
            self.reader,
            self.selector,
            self.protector,
            self.resolver,
            self.factory,
            clock=self.clock,
        )

    def statement_file(self, name: str = "statement.xlsx", content: bytes = b"bytes-1") -> Path:
        path = self.tmp_path / name
        path.write_bytes(content)
        return path

    def context(
        self,
        last4s: Sequence[str] = ("4396",),
        issuer: IssuerCode = IssuerCode.MAX,
        owner: str | None = None,
        account: AccountHint | None = None,
        declared: StatementTotals | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> StatementContext:
        cards = tuple(
            CardHint(CardHintKey(issuer, last4), (labels or {}).get(last4)) for last4 in last4s
        )
        return StatementContext(
            issuer,
            cards,
            OwnerHint(owner) if owner else None,
            account,
            declared_totals=declared or StatementTotals(),
        )

    def result(
        self,
        transactions: Sequence[ParsedTransaction],
        issues: Sequence[object] = (),
        rows_seen: int | None = None,
        calculated: StatementTotals | None = None,
        plans: Sequence[object] = (),
    ) -> StatementParseResult:
        return StatementParseResult(
            tuple(transactions),
            tuple(issues),  # type: ignore[arg-type]
            len(transactions) if rows_seen is None else rows_seen,
            calculated or StatementTotals(),
            tuple(plans),  # type: ignore[arg-type]
        )

    def statement(self, context: StatementContext, result: StatementParseResult) -> None:
        self.parser.context = context
        self.parser.result = result

    def token(self, kind: IdentifierType, value: str) -> IdentifierToken:
        return self.protector.encrypt(kind, value)

    def seed_card(
        self,
        last4: str = "4396",
        issuer: IssuerCode = IssuerCode.MAX,
        owner: str = "Dana Levi",
        account_number: str | None = "8917221038",
        bank_code: str | None = "LEUMI",
        same_account_as: CardRecord | None = None,
    ) -> CardRecord:
        """Committed identity data, created directly (as an earlier import would have)."""
        with self.factory.create() as uow:
            if same_account_as is not None:
                owner_id, account_id = same_account_as.owner_id, same_account_as.account_id
            else:
                key = OwnerMatchKey.from_display_name(owner)
                record = uow.owners.find_by_match_key(key) or uow.owners.create(
                    NewOwner(owner, key)
                )
                number = (
                    self.token(IdentifierType.ACCOUNT_NUMBER, account_number)
                    if account_number
                    else None
                )
                account = uow.accounts.create(NewAccount(record.owner_id, bank_code, number, None))
                owner_id, account_id = record.owner_id, account.account_id
            card = uow.cards.create(
                NewCard(
                    owner_id,
                    account_id,
                    issuer,
                    self.token(IdentifierType.CARD_LAST4, last4),
                    None,
                )
            )
            uow.commit()
        return card

    @property
    def runs(self) -> list[RunRow]:
        return list(self.db.audit.runs.values())

    @property
    def stored(self) -> list[NewTransactionAggregate]:
        return list(self.db.domain.transactions)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)
