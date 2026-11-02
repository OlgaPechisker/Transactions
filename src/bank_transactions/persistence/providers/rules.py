"""Merchant-term category rules. Stub: implemented by U7."""

from sqlalchemy.orm import Session

from bank_transactions.domain.ids import PublicId, UserId
from bank_transactions.persistence.commands import NewRule
from bank_transactions.persistence.interfaces import RuleRepository
from bank_transactions.persistence.records import RuleRecord


class SqlRuleRepository(RuleRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_newest_first(self, user_id: UserId) -> tuple[RuleRecord, ...]:
        raise NotImplementedError

    def create(self, rule: NewRule) -> RuleRecord:
        raise NotImplementedError

    def delete(self, user_id: UserId, public_id: PublicId) -> bool:
        raise NotImplementedError
