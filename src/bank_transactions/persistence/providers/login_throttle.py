"""Failed sign-in attempts. Stub: implemented by U4."""

from datetime import datetime

from sqlalchemy.orm import Session

from bank_transactions.domain.enums import LoginKeyKind
from bank_transactions.persistence.commands import NewLoginFailure
from bank_transactions.persistence.interfaces import LoginThrottleRepository


class SqlLoginThrottleRepository(LoginThrottleRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def count_failures(self, kind: LoginKeyKind, key_digest: bytes, since: datetime) -> int:
        raise NotImplementedError

    def record_failure(self, failure: NewLoginFailure) -> None:
        raise NotImplementedError

    def purge_before(self, before: datetime) -> int:
        raise NotImplementedError
