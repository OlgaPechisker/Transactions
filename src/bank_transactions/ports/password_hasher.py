from abc import ABC, abstractmethod


class PasswordHasher(ABC):
    @abstractmethod
    def hash(self, password: str) -> str: ...

    @abstractmethod
    def verify(self, password_hash: str, password: str) -> bool: ...

    @abstractmethod
    def needs_rehash(self, password_hash: str) -> bool: ...
