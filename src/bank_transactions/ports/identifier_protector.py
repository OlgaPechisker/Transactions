from abc import ABC, abstractmethod

from bank_transactions.domain.enums import IdentifierType
from bank_transactions.domain.ids import IdentifierToken


class IdentifierProtector(ABC):
    """Deterministic, reversible protection. The identifier type is authenticated so a token
    for one type cannot be decrypted as another."""

    @abstractmethod
    def encrypt(self, kind: IdentifierType, value: str) -> IdentifierToken: ...

    @abstractmethod
    def decrypt(self, kind: IdentifierType, token: IdentifierToken) -> str: ...
