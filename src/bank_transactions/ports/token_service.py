from abc import ABC, abstractmethod

from bank_transactions.domain.enums import IssuerCode, LoginKeyKind


class TokenService(ABC):
    """Session tokens, CSRF tokens, stateless question ids and login-throttle keys. Only
    digests are ever stored."""

    @abstractmethod
    def new_session_token(self) -> str: ...

    @abstractmethod
    def digest(self, token: str) -> bytes: ...

    @abstractmethod
    def csrf_for(self, token_digest: bytes) -> str: ...

    @abstractmethod
    def question_id(self, issuer: IssuerCode, hint_key: str) -> str: ...

    @abstractmethod
    def throttle_key(self, kind: LoginKeyKind, value: str) -> bytes: ...
