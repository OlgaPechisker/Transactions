"""Count of transactions awaiting a currency. Stub: implemented by U1."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import UserId
from bank_transactions.persistence.interfaces import ReviewCounterRepository


class SqlReviewCounterRepository(ReviewCounterRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def count_visible_unknown_currency(self, user_id: UserId) -> int:
        raise NotImplementedError
