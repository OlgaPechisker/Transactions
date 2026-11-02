"""Custom categories, mappings, rules and per-transaction overrides. Stub: implemented by U7."""

from collections.abc import Sequence
from dataclasses import dataclass

from bank_transactions.domain.ids import PublicId
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import (
    CustomCategoryRecord,
    MappingRecord,
    NamedCountRecord,
    RuleRecord,
    TransactionViewRecord,
)
from bank_transactions.services.interfaces import AuthContext


@dataclass(frozen=True)
class CategorizationOverview:
    bank_categories: tuple[NamedCountRecord, ...]
    mappings: tuple[MappingRecord, ...]
    rules: tuple[RuleRecord, ...]
    custom_categories: tuple[CustomCategoryRecord, ...]


class CategoryService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def overview(self, context: AuthContext) -> CategorizationOverview:
        raise NotImplementedError

    def create_category(self, context: AuthContext, name: str) -> CustomCategoryRecord:
        """Raises ConflictError(CATEGORY_EXISTS) or ValidationFailedError (RESERVED_NAME)."""
        raise NotImplementedError

    def put_mapping(
        self, context: AuthContext, bank_category: str, custom_category: str
    ) -> MappingRecord:
        raise NotImplementedError

    def delete_mapping(self, context: AuthContext, bank_category: str) -> None:
        raise NotImplementedError

    def create_rule(
        self, context: AuthContext, terms: Sequence[str], custom_category: str
    ) -> RuleRecord:
        raise NotImplementedError

    def delete_rule(self, context: AuthContext, rule_id: PublicId) -> None:
        raise NotImplementedError

    def set_override(
        self, context: AuthContext, transaction_id: PublicId, custom_category: str | None
    ) -> TransactionViewRecord:
        """`PATCH /transactions/{id}`. None clears the override."""
        raise NotImplementedError
