"""Import history. Stub: implemented by U5."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import UserId
from bank_transactions.domain.queries import Page, PageRequest
from bank_transactions.persistence.interfaces import ImportHistoryRepository
from bank_transactions.persistence.records import ImportSummaryRecord


class SqlImportHistoryRepository(ImportHistoryRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def page(
        self, user_id: UserId, page: PageRequest, descending: bool
    ) -> Page[ImportSummaryRecord]:
        raise NotImplementedError
