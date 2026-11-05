from enum import StrEnum


class IssuerCode(StrEnum):
    ISRACARD = "ISRACARD"
    MAX = "MAX"
    CAL = "CAL"


class TransactionType(StrEnum):
    REGULAR = "REGULAR"
    INSTALLMENTS = "INSTALLMENTS"
    RECURRING = "RECURRING"
    IMMEDIATE_CHARGE = "IMMEDIATE_CHARGE"


class Channel(StrEnum):
    INTERNET = "INTERNET"
    PHONE = "PHONE"
    MOBILE = "MOBILE"
    UNKNOWN = "UNKNOWN"


class ReviewReason(StrEnum):
    UNKNOWN_CURRENCY = "UNKNOWN_CURRENCY"
    UNMATCHED_FOREIGN_FEE = "UNMATCHED_FOREIGN_FEE"
    AMBIGUOUS_INSTALLMENT = "AMBIGUOUS_INSTALLMENT"


class ImportStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    NEEDS_INPUT = "NEEDS_INPUT"
    FAILED = "FAILED"


class IdentifierType(StrEnum):
    """Associated-data label that binds ciphertext to the kind of identifier it protects."""

    CARD_LAST4 = "card-last4"
    ACCOUNT_NUMBER = "account-number"


class CategorySource(StrEnum):
    """Which rule produced a transaction's effective category, strongest first."""

    OVERRIDE = "OVERRIDE"
    RULE = "RULE"
    MAPPING = "MAPPING"
    BANK = "BANK"


class TripMembership(StrEnum):
    TAGGED = "TAGGED"
    AUTO = "AUTO"


class ValidationStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"


class LoginKeyKind(StrEnum):
    """What a login-throttle key digest was derived from."""

    EMAIL = "EMAIL"
    IP = "IP"


class IssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class RowIssueCode(StrEnum):
    PENDING_TRANSACTION = "PENDING_TRANSACTION"
    MALFORMED_ROW = "MALFORMED_ROW"
    INVALID_DATE = "INVALID_DATE"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    MISSING_REQUIRED_VALUE = "MISSING_REQUIRED_VALUE"
    MISSING_OR_ZERO_FX_ORIGINAL_AMOUNT = "MISSING_OR_ZERO_FX_ORIGINAL_AMOUNT"
    TOTALS_MISMATCH = "TOTALS_MISMATCH"
