"""One table from domain/service errors to RFC 9457 `application/problem+json` responses."""

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from bank_transactions.api.schemas.common import ProblemDto, ProblemErrorDto
from bank_transactions.domain.errors import (
    ApiError,
    AuthenticationError,
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
    409: "Conflict",
    413: "Payload too large",
    415: "Unsupported media type",
    422: "Validation failed",
    429: "Too many requests",
    500: "Internal error",
    501: "Not implemented",
}

_logger = logging.getLogger(__name__)


def problem_response(
    status: int,
    code: str,
    detail: str | None = None,
    errors: tuple[FieldError, ...] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ProblemDto(
        type=PROBLEM_TYPE_BASE + code.lower().replace("_", "-"),
        title=_TITLES.get(status, "Error"),
        status=status,
        code=code,
        detail=detail or None,
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


def _handle_known(_: Request, exc: Exception) -> JSONResponse:
    status = _status_for(exc)
    if status is None:
        return _handle_unexpected(_, exc)
    code = getattr(exc, "code", "API_ERROR")
    errors = exc.errors if isinstance(exc, ValidationFailedError) else None
    headers = None
    if isinstance(exc, RateLimitedError):
        headers = {"Retry-After": str(exc.retry_after_s)}
    return problem_response(status, code, str(exc), errors, headers)


def _handle_request_validation(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    in_body = any(error["loc"] and error["loc"][0] == "body" for error in exc.errors())
    errors = tuple(
        FieldError(
            ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
            "INVALID",
            str(error["msg"]),
        )
        for error in exc.errors()
    )
    if in_body:
        return problem_response(422, ValidationFailedError.code, "Validation failed", errors)
    return problem_response(400, "INVALID_REQUEST", "Invalid request parameters", errors)


def _handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    codes = {404: NotFoundError.code, 405: "METHOD_NOT_ALLOWED"}
    return problem_response(exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"))


def _handle_not_implemented(_: Request, __: Exception) -> JSONResponse:
    return problem_response(501, "NOT_IMPLEMENTED")


def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    _logger.error("Unhandled error while serving a request", exc_info=exc)
    return problem_response(500, "INTERNAL_ERROR")


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_known)
    app.add_exception_handler(UnknownFormatError, _handle_known)
    app.add_exception_handler(RequestValidationError, _handle_request_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(NotImplementedError, _handle_not_implemented)
    app.add_exception_handler(Exception, _handle_unexpected)
