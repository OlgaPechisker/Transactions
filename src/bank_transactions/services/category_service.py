"""Custom categories, mappings, rules and per-transaction overrides.

The effective category of a transaction is, in order: the user's override, the newest matching
rule, the bank-category mapping, the bank's own category. The ordering itself is computed by the
read side (`persistence.visibility.effective_category`); this service only maintains the inputs.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from bank_transactions.domain.errors import NotFoundError, ValidationFailedError
from bank_transactions.domain.ids import PublicId, UserId
from bank_transactions.domain.queries import Dimension, TransactionFilter
from bank_transactions.domain.text import fold_text
from bank_transactions.persistence.commands import NewCustomCategory, NewRule
from bank_transactions.persistence.interfaces import Isolation, UnitOfWork, UnitOfWorkFactory
from bank_transactions.persistence.records import (
    CustomCategoryRecord,
    MappingRecord,
    NamedCountRecord,
    RuleRecord,
    TransactionViewRecord,
)
from bank_transactions.services.interfaces import AuthContext

__all__ = ["CATEGORY_NAME_MAX_LENGTH", "CategorizationOverview", "CategoryService"]

CATEGORY_NAME_MAX_LENGTH = 60
_RESERVED_NAME_KEY = "none"


@dataclass(frozen=True)
class CategorizationOverview:
    """`resulting_categories` counts every visible transaction by its effective category;
    transactions without any category are left out."""

    bank_categories: tuple[NamedCountRecord, ...]
    mappings: tuple[MappingRecord, ...]
    rules: tuple[RuleRecord, ...]
    custom_categories: tuple[CustomCategoryRecord, ...]
    resulting_categories: tuple[NamedCountRecord, ...]


def _name_key(name: str) -> str:
    return name.strip().casefold()


def _find_category(uow: UnitOfWork, user_id: UserId, name: str, field: str) -> CustomCategoryRecord:
    category = uow.categories.find_by_key(user_id, _name_key(name))
    if category is None:
        raise ValidationFailedError.single(
            field, "UNKNOWN_CATEGORY", "The category does not exist."
        )
    return category


class CategoryService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def overview(self, context: AuthContext) -> CategorizationOverview:
        user_id = context.user_id
        with self._uow_factory.create(isolation=Isolation.REPEATABLE_READ) as uow:
            query = uow.transaction_query
            groups = query.groups(user_id, TransactionFilter(), Dimension.CATEGORY)
            return CategorizationOverview(
                bank_categories=query.bank_category_counts(user_id),
                mappings=uow.mappings.list(user_id),
                rules=uow.rules.list_newest_first(user_id),
                custom_categories=uow.categories.list(user_id),
                resulting_categories=tuple(
                    NamedCountRecord(group.key, group.summary.count)
                    for group in groups
                    if group.key is not None
                ),
            )

    def create_category(self, context: AuthContext, name: str) -> CustomCategoryRecord:
        """The name is trimmed and keeps its case; uniqueness and the reserved name `none` are
        checked on the casefolded form. Raises ConflictError(CATEGORY_EXISTS) or
        ValidationFailedError (TOO_SHORT, TOO_LONG, RESERVED_NAME)."""
        trimmed = name.strip()
        if not trimmed:
            raise ValidationFailedError.single("name", "TOO_SHORT", "The name is too short.")
        if len(trimmed) > CATEGORY_NAME_MAX_LENGTH:
            raise ValidationFailedError.single("name", "TOO_LONG", "The name is too long.")
        key = _name_key(trimmed)
        if key == _RESERVED_NAME_KEY:
            raise ValidationFailedError.single("name", "RESERVED_NAME", "This name is reserved.")
        with self._uow_factory.create() as uow:
            category = uow.categories.create(NewCustomCategory(context.user_id, trimmed, key))
            uow.commit()
        return category

    def put_mapping(
        self, context: AuthContext, bank_category: str, custom_category: str
    ) -> MappingRecord:
        """Raises NotFoundError for a bank category none of the caller's transactions has, and
        ValidationFailedError (UNKNOWN_CATEGORY) for a custom category that does not exist."""
        user_id = context.user_id
        with self._uow_factory.create() as uow:
            if not uow.transaction_query.has_bank_category(user_id, bank_category):
                raise NotFoundError("the bank category does not exist")
            category = _find_category(uow, user_id, custom_category, "customCategory")
            mapping = uow.mappings.upsert(user_id, bank_category, category.category_id)
            uow.commit()
        return mapping

    def delete_mapping(self, context: AuthContext, bank_category: str) -> None:
        """Raises NotFoundError when the bank category has no mapping."""
        with self._uow_factory.create() as uow:
            if not uow.mappings.delete(context.user_id, bank_category):
                raise NotFoundError("the category mapping does not exist")
            uow.commit()

    def create_rule(
        self, context: AuthContext, terms: Sequence[str], custom_category: str
    ) -> RuleRecord:
        """Terms are normalised like `merchant_name_norm`; blanks and duplicates are dropped.
        Raises ValidationFailedError (TOO_SHORT for no usable term, UNKNOWN_CATEGORY)."""
        normalised = tuple(dict.fromkeys(term for term in map(fold_text, terms) if term))
        if not normalised:
            raise ValidationFailedError.single("terms", "TOO_SHORT", "A rule needs a term.")
        with self._uow_factory.create() as uow:
            category = _find_category(uow, context.user_id, custom_category, "category")
            rule = uow.rules.create(NewRule(context.user_id, category.category_id, normalised))
            uow.commit()
        return rule

    def delete_rule(self, context: AuthContext, rule_id: PublicId) -> None:
        """Raises NotFoundError when the rule does not exist."""
        with self._uow_factory.create() as uow:
            if not uow.rules.delete(context.user_id, rule_id):
                raise NotFoundError("the category rule does not exist")
            uow.commit()

    def set_override(
        self, context: AuthContext, transaction_id: PublicId, custom_category: str | None
    ) -> TransactionViewRecord:
        """`PATCH /transactions/{id}`. None clears the override. Raises NotFoundError for a
        transaction the caller cannot see and ValidationFailedError (UNKNOWN_CATEGORY)."""
        user_id = context.user_id
        with self._uow_factory.create() as uow:
            # The row lock keeps concurrent overrides of one transaction in sequence.
            locked = uow.transaction_query.lock_visible(user_id, [transaction_id])
            if not locked:
                raise NotFoundError("the transaction does not exist")
            if custom_category is None:
                uow.overrides.delete(user_id, locked[0].transaction_id)
            else:
                category = _find_category(uow, user_id, custom_category, "customCategory")
                uow.overrides.upsert(user_id, locked[0].transaction_id, category.category_id)
            record = uow.transaction_query.get(user_id, transaction_id)
            if record is None:
                raise NotFoundError("the transaction does not exist")
            uow.commit()
        return record
