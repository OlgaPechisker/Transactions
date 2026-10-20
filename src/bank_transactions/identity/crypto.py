"""Deterministic AES-SIV protection of card and account identifiers."""

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

from bank_transactions.domain.enums import IdentifierType
from bank_transactions.domain.ids import IdentifierToken
from bank_transactions.ports.identifier_protector import IdentifierProtector

_KEY_LENGTHS = (32, 48, 64)


class IdentifierCipher(IdentifierProtector):
    """AES-SIV with the identifier type as associated data. The same identifier always yields
    the same token, so tokens can be compared directly in indexed lookups. Inputs are opaque,
    already-canonical identifiers; failures never echo key material or identifiers."""

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) not in _KEY_LENGTHS:
            raise ValueError("the cipher key must be 32, 48 or 64 bytes")
        self._siv = AESSIV(key)

    def encrypt(self, kind: IdentifierType, value: str) -> IdentifierToken:
        if not value:
            raise ValueError("an identifier cannot be empty")
        return IdentifierToken(self._siv.encrypt(value.encode("utf-8"), [_domain(kind)]))

    def decrypt(self, kind: IdentifierType, token: IdentifierToken) -> str:
        plaintext = self._open(kind, token)
        if plaintext is None:
            raise ValueError("the identifier token does not match this identifier type or key")
        return plaintext.decode("utf-8")

    def _open(self, kind: IdentifierType, token: IdentifierToken) -> bytes | None:
        try:
            return self._siv.decrypt(bytes(token), [_domain(kind)])
        except (InvalidTag, ValueError):
            return None


def _domain(kind: IdentifierType) -> bytes:
    return kind.value.encode("ascii")
