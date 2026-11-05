"""The boundary between the API foundation and the application services (frozen by U0)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from bank_transactions.domain.ids import SessionId, UserId

__all__ = ["AuthContext", "Authenticator", "ReviewCounter"]


@dataclass(frozen=True)
class AuthContext:
    """Who is calling. `csrf_token` is derived from the session; whether the credential
    arrived by cookie (CSRF applies) is decided per request by the API layer."""

    user_id: UserId
    session_id: SessionId
    csrf_token: str
    user_display_name: str


class Authenticator(ABC):
    @abstractmethod
    def authenticate(self, token: str) -> AuthContext | None:
        """The context of the live session behind this token, or None."""


class ReviewCounter(ABC):
    @abstractmethod
    def count_visible_unknown_currency(self, user_id: UserId) -> int:
        """Transactions awaiting a currency that the user can see."""
