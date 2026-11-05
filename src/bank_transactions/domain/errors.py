"""Domain errors. Messages are sanitised: they never contain identifiers or file contents."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bank_transactions.domain.models import CardHintKey


class BankTransactionsError(Exception):
    """Base class for every error this package raises on purpose."""


class ParseError(BankTransactionsError):
    """A file-level structural failure: missing sheet, header or required column."""


class UnknownFormatError(BankTransactionsError):
    """No registered parser recognised the workbook."""

    code = "UNKNOWN_STATEMENT_FORMAT"


class IdentityUnresolvedError(BankTransactionsError):
    """One or more source cards cannot be bound to a database card without user input."""

    def __init__(self, keys: "list[CardHintKey] | tuple[CardHintKey, ...]") -> None:
        self.keys = tuple(keys)
        super().__init__(f"identity input required for {len(self.keys)} card(s)")


class InvalidIdentitySelectionError(BankTransactionsError):
    """A resolver answered with a selection the question did not allow."""


class PersistenceError(BankTransactionsError):
    """A storage failure translated from the database layer."""


class ApiError(BankTransactionsError):
    """An error the REST API reports to the caller. `code` is the stable machine-readable value
    in the Problem body; the message is sanitised text that is safe to return."""

    code = "API_ERROR"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(message)


class NotFoundError(ApiError):
    """Missing and inaccessible resources are indistinguishable."""

    code = "NOT_FOUND"


class ConflictError(ApiError):
    IMPORT_IN_PROGRESS = "IMPORT_IN_PROGRESS"
    DATA_ALREADY_OWNED = "DATA_ALREADY_OWNED"
    TRIP_DATE_OVERLAP = "TRIP_DATE_OVERLAP"
    NOT_AUTO_MEMBER = "NOT_AUTO_MEMBER"
    ALREADY_VALIDATED = "ALREADY_VALIDATED"
    BATCH_CHANGED = "BATCH_CHANGED"
    CATEGORY_EXISTS = "CATEGORY_EXISTS"

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message, code=code)


class ImportInProgressError(ConflictError):
    """The same file is already being imported."""

    def __init__(self, message: str = "") -> None:
        super().__init__(ConflictError.IMPORT_IN_PROGRESS, message)


class DataAlreadyOwnedError(ConflictError):
    """An issuer + last-four key is claimed by another user. Never names that user."""

    def __init__(self, message: str = "") -> None:
        super().__init__(ConflictError.DATA_ALREADY_OWNED, message)


@dataclass(frozen=True)
class FieldError:
    """One invalid request field. `field` uses the wire (camelCase) name."""

    field: str
    code: str
    message: str


class ValidationFailedError(ApiError):
    code = "VALIDATION_ERROR"

    def __init__(self, errors: Iterable[FieldError], message: str = "Validation failed") -> None:
        self.errors = tuple(errors)
        super().__init__(message)

    @classmethod
    def single(cls, field: str, code: str, message: str) -> "ValidationFailedError":
        return cls((FieldError(field, code, message),), message)


class InvalidQueryError(ApiError):
    INVALID_FILTER = "INVALID_FILTER"
    INVALID_SORT = "INVALID_SORT"

    def __init__(self, code: str, message: str = "") -> None:
        if code not in (self.INVALID_FILTER, self.INVALID_SORT):
            raise ValueError("an invalid query is a filter or a sort problem")
        super().__init__(message, code=code)


class AuthenticationError(ApiError):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"

    code = UNAUTHENTICATED


class CsrfError(ApiError):
    code = "CSRF_VALIDATION_FAILED"


class RateLimitedError(ApiError):
    code = "RATE_LIMITED"

    def __init__(self, retry_after_s: int, message: str = "") -> None:
        if retry_after_s < 0:
            raise ValueError("retry-after cannot be negative")
        self.retry_after_s = retry_after_s
        super().__init__(message)


class FileTooLargeError(ApiError):
    code = "FILE_TOO_LARGE"


class UnsupportedFileError(ApiError):
    code = "UNSUPPORTED_FILE_TYPE"


class UnreadableWorkbookError(ApiError):
    code = "UNREADABLE_WORKBOOK"
