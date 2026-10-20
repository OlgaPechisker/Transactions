from abc import ABC, abstractmethod

from bank_transactions.domain.workbook import WorkbookView
from bank_transactions.parsing.interfaces import StatementParser


class ParserSelector(ABC):
    @abstractmethod
    def select(self, wb: WorkbookView) -> StatementParser:
        """Return the best parser or raise UnknownFormatError."""
