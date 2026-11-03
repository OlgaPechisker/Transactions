"""Merchant-term category rules."""

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, delete, func, insert, select
from sqlalchemy.orm import Session

from bank_transactions.domain.ids import CustomCategoryId, PublicId, RuleId, UserId
from bank_transactions.persistence.commands import NewRule
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import RuleRepository
from bank_transactions.persistence.orm.models import (
    CategoryRuleRow,
    CategoryRuleTermRow,
    CustomCategoryRow,
    TransactionRow,
)
from bank_transactions.persistence.public_ids import new_public_id
from bank_transactions.persistence.records import RuleRecord
from bank_transactions.persistence.visibility import visible_transactions

_rules = CategoryRuleRow.__table__
_terms = CategoryRuleTermRow.__table__


class SqlRuleRepository(RuleRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def list_newest_first(self, user_id: UserId) -> tuple[RuleRecord, ...]:
        return self._load(user_id, _rules.c.user_id == user_id)

    @translates_database_errors
    def create(self, rule: NewRule) -> RuleRecord:
        rule_id = self._session.execute(
            insert(CategoryRuleRow)
            .values(
                public_id=new_public_id("rul"),
                user_id=rule.user_id,
                custom_category_id=rule.category_id,
            )
            .returning(CategoryRuleRow.id)
        ).scalar_one()
        self._session.execute(
            insert(CategoryRuleTermRow),
            [{"rule_id": rule_id, "term_norm": term} for term in rule.terms],
        )
        (created,) = self._load(rule.user_id, _rules.c.id == rule_id)
        return created

    @translates_database_errors
    def delete(self, user_id: UserId, public_id: PublicId) -> bool:
        deleted = self._session.execute(
            delete(CategoryRuleRow)
            .where(CategoryRuleRow.user_id == user_id, CategoryRuleRow.public_id == public_id)
            .returning(CategoryRuleRow.id)
        ).first()
        return deleted is not None

    def _load(self, user_id: UserId, condition: ColumnElement[bool]) -> tuple[RuleRecord, ...]:
        rows = self._session.execute(
            select(
                _rules.c.id,
                _rules.c.public_id,
                _rules.c.custom_category_id,
                CustomCategoryRow.name,
            )
            .join(CustomCategoryRow, CustomCategoryRow.id == _rules.c.custom_category_id)
            .where(condition)
            .order_by(_rules.c.id.desc())
        ).all()
        rule_ids = [row[0] for row in rows]
        terms = self._terms(rule_ids)
        matched = self._matched_counts(user_id, rule_ids)
        return tuple(
            RuleRecord(
                rule_id=RuleId(row[0]),
                public_id=PublicId(row[1]),
                terms=tuple(terms[row[0]]),
                category_id=CustomCategoryId(row[2]),
                category_name=row[3],
                matched_count=matched.get(row[0], 0),
            )
            for row in rows
        )

    def _terms(self, rule_ids: Sequence[int]) -> dict[int, list[str]]:
        terms: dict[int, list[str]] = defaultdict(list)
        for rule_id, term in self._session.execute(
            select(_terms.c.rule_id, _terms.c.term_norm)
            .where(_terms.c.rule_id.in_(rule_ids))
            .order_by(_terms.c.rule_id, _terms.c.term_norm)
        ):
            terms[rule_id].append(term)
        return terms

    def _matched_counts(self, user_id: UserId, rule_ids: Sequence[int]) -> dict[int, int]:
        """Visible transactions whose merchant contains any term of each rule, whether or not
        an override makes another category win."""
        visible: Any = (
            visible_transactions(user_id)
            .with_only_columns(TransactionRow.id, TransactionRow.merchant_name_norm)
            .subquery("visible")
        )
        rows = self._session.execute(
            select(_terms.c.rule_id, func.count(func.distinct(visible.c.id)))
            .join(visible, func.strpos(visible.c.merchant_name_norm, _terms.c.term_norm) > 0)
            .where(_terms.c.rule_id.in_(rule_ids))
            .group_by(_terms.c.rule_id)
        ).all()
        return {rule_id: count for rule_id, count in rows}
