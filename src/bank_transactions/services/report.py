from dataclasses import dataclass

from bank_transactions.domain.enums import ImportStatus, IssuerCode, IssueSeverity, RowIssueCode
from bank_transactions.domain.ids import ImportRunId
from bank_transactions.domain.immutability import freeze_field
from bank_transactions.domain.issues import RowIssue, SourceLocation

__all__ = [
    "ImportReport",
    "IssueSeverity",
    "RowIssue",
    "RowIssueCode",
    "SourceLocation",
]


@dataclass(frozen=True)
class ImportReport:
    """Outcome of one import attempt. Contains no plaintext identifiers: unresolved cards are
    sanitised labels (e.g. "MAX card 2 of 3"), warnings are safe sentences. For a dry run the
    counts describe what would have been imported."""

    run_id: ImportRunId
    status: ImportStatus
    issuer_code: IssuerCode | None
    rows_total: int
    rows_imported: int
    rows_duplicate: int
    rows_skipped: int
    rows_needing_review: int
    issues: tuple[RowIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    unresolved_cards: tuple[str, ...] = ()
    dry_run: bool = False

    def __post_init__(self) -> None:
        for name in ("issues", "warnings", "unresolved_cards"):
            freeze_field(self, name, tuple)
        if self.status is ImportStatus.RUNNING:
            raise ValueError("a report describes a finished attempt")
        counts = (
            self.rows_total,
            self.rows_imported,
            self.rows_duplicate,
            self.rows_skipped,
            self.rows_needing_review,
        )
        if any(count < 0 for count in counts):
            raise ValueError("row counts cannot be negative")

    @property
    def needs_input(self) -> bool:
        return self.status is ImportStatus.NEEDS_INPUT
