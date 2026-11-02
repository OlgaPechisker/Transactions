"""Sign-in, sign-out and request authentication. Only digests of session tokens and of throttle
keys are stored; the plaintext token exists once, in the sign-in response."""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from bank_transactions.domain.enums import LoginKeyKind
from bank_transactions.domain.errors import (
    AuthenticationError,
    RateLimitedError,
    ValidationFailedError,
)
from bank_transactions.persistence.commands import NewLoginFailure, NewSession, NewUser
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import UserRecord
from bank_transactions.ports.clock import Clock
from bank_transactions.ports.password_hasher import PasswordHasher
from bank_transactions.ports.token_service import TokenService
from bank_transactions.services.interfaces import AuthContext, Authenticator

SESSION_LIFETIME = timedelta(hours=12)
THROTTLE_WINDOW = timedelta(minutes=15)
MAX_FAILED_SIGN_INS = 10

_THROTTLE_KINDS = (LoginKeyKind.EMAIL, LoginKeyKind.IP)


def normalize_email(email: str) -> str:
    """The key a sign-in looks a user up by: trimmed and case-insensitive."""
    return email.strip().casefold()


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
        # Verified against when the email is unknown, so both outcomes take the same time.
        self._dummy_hash = hasher.hash(secrets.token_urlsafe(32))

    def sign_in(self, email: str, password: str, client_ip: str) -> IssuedSession:
        """Raises AuthenticationError (INVALID_CREDENTIALS) or RateLimitedError. Each database
        step is its own unit of work, so a failure is recorded even though sign-in fails, and
        the slow hash check holds no connection."""
        email_key = normalize_email(email)
        keys = (
            self._tokens.throttle_key(LoginKeyKind.EMAIL, email_key),
            self._tokens.throttle_key(LoginKeyKind.IP, client_ip),
        )
        user = self._find_user_unless_throttled(email_key, keys)
        stored_hash = self._dummy_hash if user is None else user.password_hash
        verified = self._hasher.verify(stored_hash, password)
        if user is None or not verified:
            self._record_failure(keys)
            raise AuthenticationError(
                "The email or password is incorrect", code=AuthenticationError.INVALID_CREDENTIALS
            )
        return self._open_session(user, password)

    def sign_out(self, context: AuthContext) -> None:
        """Idempotent."""
        with self._uow_factory.create() as uow:
            uow.sessions.revoke(context.session_id, self._clock.now())
            uow.commit()

    def create_user(self, email: str, display_name: str, password: str) -> UserRecord:
        """Administrative provisioning (there is no registration endpoint). Raises
        ValidationFailedError for an unusable value or an email that is already taken."""
        email_key = normalize_email(email)
        if not 0 < email_key.find("@") < len(email_key) - 1:
            raise ValidationFailedError.single("email", "INVALID_FORMAT", "the email is invalid")
        if not display_name.strip():
            raise ValidationFailedError.single("name", "REQUIRED", "the name is required")
        if not password:
            raise ValidationFailedError.single("password", "REQUIRED", "the password is required")
        password_hash = self._hasher.hash(password)
        with self._uow_factory.create() as uow:
            if uow.users.find_by_email_key(email_key) is not None:
                raise ValidationFailedError.single(
                    "email", "ALREADY_EXISTS", "a user with that email already exists"
                )
            user = uow.users.create(
                NewUser(email.strip(), email_key, display_name.strip(), password_hash)
            )
            uow.commit()
        return user

    def _find_user_unless_throttled(
        self, email_key: str, keys: tuple[bytes, bytes]
    ) -> UserRecord | None:
        since = self._clock.now() - THROTTLE_WINDOW
        with self._uow_factory.create() as uow:
            for kind, digest in zip(_THROTTLE_KINDS, keys, strict=True):
                if uow.login_throttle.count_failures(kind, digest, since) >= MAX_FAILED_SIGN_INS:
                    raise RateLimitedError(
                        int(THROTTLE_WINDOW.total_seconds()),
                        "Too many sign-in attempts; try again later",
                    )
            return uow.users.find_by_email_key(email_key)

    def _record_failure(self, keys: tuple[bytes, bytes]) -> None:
        now = self._clock.now()
        with self._uow_factory.create() as uow:
            for kind, digest in zip(_THROTTLE_KINDS, keys, strict=True):
                uow.login_throttle.record_failure(NewLoginFailure(kind, digest, now))
            uow.login_throttle.purge_before(now - THROTTLE_WINDOW)
            uow.commit()

    def _open_session(self, user: UserRecord, password: str) -> IssuedSession:
        token = self._tokens.new_session_token()
        digest = self._tokens.digest(token)
        now = self._clock.now()
        expires_at = now + SESSION_LIFETIME
        with self._uow_factory.create() as uow:
            if self._hasher.needs_rehash(user.password_hash):
                uow.users.update_password_hash(user.user_id, self._hasher.hash(password))
            session = uow.sessions.create(NewSession(user.user_id, digest, now, expires_at))
            uow.commit()
        context = AuthContext(
            user.user_id, session.session_id, self._tokens.csrf_for(digest), user.display_name
        )
        return IssuedSession(token, expires_at, context)


class SessionAuthenticator(Authenticator):
    def __init__(self, uow_factory: UnitOfWorkFactory, tokens: TokenService, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._tokens = tokens
        self._clock = clock

    def authenticate(self, token: str) -> AuthContext | None:
        digest = self._tokens.digest(token)
        with self._uow_factory.create() as uow:
            session = uow.sessions.find_active_by_digest(digest, self._clock.now())
            user = None if session is None else uow.users.get(session.user_id)
        if session is None or user is None:
            return None
        return AuthContext(
            user.user_id, session.session_id, self._tokens.csrf_for(digest), user.display_name
        )
