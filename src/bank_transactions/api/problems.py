"""One table from domain/service errors to RFC 9457 `application/problem+json` responses.

Problem messages come only from our own sanitised error text or from the fixed texts below;
they never echo request values, paths, numbers from the data or exception internals."""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from bank_transactions.api.schemas.common import ProblemDto, ProblemErrorDto
from bank_transactions.domain.errors import (
    ApiError,
    AuthenticationError,
    BankTransactionsError,
    ConflictError,
    CsrfError,
    FieldError,
    FileTooLargeError,
    InvalidQueryError,
    NotFoundError,
    RateLimitedError,
    UnknownFormatError,
    UnreadableWorkbookError,
    UnsupportedFileError,
    ValidationFailedError,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://api.example/problems/"
PROBLEM_SCHEMA = "Problem"
INVALID_REQUEST = "INVALID_REQUEST"

_STATUS_BY_TYPE: tuple[tuple[type[Exception], int], ...] = (
    (ValidationFailedError, 422),
    (InvalidQueryError, 400),
    (AuthenticationError, 401),
    (CsrfError, 403),
    (NotFoundError, 404),
    (ConflictError, 409),
    (FileTooLargeError, 413),
    (UnsupportedFileError, 415),
    (UnreadableWorkbookError, 422),
    (UnknownFormatError, 422),
    (RateLimitedError, 429),
)

_TITLES: Mapping[int, str] = {
    400: "Bad request",
    401: "Not authenticated",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    413: "Payload too large",
    415: "Unsupported media type",
    422: "Validation failed",
    429: "Too many requests",
    500: "Internal error",
    501: "Not implemented",
}

_DESCRIPTIONS: Mapping[int, str] = {
    400: "Malformed request or invalid query parameter",
    401: "Not authenticated",
    403: "Missing or invalid CSRF on a cookie-only mutation",
    404: "Not found or not visible to the caller",
    409: "Conflicting state",
    413: "File too large",
    415: "Not an .xlsx workbook",
    422: "Semantic validation failed",
    429: "Rate limited",
}

# pydantic error type -> (field error code, sanitised message)
_FIELD_ERRORS: Mapping[str, tuple[str, str]] = {
    "missing": ("REQUIRED", "A value is required."),
    "string_too_short": ("TOO_SHORT", "The value is too short."),
    "string_too_long": ("TOO_LONG", "The value is too long."),
    "too_short": ("TOO_SHORT", "There are too few values."),
    "too_long": ("TOO_LONG", "There are too many values."),
    "string_pattern_mismatch": ("INVALID_FORMAT", "The value has an invalid format."),
    "extra_forbidden": ("UNKNOWN_FIELD", "This field is not allowed."),
    "json_invalid": ("MALFORMED_JSON", "The request body is not valid JSON."),
    "enum": ("INVALID_VALUE", "The value is not one of the allowed values."),
    "literal_error": ("INVALID_VALUE", "The value is not one of the allowed values."),
    "greater_than": ("OUT_OF_RANGE", "The value is out of range."),
    "greater_than_equal": ("OUT_OF_RANGE", "The value is out of range."),
    "less_than": ("OUT_OF_RANGE", "The value is out of range."),
    "less_than_equal": ("OUT_OF_RANGE", "The value is out of range."),
}
_UNKNOWN_FIELD_ERROR = ("INVALID", "The value is invalid.")
_QUERY_SOURCES = frozenset({"query", "path"})

_logger = logging.getLogger(__name__)


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """The `responses=` argument that documents Problem responses on a route. 401 and 403 are
    documented automatically by `create_app`; list the other statuses the operation has."""
    return {
        status: {
            "description": _DESCRIPTIONS.get(status, "Error"),
            "content": {
                PROBLEM_MEDIA_TYPE: {"schema": {"$ref": f"#/components/schemas/{PROBLEM_SCHEMA}"}}
            },
        }
        for status in statuses
    }


def problem_response(
    status: int,
    code: str,
    detail: str | None = None,
    errors: Sequence[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
    instance: str | None = None,
) -> JSONResponse:
    body = ProblemDto(
        type=PROBLEM_TYPE_BASE + code.lower().replace("_", "-"),
        title=_TITLES.get(status, "Error"),
        status=status,
        code=code,
        detail=detail or None,
        instance=instance,
        errors=None
        if errors is None
        else [ProblemErrorDto(field=e.field, code=e.code, message=e.message) for e in errors],
    )
    return JSONResponse(
        body.model_dump(mode="json", by_alias=True, exclude_none=True),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=dict(headers or {}),
    )


def _status_for(exc: Exception) -> int | None:
    for error_type, status in _STATUS_BY_TYPE:
        if isinstance(exc, error_type):
            return status
    return None


def _handle_known(request: Request, exc: Exception) -> JSONResponse:
    status = _status_for(exc)
    if status is None:
        return _handle_failure(request, exc)
    code = getattr(exc, "code", ApiError.code)
    errors = exc.errors if isinstance(exc, ValidationFailedError) else None
    headers = None
    if isinstance(exc, RateLimitedError):
        headers = {"Retry-After": str(exc.retry_after_s)}
    elif isinstance(exc, AuthenticationError) and code == AuthenticationError.UNAUTHENTICATED:
        headers = {"WWW-Authenticate": "Bearer"}
    return problem_response(status, code, str(exc), errors, headers, request.url.path)


def _field_path(location: Sequence[Any]) -> str:
    path = ""
    for part in location:
        path += f"[{part}]" if isinstance(part, int) else f".{part}" if path else str(part)
    return path


def _field_error(error: Mapping[str, Any]) -> FieldError:
    kind = str(error["type"])
    code, message = _FIELD_ERRORS.get(kind, _UNKNOWN_FIELD_ERROR)
    field = "body" if kind == "json_invalid" else _field_path(error["loc"][1:]) or "body"
    return FieldError(field, code, message)


def _handle_request_validation(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    raw = exc.errors()
    sources = {str(error["loc"][0]) for error in raw}
    errors = tuple(_field_error(error) for error in raw)
    if sources <= {"body"}:
        return problem_response(
            422, ValidationFailedError.code, "Validation failed", errors, instance=request.url.path
        )
    if sources & _QUERY_SOURCES:
        sort_only = all(
            tuple(error["loc"][:2]) == ("query", "sort")
            for error in raw
            if error["loc"][0] in _QUERY_SOURCES
        )
        code = InvalidQueryError.INVALID_SORT if sort_only else InvalidQueryError.INVALID_FILTER
    else:
        code = INVALID_REQUEST
    return problem_response(
        400, code, "A request parameter is invalid.", errors, instance=request.url.path
    )


def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    codes = {404: NotFoundError.code, 405: "METHOD_NOT_ALLOWED"}
    return problem_response(
        exc.status_code,
        codes.get(exc.status_code, "HTTP_ERROR"),
        headers=exc.headers,
        instance=request.url.path,
    )


def _handle_not_implemented(request: Request, _: Exception) -> JSONResponse:
    return problem_response(501, "NOT_IMPLEMENTED", instance=request.url.path)


def _handle_failure(request: Request, exc: Exception) -> JSONResponse:
    _logger.error("Unexpected failure while serving a request", exc_info=exc)
    return problem_response(500, "INTERNAL_ERROR", instance=request.url.path)


def _handle_unexpected(request: Request, _: Exception) -> JSONResponse:
    # The server middleware re-raises this one after responding, so the server logs it.
    return problem_response(500, "INTERNAL_ERROR", instance=request.url.path)


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_known)
    app.add_exception_handler(UnknownFormatError, _handle_known)
    app.add_exception_handler(BankTransactionsError, _handle_failure)
    app.add_exception_handler(RequestValidationError, _handle_request_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(NotImplementedError, _handle_not_implemented)
    app.add_exception_handler(Exception, _handle_unexpected)
