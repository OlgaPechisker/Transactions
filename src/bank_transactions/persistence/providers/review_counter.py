"""Count of transactions awaiting a currency."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bank_transactions.domain.ids import UserId
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import ReviewCounterRepository
from bank_transactions.persistence.visibility import visible_transactions, visible_unknown_currency


class SqlReviewCounterRepository(ReviewCounterRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def count_visible_unknown_currency(self, user_id: UserId) -> int:
        visible = visible_transactions(user_id).where(visible_unknown_currency()).subquery()
        return self._session.scalar(select(func.count()).select_from(visible)) or 0
