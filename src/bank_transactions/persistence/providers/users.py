"""User accounts. Stub: implemented by U4."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import UserId
from bank_transactions.persistence.commands import NewUser
from bank_transactions.persistence.interfaces import UserRepository
from bank_transactions.persistence.records import UserRecord


class SqlUserRepository(UserRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_email_key(self, email_key: str) -> UserRecord | None:
        raise NotImplementedError

    def get(self, user_id: UserId) -> UserRecord | None:
        raise NotImplementedError

    def lock(self, user_id: UserId) -> None:
        raise NotImplementedError

    def create(self, user: NewUser) -> UserRecord:
        raise NotImplementedError

    def update_password_hash(self, user_id: UserId, password_hash: str) -> None:
        raise NotImplementedError
