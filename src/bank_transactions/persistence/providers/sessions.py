"""API sessions. Stub: implemented by U4."""

from datetime import datetime

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import SessionId
from bank_transactions.persistence.commands import NewSession
from bank_transactions.persistence.interfaces import SessionRepository
from bank_transactions.persistence.records import SessionRecord


class SqlSessionRepository(SessionRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, session: NewSession) -> SessionRecord:
        raise NotImplementedError

    def find_active_by_digest(self, token_digest: bytes, now: datetime) -> SessionRecord | None:
        raise NotImplementedError

    def revoke(self, session_id: SessionId, revoked_at: datetime) -> None:
        raise NotImplementedError
