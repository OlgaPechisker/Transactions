from dataclasses import dataclass

from bank_transactions.domain.enums import IssueSeverity, RowIssueCode


@dataclass(frozen=True)
class SourceLocation:
    """Where a row sits in the workbook. Sheet names can carry account numbers, so only
    the one-based sheet position is recorded."""

    sheet_position: int
    row: int

    def __post_init__(self) -> None:
        if self.sheet_position < 1 or self.row < 1:
            raise ValueError("source locations are one-based")


@dataclass(frozen=True)
class RowIssue:
    """A recoverable problem. `message` must already be free of identifiers and cell data."""

    location: SourceLocation | None
    code: RowIssueCode
    severity: IssueSeverity
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("an issue needs a message")
