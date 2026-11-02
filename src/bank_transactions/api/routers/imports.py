"""Statement import and import history."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, UploadFile

from bank_transactions.api.dependencies import (
    Composition,
    CsrfUser,
    CurrentUser,
    Paging,
    sort_param,
)
from bank_transactions.domain.queries import SortSpec
from bank_transactions.services.import_api_service import StatementUpload

router = APIRouter(tags=["Imports"])

IMPORT_SORT_FIELDS = ("startedAt",)


@router.post("/imports", operation_id="runImport", status_code=201)
def run_import(
    file: UploadFile,
    dry_run: Annotated[bool, Form(alias="dryRun")],
    user: CsrfUser,
    comp: Composition,
    identity_selections: Annotated[str | None, Form(alias="identitySelections")] = None,
) -> Any:
    comp.imports.run(user, StatementUpload(file.file.read()), dry_run, ())
    raise NotImplementedError


@router.get("/imports", operation_id="listImports")
def list_imports(
    user: CurrentUser,
    comp: Composition,
    page: Paging,
    sort: Annotated[SortSpec, Depends(sort_param(IMPORT_SORT_FIELDS, "-startedAt"))],
) -> Any:
    comp.imports.history(user, page, sort.fields[0][1])
    raise NotImplementedError
