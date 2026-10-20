from bank_transactions.domain.enums import (
    Channel,
    IdentifierType,
    ImportStatus,
    IssuerCode,
    IssueSeverity,
    ReviewReason,
    RowIssueCode,
    TransactionType,
)


def values(enum_type):
    return {member.value for member in enum_type}


def test_database_enums_match_the_plan():
    assert values(TransactionType) == {"REGULAR", "INSTALLMENTS", "RECURRING", "IMMEDIATE_CHARGE"}
    assert values(Channel) == {"INTERNET", "PHONE", "MOBILE", "UNKNOWN"}
    assert values(ReviewReason) == {
        "UNKNOWN_CURRENCY",
        "UNMATCHED_FOREIGN_FEE",
        "AMBIGUOUS_INSTALLMENT",
    }
    assert values(ImportStatus) == {"RUNNING", "SUCCESS", "PARTIAL", "NEEDS_INPUT", "FAILED"}


def test_issuers_are_the_three_supported_cards():
    assert values(IssuerCode) == {"ISRACARD", "MAX", "CAL"}


def test_identifier_types_bind_ciphertext_to_its_domain():
    assert IdentifierType.CARD_LAST4 == "card-last4"
    assert IdentifierType.ACCOUNT_NUMBER == "account-number"


def test_row_issue_vocabulary_covers_the_documented_cases():
    assert {"PENDING_TRANSACTION", "MISSING_OR_ZERO_FX_ORIGINAL_AMOUNT", "TOTALS_MISMATCH"} <= (
        values(RowIssueCode)
    )
    assert values(IssueSeverity) == {"INFO", "WARNING", "ERROR"}
