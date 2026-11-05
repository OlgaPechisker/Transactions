from collections.abc import Mapping

from bank_transactions.domain.ids import SessionId, UserId
from bank_transactions.services.interfaces import AuthContext, Authenticator, ReviewCounter

DEFAULT_TOKEN = "test-token"


def make_auth_context(
    user_id: int = 1,
    session_id: int = 1,
    csrf_token: str = "test-csrf",
    user_display_name: str = "Test User",
) -> AuthContext:
    return AuthContext(UserId(user_id), SessionId(session_id), csrf_token, user_display_name)


class FakeAuthenticator(Authenticator):
    """Knows the sessions it was given; every other token is unauthenticated."""

    def __init__(self, sessions: Mapping[str, AuthContext] | None = None) -> None:
        self.sessions: dict[str, AuthContext] = (
            dict(sessions) if sessions is not None else {DEFAULT_TOKEN: make_auth_context()}
        )

    def authenticate(self, token: str) -> AuthContext | None:
        return self.sessions.get(token)


class FakeReviewCounter(ReviewCounter):
    def __init__(self, counts: Mapping[UserId, int] | None = None) -> None:
        self.counts: dict[UserId, int] = dict(counts or {})

    def count_visible_unknown_currency(self, user_id: UserId) -> int:
        return self.counts.get(user_id, 0)
