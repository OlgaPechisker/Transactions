"""Failed sign-in attempts, keyed by an HMAC digest so no email or address is stored."""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from bank_transactions.domain.enums import LoginKeyKind
from bank_transactions.persistence.commands import NewLoginFailure
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import LoginThrottleRepository
from bank_transactions.persistence.orm.models import LoginFailureRow


class SqlLoginThrottleRepository(LoginThrottleRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def count_failures(self, kind: LoginKeyKind, key_digest: bytes, since: datetime) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(LoginFailureRow)
                .where(
                    LoginFailureRow.key_kind == kind,
                    LoginFailureRow.key_digest == key_digest,
                    LoginFailureRow.failed_at >= since,
                )
            )
            or 0
        )

    @translates_database_errors
    def record_failure(self, failure: NewLoginFailure) -> None:
        self._session.add(
            LoginFailureRow(
                key_kind=failure.kind, key_digest=failure.key_digest, failed_at=failure.occurred_at
            )
        )
        self._session.flush()

    @translates_database_errors
    def purge_before(self, before: datetime) -> int:
        result = self._session.execute(
            delete(LoginFailureRow)
            .where(LoginFailureRow.failed_at < before)
            .execution_options(synchronize_session=False)
        )
        return cast(CursorResult[Any], result).rowcount
