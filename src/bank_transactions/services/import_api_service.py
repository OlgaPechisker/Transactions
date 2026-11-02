"""Statement import for the REST API. Stub: implemented by U5."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from bank_transactions.domain.enums import (
    ImportStatus,
    IssuerCode,
    IssueSeverity,
    RowIssueCode,
)
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import Page, PageRequest
from bank_transactions.identity.models import IdentityIssue
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import ImportSummaryRecord
from bank_transactions.ports.identifier_protector import IdentifierProtector
from bank_transactions.ports.token_service import TokenService
from bank_transactions.ports.workbook_reader import WorkbookReader
from bank_transactions.services.card_service import CardView
from bank_transactions.services.interfaces import AuthContext


class SelectionAction(StrEnum):
    USE_EXISTING_CARD = "USE_EXISTING_CARD"
    CREATE_CARD = "CREATE_CARD"


@dataclass(frozen=True)
class StatementUpload:
    data: bytes


@dataclass(frozen=True)
class ApiSelection:
    """The caller's answer to one identity question."""

    question_id: str
    action: SelectionAction
    card_id: PublicId | None = None
    nickname: str | None = None


@dataclass(frozen=True)
class ImportIssueView:
    """`sheet_position` and `row` are None for file-level issues such as a totals mismatch."""

    sheet_position: int | None
    row: int | None
    code: RowIssueCode
    severity: IssueSeverity
    message: str


@dataclass(frozen=True)
class IdentityQuestionView:
    question_id: str
    reason: IdentityIssue
    issuer: IssuerCode
    last4: str | None
    candidates: tuple[CardView, ...]


@dataclass(frozen=True)
class ImportCountsView:
    read: int
    imported: int
    duplicate: int
    skipped: int
    needs_review: int


@dataclass(frozen=True)
class ImportOutcome:
    public_id: PublicId
    status: ImportStatus
    dry_run: bool
    issuer: IssuerCode
    sha256: str
    started_at: datetime
    counts: ImportCountsView
    warnings: tuple[str, ...]
    issues: tuple[ImportIssueView, ...]
    questions: tuple[IdentityQuestionView, ...]


class ImportApiService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        reader: WorkbookReader,
        protector: IdentifierProtector,
        tokens: TokenService,
    ) -> None:
        self._uow_factory = uow_factory
        self._reader = reader
        self._protector = protector
        self._tokens = tokens

    def run(
        self,
        context: AuthContext,
        upload: StatementUpload,
        dry_run: bool,
        selections: tuple[ApiSelection, ...],
    ) -> ImportOutcome:
        raise NotImplementedError

    def history(
        self, context: AuthContext, page: PageRequest, descending: bool
    ) -> Page[ImportSummaryRecord]:
        raise NotImplementedError
