"""A user's custom categories."""

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from bank_transactions.domain.ids import CustomCategoryId, UserId
from bank_transactions.persistence.commands import NewCustomCategory
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import CategoryRepository
from bank_transactions.persistence.orm.models import CustomCategoryRow
from bank_transactions.persistence.records import CustomCategoryRecord

_COLUMNS = (CustomCategoryRow.id, CustomCategoryRow.name, CustomCategoryRow.name_key)


def _record(row: tuple[int, str, str]) -> CustomCategoryRecord:
    return CustomCategoryRecord(CustomCategoryId(row[0]), row[1], row[2])


class SqlCategoryRepository(CategoryRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def list(self, user_id: UserId) -> tuple[CustomCategoryRecord, ...]:
        rows = self._session.execute(
            select(*_COLUMNS)
            .where(CustomCategoryRow.user_id == user_id)
            .order_by(CustomCategoryRow.name_key, CustomCategoryRow.id)
        ).all()
        return tuple(_record((row[0], row[1], row[2])) for row in rows)

    @translates_database_errors
    def find_by_key(self, user_id: UserId, name_key: str) -> CustomCategoryRecord | None:
        row = self._session.execute(
            select(*_COLUMNS).where(
                CustomCategoryRow.user_id == user_id, CustomCategoryRow.name_key == name_key
            )
        ).one_or_none()
        return None if row is None else _record((row[0], row[1], row[2]))

    @translates_database_errors
    def create(self, category: NewCustomCategory) -> CustomCategoryRecord:
        """The unique (user, name key) violation is translated to ConflictError(CATEGORY_EXISTS)
        by `translates_database_errors`."""
        row = self._session.execute(
            insert(CustomCategoryRow)
            .values(user_id=category.user_id, name=category.name, name_key=category.name_key)
            .returning(*_COLUMNS)
        ).one()
        return _record((row[0], row[1], row[2]))
