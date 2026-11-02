"""Sign-in, sign-out and request authentication. Stub: implemented by U4."""

from dataclasses import dataclass
from datetime import datetime

from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.ports.clock import Clock
from bank_transactions.ports.password_hasher import PasswordHasher
from bank_transactions.ports.token_service import TokenService
from bank_transactions.services.interfaces import AuthContext, Authenticator


@dataclass(frozen=True)
class IssuedSession:
    """`token` is the only time the plaintext session token exists outside the client."""

    token: str
    expires_at: datetime
    context: AuthContext


class SessionService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        hasher: PasswordHasher,
        tokens: TokenService,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = hasher
        self._tokens = tokens
        self._clock = clock

    def sign_in(self, email: str, password: str, client_ip: str) -> IssuedSession:
        """Raises AuthenticationError (INVALID_CREDENTIALS) or RateLimitedError."""
        raise NotImplementedError

    def sign_out(self, context: AuthContext) -> None:
        """Idempotent."""
        raise NotImplementedError


class SessionAuthenticator(Authenticator):
    def __init__(self, uow_factory: UnitOfWorkFactory, tokens: TokenService, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._tokens = tokens
        self._clock = clock

    def authenticate(self, token: str) -> AuthContext | None:
        raise NotImplementedError
