"""Custom categories. Stub: implemented by U7."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import UserId
from bank_transactions.persistence.commands import NewCustomCategory
from bank_transactions.persistence.interfaces import CategoryRepository
from bank_transactions.persistence.records import CustomCategoryRecord


class SqlCategoryRepository(CategoryRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, user_id: UserId) -> tuple[CustomCategoryRecord, ...]:
        raise NotImplementedError

    def find_by_key(self, user_id: UserId, name_key: str) -> CustomCategoryRecord | None:
        raise NotImplementedError

    def create(self, category: NewCustomCategory) -> CustomCategoryRecord:
        raise NotImplementedError
