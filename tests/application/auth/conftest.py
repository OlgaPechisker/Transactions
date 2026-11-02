"""In-memory repositories and a wired `world` for the auth, profile and card services. Test
modules reach everything through the `world` / `make_world` fixtures, since sibling test modules
cannot be imported under importlib import mode."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

import pytest
from tests.fakes import (
    FakeClock,
    FakeHasher,
    FakeReviewCounter,
    FakeTokenService,
    InMemoryUnitOfWorkFactory,
)

from bank_transactions.domain.enums import IdentifierType, IssuerCode, LoginKeyKind
from bank_transactions.domain.ids import (
    CardId,
    PublicId,
    SessionId,
    UserId,
)
from bank_transactions.identity.crypto import IdentifierCipher
from bank_transactions.persistence.commands import NewLoginFailure, NewSession, NewUser
from bank_transactions.persistence.interfaces import (
    LoginThrottleRepository,
    SessionRepository,
    UserRepository,
    VisibleCardRepository,
)
from bank_transactions.persistence.records import SessionRecord, UserRecord, VisibleCardRecord
from bank_transactions.ports.identifier_protector import IdentifierProtector
from bank_transactions.ports.password_hasher import PasswordHasher
from bank_transactions.services.card_service import CardPresenter, CardService
from bank_transactions.services.profile_service import ProfileService
from bank_transactions.services.session_service import SessionAuthenticator, SessionService


class InMemoryUsers(UserRepository):
    def __init__(self) -> None:
        self.rows: dict[int, UserRecord] = {}
        self.keys: dict[str, int] = {}

    def find_by_email_key(self, email_key: str) -> UserRecord | None:
        user_id = self.keys.get(email_key)
        return None if user_id is None else self.rows[user_id]

    def get(self, user_id: UserId) -> UserRecord | None:
        return self.rows.get(user_id)

    def lock(self, user_id: UserId) -> None:
        return None

    def create(self, user: NewUser) -> UserRecord:
        user_id = len(self.rows) + 1
        record = UserRecord(
            UserId(user_id),
            PublicId(f"usr_{user_id}"),
            user.email,
            user.display_name,
            user.password_hash,
        )
        self.rows[user_id] = record
        self.keys[user.email_key] = user_id
        return record

    def update_password_hash(self, user_id: UserId, password_hash: str) -> None:
        self.rows[user_id] = replace(self.rows[user_id], password_hash=password_hash)


class InMemorySessions(SessionRepository):
    def __init__(self) -> None:
        self.rows: dict[int, SessionRecord] = {}

    def create(self, session: NewSession) -> SessionRecord:
        session_id = len(self.rows) + 1
        record = SessionRecord(
            SessionId(session_id), session.user_id, session.token_digest, session.expires_at, None
        )
        self.rows[session_id] = record
        return record

    def find_active_by_digest(self, token_digest: bytes, now: datetime) -> SessionRecord | None:
        for record in self.rows.values():
            if (
                record.token_digest == token_digest
                and record.revoked_at is None
                and record.expires_at > now
            ):
                return record
        return None

    def revoke(self, session_id: SessionId, revoked_at: datetime) -> None:
        record = self.rows[session_id]
        if record.revoked_at is None:
            self.rows[session_id] = replace(record, revoked_at=revoked_at)


class InMemoryThrottle(LoginThrottleRepository):
    def __init__(self) -> None:
        self.failures: list[NewLoginFailure] = []

    def count_failures(self, kind: LoginKeyKind, key_digest: bytes, since: datetime) -> int:
        return sum(
            1
            for f in self.failures
            if f.kind == kind and f.key_digest == key_digest and f.occurred_at >= since
        )

    def record_failure(self, failure: NewLoginFailure) -> None:
        self.failures.append(failure)

    def purge_before(self, before: datetime) -> int:
        kept = [f for f in self.failures if f.occurred_at >= before]
        removed = len(self.failures) - len(kept)
        self.failures = kept
        return removed


class InMemoryVisibleCards(VisibleCardRepository):
    def __init__(self, protector: IdentifierProtector) -> None:
        self._protector = protector
        self.cards: dict[int, list[VisibleCardRecord]] = {}

    def add(
        self, user_id: int, public_id: str, nickname: str | None = None, last4: str = "4580"
    ) -> None:
        cards = self.cards.setdefault(user_id, [])
        cards.append(
            VisibleCardRecord(
                CardId(len(cards) + 1),
                PublicId(public_id),
                IssuerCode.ISRACARD,
                self._protector.encrypt(IdentifierType.CARD_LAST4, last4),
                nickname,
                None,
            )
        )

    def list_visible(self, user_id: UserId) -> tuple[VisibleCardRecord, ...]:
        return tuple(self.cards.get(user_id, []))

    def get_visible(self, user_id: UserId, public_id: PublicId) -> VisibleCardRecord | None:
        return next((c for c in self.cards.get(user_id, []) if c.public_id == public_id), None)

    def set_display_name(
        self, user_id: UserId, public_id: PublicId, display_name: str | None
    ) -> VisibleCardRecord | None:
        cards = self.cards.get(user_id, [])
        for index, card in enumerate(cards):
            if card.public_id == public_id:
                cards[index] = replace(card, nickname=display_name)
                return cards[index]
        return None


@dataclass
class AuthWorld:
    users: InMemoryUsers
    sessions: InMemorySessions
    throttle: InMemoryThrottle
    visible_cards: InMemoryVisibleCards
    clock: FakeClock
    tokens: FakeTokenService
    review_counter: FakeReviewCounter
    hasher: PasswordHasher
    service: SessionService
    authenticator: SessionAuthenticator
    profile: ProfileService
    cards: CardService

    def add_user(
        self, email: str = "dana@example.com", name: str = "Dana Levi", password: str = "s3cret"
    ) -> UserRecord:
        key = email.strip().casefold()
        return self.users.create(NewUser(email, key, name, self.hasher.hash(password)))


def build_world(hasher: PasswordHasher | None = None) -> AuthWorld:
    hasher = hasher or FakeHasher()
    uow_factory = InMemoryUnitOfWorkFactory()
    users, sessions = InMemoryUsers(), InMemorySessions()
    protector = IdentifierCipher(bytes(range(64)))
    throttle, visible_cards = InMemoryThrottle(), InMemoryVisibleCards(protector)
    uow_factory.template.users = users
    uow_factory.template.sessions = sessions
    uow_factory.template.login_throttle = throttle
    uow_factory.template.visible_cards = visible_cards
    clock, tokens, counter = FakeClock(), FakeTokenService(), FakeReviewCounter()
    presenter = CardPresenter(protector)
    return AuthWorld(
        users=users,
        sessions=sessions,
        throttle=throttle,
        visible_cards=visible_cards,
        clock=clock,
        tokens=tokens,
        review_counter=counter,
        hasher=hasher,
        service=SessionService(uow_factory, hasher, tokens, clock),
        authenticator=SessionAuthenticator(uow_factory, tokens, clock),
        profile=ProfileService(uow_factory, counter),
        cards=CardService(uow_factory, presenter),
    )


@pytest.fixture
def make_world() -> Callable[[PasswordHasher | None], AuthWorld]:
    return build_world


@pytest.fixture
def world() -> AuthWorld:
    return build_world()
