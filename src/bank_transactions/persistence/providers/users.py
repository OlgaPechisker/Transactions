"""User accounts."""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from bank_transactions.domain.ids import PublicId, UserId
from bank_transactions.persistence.commands import NewUser
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import UserRepository
from bank_transactions.persistence.orm.models import UserRow
from bank_transactions.persistence.public_ids import new_public_id
from bank_transactions.persistence.records import UserRecord


def _record(row: UserRow) -> UserRecord:
    return UserRecord(
        UserId(row.id), PublicId(row.public_id), row.email, row.display_name, row.password_hash
    )


class SqlUserRepository(UserRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def find_by_email_key(self, email_key: str) -> UserRecord | None:
        row = self._session.scalars(
            select(UserRow).where(UserRow.email_key == email_key)
        ).one_or_none()
        return None if row is None else _record(row)

    @translates_database_errors
    def get(self, user_id: UserId) -> UserRecord | None:
        row = self._session.get(UserRow, user_id)
        return None if row is None else _record(row)

    @translates_database_errors
    def lock(self, user_id: UserId) -> None:
        self._session.execute(select(UserRow.id).where(UserRow.id == user_id).with_for_update())

    @translates_database_errors
    def create(self, user: NewUser) -> UserRecord:
        row = UserRow(
            public_id=new_public_id("usr"),
            email=user.email,
            email_key=user.email_key,
            display_name=user.display_name,
            password_hash=user.password_hash,
        )
        self._session.add(row)
        self._session.flush()
        return _record(row)

    @translates_database_errors
    def update_password_hash(self, user_id: UserId, password_hash: str) -> None:
        self._session.execute(
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(password_hash=password_hash)
            .execution_options(synchronize_session=False)
        )
