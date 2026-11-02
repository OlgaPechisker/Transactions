"""Bank-category to custom-category mappings. Stub: implemented by U7."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import CustomCategoryId, UserId
from bank_transactions.persistence.interfaces import MappingRepository
from bank_transactions.persistence.records import MappingRecord


class SqlMappingRepository(MappingRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, user_id: UserId) -> tuple[MappingRecord, ...]:
        raise NotImplementedError

    def upsert(
        self, user_id: UserId, bank_category: str, category_id: CustomCategoryId
    ) -> MappingRecord:
        raise NotImplementedError

    def delete(self, user_id: UserId, bank_category: str) -> bool:
        raise NotImplementedError
