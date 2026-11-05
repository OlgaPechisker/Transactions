"""API sessions. Only the SHA-256 digest of a token is stored."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from bank_transactions.domain.ids import SessionId, UserId
from bank_transactions.persistence.commands import NewSession
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import SessionRepository
from bank_transactions.persistence.orm.models import SessionRow
from bank_transactions.persistence.records import SessionRecord


def _record(row: SessionRow) -> SessionRecord:
    return SessionRecord(
        SessionId(row.id),
        UserId(row.user_id),
        bytes(row.token_digest),
        row.expires_at,
        row.revoked_at,
    )


class SqlSessionRepository(SessionRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def create(self, session: NewSession) -> SessionRecord:
        row = SessionRow(
            user_id=session.user_id,
            token_digest=session.token_digest,
            created_at=session.created_at,
            expires_at=session.expires_at,
        )
        self._session.add(row)
        self._session.flush()
        return _record(row)

    @translates_database_errors
    def find_active_by_digest(self, token_digest: bytes, now: datetime) -> SessionRecord | None:
        row = self._session.scalars(
            select(SessionRow).where(
                SessionRow.token_digest == token_digest,
                SessionRow.revoked_at.is_(None),
                SessionRow.expires_at > now,
            )
        ).one_or_none()
        return None if row is None else _record(row)

    @translates_database_errors
    def revoke(self, session_id: SessionId, revoked_at: datetime) -> None:
        self._session.execute(
            update(SessionRow)
            .where(SessionRow.id == session_id, SessionRow.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
            .execution_options(synchronize_session=False)
        )
