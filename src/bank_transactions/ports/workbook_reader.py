from abc import ABC, abstractmethod
from pathlib import Path

from bank_transactions.domain.workbook import WorkbookView


class WorkbookReader(ABC):
    @abstractmethod
    def open(self, path: Path) -> WorkbookView:
        """Read the whole workbook into an immutable view; raise ParseError if unreadable."""

    @abstractmethod
    def open_bytes(self, data: bytes) -> WorkbookView:
        """Same as `open` for an in-memory upload; raise ParseError if unreadable."""
