"""Session, CSRF, question-id and throttle tokens derived from the cipher key with HKDF/HMAC."""

import base64
import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from bank_transactions.domain.enums import IssuerCode, LoginKeyKind
from bank_transactions.ports.token_service import TokenService

_SESSION_TOKEN_BYTES = 32
_QUESTION_ID_PREFIX = "iq_"
_QUESTION_ID_LENGTH = 26  # 128 bits of base32 without padding


class HmacTokenService(TokenService):
    """Each purpose gets its own HKDF-SHA256 subkey, so a value derived for one purpose can never
    be confused with another. The cipher key itself is not kept."""

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or not key:
            raise ValueError("the token key must be non-empty bytes")
        self._csrf_key = _subkey(key, b"bt/csrf")
        self._question_key = _subkey(key, b"bt/iq")
        self._throttle_key = _subkey(key, b"bt/throttle")

    def new_session_token(self) -> str:
        return secrets.token_urlsafe(_SESSION_TOKEN_BYTES)

    def digest(self, token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    def csrf_for(self, token_digest: bytes) -> str:
        mac = hmac.digest(self._csrf_key, token_digest, "sha256")
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")

    def question_id(self, issuer: IssuerCode, hint_key: str) -> str:
        mac = hmac.digest(self._question_key, _message(issuer.value, hint_key), "sha256")
        encoded = base64.b32encode(mac).decode("ascii").lower()
        return _QUESTION_ID_PREFIX + encoded[:_QUESTION_ID_LENGTH]

    def throttle_key(self, kind: LoginKeyKind, value: str) -> bytes:
        return hmac.digest(self._throttle_key, _message(kind.value, value), "sha256")


def _subkey(key: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(key)


def _message(domain: str, value: str) -> bytes:
    return f"{domain}|{value}".encode()
