from abc import ABC, abstractmethod
from pathlib import Path

from bank_transactions.domain.workbook import WorkbookView


class WorkbookReader(ABC):
    @abstractmethod
    def open(self, path: Path) -> WorkbookView:
        """Read the whole workbook into an immutable view; raise ParseError if unreadable."""
