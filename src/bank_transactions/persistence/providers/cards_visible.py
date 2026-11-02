"""Cards a user sees through a claim. Stub: implemented by U4."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import PublicId, UserId
from bank_transactions.persistence.interfaces import VisibleCardRepository
from bank_transactions.persistence.records import VisibleCardRecord


class SqlVisibleCardRepository(VisibleCardRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_visible(self, user_id: UserId) -> tuple[VisibleCardRecord, ...]:
        raise NotImplementedError

    def get_visible(self, user_id: UserId, public_id: PublicId) -> VisibleCardRecord | None:
        raise NotImplementedError

    def set_display_name(
        self, user_id: UserId, public_id: PublicId, display_name: str | None
    ) -> VisibleCardRecord | None:
        raise NotImplementedError
