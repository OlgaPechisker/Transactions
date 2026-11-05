"""Per-transaction category overrides."""

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from bank_transactions.domain.ids import CustomCategoryId, TransactionId, UserId
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import OverrideRepository
from bank_transactions.persistence.orm.models import CategoryOverrideRow


class SqlOverrideRepository(OverrideRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def upsert(
        self, user_id: UserId, transaction_id: TransactionId, category_id: CustomCategoryId
    ) -> None:
        statement = insert(CategoryOverrideRow).values(
            user_id=user_id, transaction_id=transaction_id, custom_category_id=category_id
        )
        self._session.execute(
            statement.on_conflict_do_update(
                index_elements=["user_id", "transaction_id"],
                set_={"custom_category_id": statement.excluded.custom_category_id},
            )
        )

    @translates_database_errors
    def delete(self, user_id: UserId, transaction_id: TransactionId) -> None:
        self._session.execute(
            delete(CategoryOverrideRow).where(
                CategoryOverrideRow.user_id == user_id,
                CategoryOverrideRow.transaction_id == transaction_id,
            )
        )
