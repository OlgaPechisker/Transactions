from abc import ABC, abstractmethod
from typing import ClassVar

from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.models import StatementContext, StatementParseResult
from bank_transactions.domain.workbook import WorkbookView


class StatementParser(ABC):
    """Public parser contract (plan section 5.1). Parsers never see a repository, session or
    database id. Structural failures raise ParseError; row problems become RowIssues."""

    issuer_code: ClassVar[IssuerCode]

    @abstractmethod
    def sniff(self, wb: WorkbookView) -> float:
        """Confidence 0.0-1.0 that this parser understands the workbook."""

    @abstractmethod
    def read_context(self, wb: WorkbookView) -> StatementContext:
        """Cheap banner/sheet-name read: no transaction rows (Max may scan only the identity
        column to discover cards)."""

    @abstractmethod
    def read_transactions(self, wb: WorkbookView, ctx: StatementContext) -> StatementParseResult:
        """Finalized charged transactions, row issues, source-row count and calculated totals.
        Every transaction's card_key is one of ctx.cards."""
