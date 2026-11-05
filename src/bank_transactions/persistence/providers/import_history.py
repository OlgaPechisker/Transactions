"""The caller's import history: committed runs that contributed transactions they can see."""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from bank_transactions.domain.enums import ImportStatus
from bank_transactions.domain.ids import PublicId, UserId
from bank_transactions.domain.queries import Page, PageRequest
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import ImportHistoryRepository
from bank_transactions.persistence.orm.models import ImportRunRow
from bank_transactions.persistence.records import ImportSummaryRecord
from bank_transactions.persistence.visibility import visible_transactions

_HISTORY_STATUSES = (ImportStatus.SUCCESS, ImportStatus.PARTIAL)


class SqlImportHistoryRepository(ImportHistoryRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def page(
        self, user_id: UserId, page: PageRequest, descending: bool
    ) -> Page[ImportSummaryRecord]:
        """`rows_imported` counts the transactions of the run the user can see, so a run never
        reveals how many rows were imported for other users' cards."""
        visible = visible_transactions(user_id).subquery("visible")
        count = sa.func.count(visible.c.id)
        runs = (
            sa.select(
                ImportRunRow.public_id,
                ImportRunRow.file_sha256,
                ImportRunRow.started_at,
                ImportRunRow.status,
                count.label("rows_imported"),
            )
            .join(visible, visible.c.import_run_id == ImportRunRow.id)
            .where(ImportRunRow.status.in_(_HISTORY_STATUSES))
            .group_by(ImportRunRow.id)
        )
        order = sa.desc if descending else sa.asc
        rows = self._session.execute(
            runs.order_by(order(ImportRunRow.started_at), order(ImportRunRow.id))
            .limit(page.size)
            .offset(page.offset)
        ).all()
        total = self._session.scalar(sa.select(sa.func.count()).select_from(runs.subquery())) or 0
        items = tuple(
            ImportSummaryRecord(
                PublicId(row.public_id),
                row.file_sha256,
                row.started_at,
                row.status,
                row.rows_imported,
            )
            for row in rows
        )
        return Page(items, page.page, page.size, total)
