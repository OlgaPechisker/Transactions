"""Bank-category to custom-category mappings."""

from sqlalchemy import Select, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from bank_transactions.domain.ids import CustomCategoryId, UserId
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import MappingRepository
from bank_transactions.persistence.orm.models import CategoryMappingRow, CustomCategoryRow
from bank_transactions.persistence.records import MappingRecord


def _mappings(user_id: UserId) -> Select[str, int, str]:
    return (
        select(
            CategoryMappingRow.bank_category_raw,
            CategoryMappingRow.custom_category_id,
            CustomCategoryRow.name,
        )
        .join(CustomCategoryRow, CustomCategoryRow.id == CategoryMappingRow.custom_category_id)
        .where(CategoryMappingRow.user_id == user_id)
    )


class SqlMappingRepository(MappingRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def list(self, user_id: UserId) -> tuple[MappingRecord, ...]:
        rows = self._session.execute(
            _mappings(user_id).order_by(CategoryMappingRow.bank_category_raw)
        ).all()
        return tuple(MappingRecord(row[0], CustomCategoryId(row[1]), row[2]) for row in rows)

    @translates_database_errors
    def upsert(
        self, user_id: UserId, bank_category: str, category_id: CustomCategoryId
    ) -> MappingRecord:
        statement = insert(CategoryMappingRow).values(
            user_id=user_id, bank_category_raw=bank_category, custom_category_id=category_id
        )
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=["user_id", "bank_category_raw"],
                set_={"custom_category_id": statement.excluded.custom_category_id},
            )
        )
        row = self._session.execute(
            _mappings(user_id).where(CategoryMappingRow.bank_category_raw == bank_category)
        ).one()
        return MappingRecord(row[0], CustomCategoryId(row[1]), row[2])

    @translates_database_errors
    def delete(self, user_id: UserId, bank_category: str) -> bool:
        deleted = self._session.execute(
            delete(CategoryMappingRow)
            .where(
                CategoryMappingRow.user_id == user_id,
                CategoryMappingRow.bank_category_raw == bank_category,
            )
            .returning(CategoryMappingRow.bank_category_raw)
        ).first()
        return deleted is not None
