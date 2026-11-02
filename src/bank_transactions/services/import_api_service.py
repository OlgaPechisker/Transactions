"""Statement import for the REST API: the caller's own import pipeline and history.

The pipeline is `StatementImportService.import_upload`; this service adds what is specific to a
signed-in caller: identity questions answered by API selections (question ids from the token
service), the caller's cards only, and the user-visible shape of the result."""

import io
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from bank_transactions.domain.enums import (
    ImportStatus,
    IssuerCode,
    IssueSeverity,
    RowIssueCode,
)
from bank_transactions.domain.errors import (
    FileTooLargeError,
    IdentityUnresolvedError,
    InvalidIdentitySelectionError,
    NotFoundError,
    UnsupportedFileError,
    ValidationFailedError,
)
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.models import StatementContext
from bank_transactions.domain.queries import Page, PageRequest
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import (
    CreateAccount,
    CreateCard,
    CreateOwner,
    IdentityIssue,
    IdentityQuestion,
    IdentitySelection,
    ResolvedCards,
    UseCard,
)
from bank_transactions.persistence.interfaces import UnitOfWork, UnitOfWorkFactory
from bank_transactions.persistence.records import ImportSummaryRecord, VisibleCardRecord
from bank_transactions.ports.clock import Clock
from bank_transactions.ports.identifier_protector import IdentifierProtector
from bank_transactions.ports.parser_selector import ParserSelector
from bank_transactions.ports.token_service import TokenService
from bank_transactions.ports.workbook_reader import WorkbookReader
from bank_transactions.services.card_service import CardPresenter, CardView
from bank_transactions.services.identity_gate import IdentityGate
from bank_transactions.services.import_service import StatementImportService, UploadImport
from bank_transactions.services.interfaces import AuthContext

DEFAULT_STALE_AFTER = timedelta(hours=1)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_CONTENT_TYPES_PART = "[Content_Types].xml"
_CONTENT_TYPES_LIMIT = 1024 * 1024
_WORKBOOK_CONTENT_TYPE = b"application/vnd.openxmlformats-officedocument.spreadsheetml"


class SelectionAction(StrEnum):
    USE_EXISTING_CARD = "USE_EXISTING_CARD"
    CREATE_CARD = "CREATE_CARD"


def _is_xlsx_container(data: bytes) -> bool:
    """A ZIP holding an OOXML workbook part; the workbook itself is read later."""
    try:
        with (
            zipfile.ZipFile(io.BytesIO(data)) as archive,
            archive.open(_CONTENT_TYPES_PART) as part,
        ):
            return _WORKBOOK_CONTENT_TYPE in part.read(_CONTENT_TYPES_LIMIT)
    except (
        zipfile.BadZipFile,
        zlib.error,
        EOFError,
        KeyError,
        RuntimeError,
        NotImplementedError,
        OSError,
    ):
        return False


@dataclass(frozen=True)
class StatementUpload:
    """Raises FileTooLargeError (413) or UnsupportedFileError (415) before anything else about
    the upload is looked at."""

    data: bytes

    def __post_init__(self) -> None:
        if len(self.data) > MAX_UPLOAD_BYTES:
            raise FileTooLargeError("The file is larger than 20 MiB.")
        if not _is_xlsx_container(self.data):
            raise UnsupportedFileError("Only .xlsx statement workbooks are supported.")


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

    @property
    def allowed_actions(self) -> tuple[SelectionAction, ...]:
        if self.candidates:
            return (SelectionAction.USE_EXISTING_CARD, SelectionAction.CREATE_CARD)
        return (SelectionAction.CREATE_CARD,)


@dataclass(frozen=True)
class ImportCountsView:
    """`needs_review` counts the user-visible review reason only (an unknown currency)."""

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


class _NoAnswers(IdentityResolver):
    """The pipeline's own resolver is never used by the API: every upload brings its own step."""

    def complete(self, question: IdentityQuestion) -> IdentitySelection:
        raise IdentityUnresolvedError([question.hint.card.key])


class _SelectionResolver(IdentityResolver):
    """Answers a question from the caller's selection for it; an unanswered question is recorded
    so the caller gets it back, and the gate reports the card as unresolved."""

    def __init__(
        self,
        question_ids: Mapping[tuple[IssuerCode, str], str],
        answers: Mapping[str, ApiSelection],
        visible: Mapping[PublicId, VisibleCardRecord],
    ) -> None:
        self._question_ids = question_ids
        self._answers = answers
        self._visible = visible
        self.unanswered: list[tuple[str, IdentityQuestion]] = []

    def complete(self, question: IdentityQuestion) -> IdentitySelection:
        key = question.hint.card.key
        question_id = self._question_ids[(key.issuer_code, key.last4)]
        answer = self._answers.get(question_id)
        if answer is None:
            self.unanswered.append((question_id, question))
            raise IdentityUnresolvedError([key])
        if answer.action is SelectionAction.USE_EXISTING_CARD:
            return UseCard(self._visible[_required_card(answer)].card_id)
        return _new_card(question, answer.nickname)


def _required_card(answer: ApiSelection) -> PublicId:
    if answer.card_id is None:
        raise ValidationFailedError.single("identitySelections", "REQUIRED", "A card id is needed.")
    return answer.card_id


def _new_card(question: IdentityQuestion, nickname: str | None) -> CreateCard:
    """Evidence first (the owner and account the file names), else the user's placeholder owner
    and the owner's placeholder account, which the gate reuses. Nothing is fabricated."""
    owner_hint = question.hint.owner
    owner = (
        CreateOwner(owner_hint.display_name)
        if question.determined_owner is None and owner_hint is not None
        else None
    )
    account = CreateAccount() if question.determined_account is None else None
    return CreateCard(owner, account, (nickname or "").strip() or None)


class _ApiIdentity:
    """The caller's identity step of one import: claims, selections, then the gate."""

    def __init__(
        self,
        context: AuthContext,
        protector: IdentifierProtector,
        tokens: TokenService,
        presenter: CardPresenter,
        selections: tuple[ApiSelection, ...],
    ) -> None:
        self._context = context
        self._protector = protector
        self._tokens = tokens
        self._presenter = presenter
        self._selections = selections
        self.questions: tuple[IdentityQuestionView, ...] = ()

    def resolve(self, statement: StatementContext, uow: UnitOfWork) -> ResolvedCards:
        user_id = self._context.user_id
        question_ids = {
            (card.key.issuer_code, card.key.last4): self._tokens.question_id(
                card.key.issuer_code, card.key.last4
            )
            for card in statement.cards
        }
        visible = uow.visible_cards.list_visible(user_id)
        by_public_id = {card.public_id: card for card in visible}
        resolver = _SelectionResolver(
            question_ids, {s.question_id: s for s in self._selections}, by_public_id
        )
        gate = IdentityGate(self._protector, resolver, placeholder_owners=True)
        gate.guard_claims(statement, uow, user_id)
        self._validate(set(question_ids.values()), by_public_id)
        try:
            return gate.resolve(statement, uow, user_id)
        except InvalidIdentitySelectionError as invalid:
            raise ValidationFailedError.single(
                "identitySelections", "INVALID_SELECTION", str(invalid)
            ) from None
        except IdentityUnresolvedError:
            self.questions = self._views(resolver.unanswered, visible)
            raise

    def _validate(
        self, question_ids: set[str], visible: Mapping[PublicId, VisibleCardRecord]
    ) -> None:
        """A card the caller cannot see is a 404; a question this file never asked is a 422."""
        for index, selection in enumerate(self._selections):
            if selection.action is SelectionAction.USE_EXISTING_CARD:
                if selection.card_id is None:
                    raise ValidationFailedError.single(
                        f"identitySelections[{index}].cardId", "REQUIRED", "A card id is needed."
                    )
                if selection.card_id not in visible:
                    raise NotFoundError("The card was not found.")
            if selection.question_id not in question_ids:
                raise ValidationFailedError.single(
                    f"identitySelections[{index}].questionId",
                    "UNKNOWN_QUESTION",
                    "The question does not belong to this file.",
                )

    def _views(
        self,
        unanswered: list[tuple[str, IdentityQuestion]],
        visible: tuple[VisibleCardRecord, ...],
    ) -> tuple[IdentityQuestionView, ...]:
        """Candidates are looked up among the caller's own cards, so no one else's card can
        appear in a question."""
        by_card_id = {card.card_id: card for card in visible}
        name = self._context.user_display_name
        return tuple(
            IdentityQuestionView(
                question_id,
                question.issue,
                question.hint.card.key.issuer_code,
                question.hint.card.key.last4,
                tuple(
                    self._presenter.present(by_card_id[candidate.card_id], name)
                    for candidate in question.candidates
                    if candidate.card_id in by_card_id
                ),
            )
            for question_id, question in unanswered
        )


class ImportApiService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        reader: WorkbookReader,
        selector: ParserSelector,
        protector: IdentifierProtector,
        tokens: TokenService,
        clock: Clock,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> None:
        """`selector` detects the issuer (no match → UnknownFormatError → 422); `clock` and
        `stale_after` feed the audit trail's start time and its stuck-run sweep."""
        self._uow_factory = uow_factory
        self._protector = protector
        self._tokens = tokens
        self._presenter = CardPresenter(protector)
        self._importer = StatementImportService(
            reader,
            selector,
            protector,
            _NoAnswers(),
            uow_factory,
            clock=clock.now,
            stale_after=stale_after,
        )

    def run(
        self,
        context: AuthContext,
        upload: StatementUpload,
        dry_run: bool,
        selections: tuple[ApiSelection, ...],
    ) -> ImportOutcome:
        _reject_repeated_questions(selections)
        identity = _ApiIdentity(context, self._protector, self._tokens, self._presenter, selections)
        result = self._importer.import_upload(
            upload.data, dry_run=dry_run, identity=identity.resolve
        )
        return _outcome(result, identity.questions)

    def history(
        self, context: AuthContext, page: PageRequest, descending: bool
    ) -> Page[ImportSummaryRecord]:
        with self._uow_factory.create() as uow:
            return uow.import_history.page(context.user_id, page, descending)


def _reject_repeated_questions(selections: tuple[ApiSelection, ...]) -> None:
    seen: set[str] = set()
    for index, selection in enumerate(selections):
        if selection.question_id in seen:
            raise ValidationFailedError.single(
                f"identitySelections[{index}].questionId",
                "DUPLICATE_QUESTION",
                "A question can be answered once.",
            )
        seen.add(selection.question_id)


def _outcome(result: UploadImport, questions: tuple[IdentityQuestionView, ...]) -> ImportOutcome:
    report = result.report
    if report.issuer_code is None:
        raise ValueError("a finished import names its issuer")
    return ImportOutcome(
        public_id=result.public_id,
        status=report.status,
        dry_run=report.dry_run,
        issuer=report.issuer_code,
        sha256=result.sha256,
        started_at=result.started_at,
        counts=ImportCountsView(
            read=report.rows_total,
            imported=report.rows_imported,
            duplicate=report.rows_duplicate,
            skipped=report.rows_skipped,
            needs_review=report.rows_pending_currency,
        ),
        warnings=report.warnings,
        issues=tuple(
            ImportIssueView(
                None if issue.location is None else issue.location.sheet_position,
                None if issue.location is None else issue.location.row,
                issue.code,
                issue.severity,
                issue.message,
            )
            for issue in report.issues
        ),
        questions=questions if report.needs_input else (),
    )
