"""Import DTOs, mirroring `docs/api-contract/openapi.yaml` (ImportResult, ImportSummary and the
JSON-encoded `identitySelections` form field)."""

from typing import Annotated, Literal

from pydantic import Field

from bank_transactions.api.schemas.common import ApiModel, CardRefDto, PageMeta


class UseExistingCardSelection(ApiModel):
    question_id: str
    action: Literal["USE_EXISTING_CARD"]
    card_id: str


class CreateCardSelection(ApiModel):
    question_id: str
    action: Literal["CREATE_CARD"]
    nickname: str | None = Field(default=None, max_length=120)


IdentitySelectionDto = Annotated[
    UseExistingCardSelection | CreateCardSelection, Field(discriminator="action")
]


class ReviewReasonCountDto(ApiModel):
    reason: Literal["UNKNOWN_CURRENCY"]
    count: int = Field(ge=0)


class ImportCountsDto(ApiModel):
    read: int
    imported: int
    duplicate: int
    skipped: int
    needs_review: int
    needs_review_by_reason: list[ReviewReasonCountDto]


class ImportIssueDto(ApiModel):
    sheet_position: int | None = Field(ge=1)
    row: int | None
    code: str
    severity: str
    message: str


class IdentityQuestionDto(ApiModel):
    id: str
    reason: str
    issuer: str
    last4: str | None = Field(pattern=r"^\d{4}$")
    candidates: list[CardRefDto]
    allowed_actions: list[str]


class ImportResultDto(ApiModel):
    id: str
    status: str
    dry_run: bool
    issuer: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: str
    counts: ImportCountsDto
    warnings: list[str]
    issues: list[ImportIssueDto]
    identity_questions: list[IdentityQuestionDto]


class ImportSummaryDto(ApiModel):
    id: str
    sha256: str
    started_at: str
    status: str
    rows_imported: int


class ImportPageDto(ApiModel):
    items: list[ImportSummaryDto]
    meta: PageMeta
