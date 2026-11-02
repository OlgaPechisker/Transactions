"""Statement import and import history."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request, Response, UploadFile
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError
from starlette.responses import Response as StarletteResponse

from bank_transactions.api.dependencies import (
    Composition,
    CsrfUser,
    CurrentUser,
    Paging,
    single_sort_param,
)
from bank_transactions.api.problems import problem_responses
from bank_transactions.api.schemas.imports import (
    IdentityQuestionDto,
    IdentitySelectionDto,
    ImportCountsDto,
    ImportIssueDto,
    ImportPageDto,
    ImportResultDto,
    ImportSummaryDto,
    ReviewReasonCountDto,
)
from bank_transactions.api.schemas.mappers import (
    format_timestamp,
    to_card_ref_dto,
    to_page_meta,
)
from bank_transactions.domain.errors import FieldError, FileTooLargeError, ValidationFailedError
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import SortSpec
from bank_transactions.services.import_api_service import (
    MAX_UPLOAD_BYTES,
    ApiSelection,
    IdentityQuestionView,
    ImportCountsView,
    ImportIssueView,
    ImportOutcome,
    SelectionAction,
    StatementUpload,
)
from bank_transactions.services.read_models import ImportSummaryRecord

IMPORT_SORT_FIELDS = ("startedAt",)
_MULTIPART_OVERHEAD = 1024 * 1024
_SELECTIONS = TypeAdapter(list[IdentitySelectionDto])


class _BoundedUploadRoute(APIRoute):
    """Refuses an oversized body from its declared length, before the multipart form is buffered.
    The endpoint still checks the real size of the file."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, StarletteResponse]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> StarletteResponse:
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD:
                raise FileTooLargeError("The file is larger than 20 MiB.")
            return await handler(request)

        return bounded


router = APIRouter(tags=["Imports"], route_class=_BoundedUploadRoute)


def _selection_error(error: Any) -> FieldError:
    field = "identitySelections"
    for part in error["loc"]:
        field += f"[{part}]" if isinstance(part, int) else f".{part}"
    return FieldError(field, "INVALID", str(error["msg"]))


def _parse_selections(raw: str | None) -> tuple[ApiSelection, ...]:
    if raw is None or not raw.strip():
        return ()
    try:
        parsed = _SELECTIONS.validate_json(raw)
    except ValidationError as invalid:
        raise ValidationFailedError(_selection_error(e) for e in invalid.errors()) from None
    return tuple(
        ApiSelection(
            item.question_id,
            SelectionAction(item.action),
            PublicId(item.card_id) if item.action == "USE_EXISTING_CARD" else None,
            None if item.action == "USE_EXISTING_CARD" else item.nickname,
        )
        for item in parsed
    )


def _question_dto(question: IdentityQuestionView) -> IdentityQuestionDto:
    return IdentityQuestionDto(
        id=question.question_id,
        reason=question.reason,
        issuer=question.issuer,
        last4=question.last4,
        candidates=[to_card_ref_dto(card) for card in question.candidates],
        allowed_actions=[str(action) for action in question.allowed_actions],
    )


def _issue_dto(issue: ImportIssueView) -> ImportIssueDto:
    return ImportIssueDto(
        sheet_position=issue.sheet_position,
        row=issue.row,
        code=issue.code,
        severity=issue.severity,
        message=issue.message,
    )


def _counts_dto(counts: ImportCountsView) -> ImportCountsDto:
    return ImportCountsDto(
        read=counts.read,
        imported=counts.imported,
        duplicate=counts.duplicate,
        skipped=counts.skipped,
        needs_review=counts.needs_review,
        needs_review_by_reason=(
            [ReviewReasonCountDto(reason="UNKNOWN_CURRENCY", count=counts.needs_review)]
            if counts.needs_review
            else []
        ),
    )


def _result_dto(outcome: ImportOutcome) -> ImportResultDto:
    return ImportResultDto(
        id=outcome.public_id,
        status=outcome.status,
        dry_run=outcome.dry_run,
        issuer=outcome.issuer,
        sha256=outcome.sha256,
        started_at=format_timestamp(outcome.started_at),
        counts=_counts_dto(outcome.counts),
        warnings=list(outcome.warnings),
        issues=[_issue_dto(issue) for issue in outcome.issues],
        identity_questions=[_question_dto(question) for question in outcome.questions],
    )


def _summary_dto(record: ImportSummaryRecord) -> ImportSummaryDto:
    return ImportSummaryDto(
        id=record.public_id,
        sha256=record.file_sha256,
        started_at=format_timestamp(record.started_at),
        status=record.status,
        rows_imported=record.rows_imported,
    )


@router.post(
    "/imports",
    operation_id="runImport",
    status_code=201,
    responses=problem_responses(404, 409, 413, 415, 422),
)
def run_import(
    request: Request,
    response: Response,
    file: UploadFile,
    dry_run: Annotated[bool, Form(alias="dryRun")],
    user: CsrfUser,
    comp: Composition,
    identity_selections: Annotated[str | None, Form(alias="identitySelections")] = None,
) -> ImportResultDto:
    upload = StatementUpload(file.file.read(MAX_UPLOAD_BYTES + 1))
    selections = _parse_selections(identity_selections)
    outcome = comp.imports.run(user, upload, dry_run, selections)
    response.headers["Location"] = f"{request.url.path}/{outcome.public_id}"
    return _result_dto(outcome)


@router.get("/imports", operation_id="listImports", responses=problem_responses(400))
def list_imports(
    user: CurrentUser,
    comp: Composition,
    page: Paging,
    sort: Annotated[SortSpec, Depends(single_sort_param(IMPORT_SORT_FIELDS, "-startedAt"))],
) -> ImportPageDto:
    runs = comp.imports.history(user, page, sort.fields[0][1])
    return ImportPageDto(items=[_summary_dto(run) for run in runs.items], meta=to_page_meta(runs))
