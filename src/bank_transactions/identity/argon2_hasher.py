"""Argon2id password hashing. Stub: implemented by U3."""

from bank_transactions.ports.password_hasher import PasswordHasher


class Argon2PasswordHasher(PasswordHasher):
    def hash(self, password: str) -> str:
        raise NotImplementedError

    def verify(self, password_hash: str, password: str) -> bool:
        raise NotImplementedError

    def needs_rehash(self, password_hash: str) -> bool:
        raise NotImplementedError
