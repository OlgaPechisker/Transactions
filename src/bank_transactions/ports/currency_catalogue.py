from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class CurrencyEntry:
    code: str
    name: str


class CurrencyCatalogue(ABC):
    @abstractmethod
    def codes(self) -> tuple[str, ...]:
        """Every valid ISO 4217 alphabetic code except XXX and XTS, sorted."""

    @abstractmethod
    def entries(self) -> tuple[CurrencyEntry, ...]:
        """The same currencies with their English names, sorted by code."""
