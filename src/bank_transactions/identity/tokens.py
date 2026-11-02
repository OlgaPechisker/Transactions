"""HKDF/HMAC session, CSRF, question-id and throttle tokens. Stub: implemented by U3."""

from bank_transactions.domain.enums import IssuerCode, LoginKeyKind
from bank_transactions.ports.token_service import TokenService


class HmacTokenService(TokenService):
    def __init__(self, key: bytes) -> None:
        self._key = key

    def new_session_token(self) -> str:
        raise NotImplementedError

    def digest(self, token: str) -> bytes:
        raise NotImplementedError

    def csrf_for(self, token_digest: bytes) -> str:
        raise NotImplementedError

    def question_id(self, issuer: IssuerCode, hint_key: str) -> str:
        raise NotImplementedError

    def throttle_key(self, kind: LoginKeyKind, value: str) -> bytes:
        raise NotImplementedError
