"""The category service: custom category names, mappings, rules and overrides, each user's own."""

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

import pytest
from tests.fakes.auth import make_auth_context
from tests.fakes.unit_of_work import InMemoryUnitOfWorkFactory

from bank_transactions.domain.enums import CategorySource, IssuerCode, TransactionType
from bank_transactions.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from bank_transactions.domain.ids import (
    CardId,
    CustomCategoryId,
    IdentifierToken,
    PublicId,
    RuleId,
    TransactionId,
)
from bank_transactions.persistence.commands import NewCustomCategory, NewRule
from bank_transactions.persistence.interfaces import Isolation
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    CustomCategoryRecord,
    GroupRecord,
    MappingRecord,
    NamedCountRecord,
    RuleRecord,
    TransactionRef,
    TransactionViewRecord,
    VisibleCardRecord,
)
from bank_transactions.services.category_service import CategoryService

ANN = make_auth_context(user_id=1)
BEN = make_auth_context(user_id=2)
CARD = VisibleCardRecord(
    CardId(1), PublicId("crd_1"), IssuerCode.MAX, IdentifierToken(b"enc"), None, None
)


def view(transaction_id: int, public_id: str) -> TransactionViewRecord:
    return TransactionViewRecord(
        transaction_id=TransactionId(transaction_id),
        public_id=PublicId(public_id),
        transaction_date=date(2026, 7, 3),
        charge_date=None,
        merchant="Super Pharm",
        city=None,
        country=None,
        bank_category="Pharmacy",
        custom_category=None,
        effective_category="Pharmacy",
        category_source=CategorySource.BANK,
        card=CARD,
        transaction_type=TransactionType.REGULAR,
        installment_number=None,
        installment_total=None,
        original_amount=None,
        original_currency=None,
        billing_amount=Decimal("10.00"),
        billing_currency="ILS",
        effective_rate=None,
        commission=None,
        trip=None,
        notes=None,
    )


@dataclass
class Store:
    """What the fakes persist, shared by every unit of work of one test."""

    categories: dict[tuple[int, str], CustomCategoryRecord] = field(default_factory=dict)
    mappings: dict[tuple[int, str], int] = field(default_factory=dict)
    rules: list[tuple[int, RuleRecord]] = field(default_factory=list)
    overrides: dict[tuple[int, int], int] = field(default_factory=dict)
    transactions: dict[int, list[TransactionViewRecord]] = field(default_factory=dict)
    bank_categories: dict[int, list[str]] = field(default_factory=dict)

    def category_name(self, category_id: int) -> str:
        return next(r.name for r in self.categories.values() if r.category_id == category_id)


class FakeCategories:
    def __init__(self, store: Store) -> None:
        self._store = store

    def list(self, user_id):
        return tuple(r for (u, _), r in sorted(self._store.categories.items()) if u == user_id)

    def find_by_key(self, user_id, name_key):
        return self._store.categories.get((user_id, name_key))

    def create(self, new: NewCustomCategory):
        if (new.user_id, new.name_key) in self._store.categories:
            raise ConflictError(ConflictError.CATEGORY_EXISTS)
        record = CustomCategoryRecord(
            CustomCategoryId(len(self._store.categories) + 1), new.name, new.name_key
        )
        self._store.categories[(new.user_id, new.name_key)] = record
        return record


class FakeMappings:
    def __init__(self, store: Store) -> None:
        self._store = store

    def list(self, user_id):
        return tuple(
            MappingRecord(
                bank, CustomCategoryId(category_id), self._store.category_name(category_id)
            )
            for (u, bank), category_id in self._store.mappings.items()
            if u == user_id
        )

    def upsert(self, user_id, bank_category, category_id):
        self._store.mappings[(user_id, bank_category)] = category_id
        return MappingRecord(bank_category, category_id, self._store.category_name(category_id))

    def delete(self, user_id, bank_category):
        return self._store.mappings.pop((user_id, bank_category), None) is not None


class FakeRules:
    def __init__(self, store: Store) -> None:
        self._store = store

    def list_newest_first(self, user_id):
        return tuple(r for u, r in reversed(self._store.rules) if u == user_id)

    def create(self, new: NewRule):
        number = len(self._store.rules) + 1
        record = RuleRecord(
            RuleId(number),
            PublicId(f"rul_{number}"),
            new.terms,
            new.category_id,
            self._store.category_name(new.category_id),
        )
        self._store.rules.append((new.user_id, record))
        return record

    def delete(self, user_id, public_id):
        owned = [
            item
            for item in self._store.rules
            if item[0] == user_id and item[1].public_id == public_id
        ]
        for item in owned:
            self._store.rules.remove(item)
        return bool(owned)


class FakeOverrides:
    def __init__(self, store: Store) -> None:
        self._store = store

    def upsert(self, user_id, transaction_id, category_id):
        self._store.overrides[(user_id, transaction_id)] = category_id

    def delete(self, user_id, transaction_id):
        self._store.overrides.pop((user_id, transaction_id), None)


class FakeTransactionQuery:
    def __init__(self, store: Store) -> None:
        self._store = store

    def has_bank_category(self, user_id, bank_category):
        return bank_category in self._store.bank_categories.get(user_id, [])

    def bank_category_counts(self, user_id):
        return tuple(NamedCountRecord(name, 3) for name in self._store.bank_categories[user_id])

    def groups(self, user_id, filter, dimension):
        return (
            GroupRecord("Health", BillingSummaryRecord(2, 0, (), 0)),
            GroupRecord(None, BillingSummaryRecord(1, 0, (), 0)),
        )

    def lock_visible(self, user_id, public_ids):
        return tuple(
            TransactionRef(r.transaction_id, r.public_id)
            for r in self._store.transactions.get(user_id, [])
            if r.public_id in public_ids
        )

    def get(self, user_id, public_id):
        record = next(
            (r for r in self._store.transactions.get(user_id, []) if r.public_id == public_id),
            None,
        )
        if record is None:
            return None
        chosen = self._store.overrides.get((user_id, record.transaction_id))
        return (
            replace(record, custom_category=self._store.category_name(chosen)) if chosen else record
        )


@pytest.fixture
def store() -> Store:
    return Store(
        transactions={ANN.user_id: [view(1, "txn_1")]},
        bank_categories={ANN.user_id: ["Pharmacy"], BEN.user_id: ["Pharmacy"]},
    )


@pytest.fixture
def factory(store: Store) -> InMemoryUnitOfWorkFactory:
    factory = InMemoryUnitOfWorkFactory()
    factory.template.categories = FakeCategories(store)
    factory.template.mappings = FakeMappings(store)
    factory.template.rules = FakeRules(store)
    factory.template.overrides = FakeOverrides(store)
    factory.template.transaction_query = FakeTransactionQuery(store)
    return factory


@pytest.fixture
def service(factory) -> CategoryService:
    return CategoryService(factory)


def error_codes(raised: pytest.ExceptionInfo[ValidationFailedError]) -> list[tuple[str, str]]:
    return [(e.field, e.code) for e in raised.value.errors]


# --- custom categories ----------------------------------------------------------------------------


def test_a_name_is_trimmed_and_keeps_its_case_but_is_keyed_casefolded(service, store, factory):
    created = service.create_category(ANN, "  Groceries ")

    assert (created.name, created.name_key) == ("Groceries", "groceries")
    assert store.categories[(ANN.user_id, "groceries")] == created
    assert factory.created[-1][0].committed


@pytest.mark.parametrize("again", ["groceries", "GROCERIES", " Groceries  "])
def test_a_duplicate_name_in_any_casing_exists_already(service, again):
    service.create_category(ANN, "Groceries")

    with pytest.raises(ConflictError) as raised:
        service.create_category(ANN, again)

    assert raised.value.code == ConflictError.CATEGORY_EXISTS


def test_two_users_may_use_the_same_name(service):
    service.create_category(ANN, "Groceries")

    assert service.create_category(BEN, "groceries").name == "groceries"


@pytest.mark.parametrize("name", ["none", "None", "NONE", "  none "])
def test_the_reserved_name_is_rejected_in_any_casing(service, factory, name):
    with pytest.raises(ValidationFailedError) as raised:
        service.create_category(ANN, name)

    assert error_codes(raised) == [("name", "RESERVED_NAME")]
    assert factory.created == []


@pytest.mark.parametrize(
    ("name", "code"), [("", "TOO_SHORT"), ("   ", "TOO_SHORT"), ("x" * 61, "TOO_LONG")]
)
def test_a_blank_or_too_long_name_is_invalid(service, name, code):
    with pytest.raises(ValidationFailedError) as raised:
        service.create_category(ANN, name)

    assert error_codes(raised) == [("name", code)]


def test_a_name_of_sixty_characters_is_accepted(service):
    assert service.create_category(ANN, "x" * 60).name == "x" * 60


# --- mappings -------------------------------------------------------------------------------------


def test_a_bank_category_is_mapped_to_a_custom_category_found_by_any_casing(service, store):
    service.create_category(ANN, "Health")

    mapping = service.put_mapping(ANN, "Pharmacy", "HEALTH ")

    assert (mapping.bank_category, mapping.category_name) == ("Pharmacy", "Health")
    assert store.mappings == {(ANN.user_id, "Pharmacy"): mapping.category_id}


def test_mapping_replaces_the_previous_target(service, store):
    service.create_category(ANN, "Health")
    second = service.create_category(ANN, "Care")
    service.put_mapping(ANN, "Pharmacy", "Health")

    service.put_mapping(ANN, "Pharmacy", "Care")

    assert store.mappings == {(ANN.user_id, "Pharmacy"): second.category_id}


def test_a_bank_category_the_user_does_not_have_is_not_found_even_with_an_unknown_target(service):
    with pytest.raises(NotFoundError):
        service.put_mapping(ANN, "Gambling", "Nowhere")


def test_an_unknown_custom_category_is_a_validation_error(service, store):
    with pytest.raises(ValidationFailedError) as raised:
        service.put_mapping(ANN, "Pharmacy", "Nowhere")

    assert error_codes(raised) == [("customCategory", "UNKNOWN_CATEGORY")]
    assert store.mappings == {}


def test_another_users_custom_category_is_unknown(service, store):
    service.create_category(ANN, "Health")

    with pytest.raises(ValidationFailedError) as raised:
        service.put_mapping(BEN, "Pharmacy", "Health")

    assert error_codes(raised) == [("customCategory", "UNKNOWN_CATEGORY")]
    assert store.mappings == {}


def test_a_mapping_is_deleted_once(service):
    service.create_category(ANN, "Health")
    service.put_mapping(ANN, "Pharmacy", "Health")

    service.delete_mapping(ANN, "Pharmacy")

    with pytest.raises(NotFoundError):
        service.delete_mapping(ANN, "Pharmacy")


def test_another_users_mapping_cannot_be_deleted(service):
    service.create_category(ANN, "Health")
    service.put_mapping(ANN, "Pharmacy", "Health")

    with pytest.raises(NotFoundError):
        service.delete_mapping(BEN, "Pharmacy")


# --- rules ----------------------------------------------------------------------------------------


def test_rule_terms_are_normalised_like_merchant_names_without_blanks_or_duplicates(service):
    service.create_category(ANN, "Coffee")

    rule = service.create_rule(
        ANN, ["  Aroma   TLV ", "aroma tlv", "McDonald’s", "   ", "ארומה"], "coffee"
    )

    assert rule.terms == ("aroma tlv", "mcdonald's", "ארומה")
    assert rule.category_name == "Coffee"


def test_a_rule_without_a_usable_term_is_invalid(service):
    service.create_category(ANN, "Coffee")

    with pytest.raises(ValidationFailedError) as raised:
        service.create_rule(ANN, ["  ", ""], "Coffee")

    assert error_codes(raised) == [("terms", "TOO_SHORT")]


def test_a_rule_for_an_unknown_category_is_a_validation_error(service, store):
    with pytest.raises(ValidationFailedError) as raised:
        service.create_rule(ANN, ["aroma"], "Coffee")

    assert error_codes(raised) == [("category", "UNKNOWN_CATEGORY")]
    assert store.rules == []


def test_a_rule_is_deleted_once_and_only_by_its_owner(service):
    service.create_category(ANN, "Coffee")
    rule = service.create_rule(ANN, ["aroma"], "Coffee")

    with pytest.raises(NotFoundError):
        service.delete_rule(BEN, rule.public_id)
    service.delete_rule(ANN, rule.public_id)
    with pytest.raises(NotFoundError):
        service.delete_rule(ANN, rule.public_id)


# --- overrides ------------------------------------------------------------------------------------


def test_an_override_sets_the_category_and_returns_the_transaction(service, store, factory):
    created = service.create_category(ANN, "Health")

    record = service.set_override(ANN, PublicId("txn_1"), "health")

    assert record.custom_category == "Health"
    assert store.overrides == {(ANN.user_id, 1): created.category_id}
    assert factory.created[-1][0].committed


def test_a_null_category_clears_the_override(service, store):
    service.create_category(ANN, "Health")
    service.set_override(ANN, PublicId("txn_1"), "Health")

    record = service.set_override(ANN, PublicId("txn_1"), None)

    assert store.overrides == {}
    assert record.custom_category is None


def test_clearing_an_override_that_was_never_set_is_accepted(service):
    assert service.set_override(ANN, PublicId("txn_1"), None).public_id == "txn_1"


def test_an_override_with_an_unknown_category_is_a_validation_error(service, store, factory):
    with pytest.raises(ValidationFailedError) as raised:
        service.set_override(ANN, PublicId("txn_1"), "Nowhere")

    assert error_codes(raised) == [("customCategory", "UNKNOWN_CATEGORY")]
    assert store.overrides == {}
    assert not factory.created[-1][0].committed


def test_a_transaction_of_another_user_is_not_found(service, store):
    service.create_category(BEN, "Health")

    with pytest.raises(NotFoundError):
        service.set_override(BEN, PublicId("txn_1"), "Health")

    assert store.overrides == {}


def test_an_unknown_transaction_is_not_found_before_the_category_is_checked(service):
    with pytest.raises(NotFoundError):
        service.set_override(ANN, PublicId("txn_404"), "Nowhere")


# --- overview -------------------------------------------------------------------------------------


def test_the_overview_reads_one_snapshot_and_leaves_the_uncategorised_out_of_the_results(
    service, factory
):
    service.create_category(ANN, "Health")
    service.put_mapping(ANN, "Pharmacy", "Health")
    service.create_rule(ANN, ["super"], "Health")

    overview = service.overview(ANN)

    assert overview.bank_categories == (NamedCountRecord("Pharmacy", 3),)
    assert [m.category_name for m in overview.mappings] == ["Health"]
    assert [r.terms for r in overview.rules] == [("super",)]
    assert [c.name for c in overview.custom_categories] == ["Health"]
    assert overview.resulting_categories == (NamedCountRecord("Health", 2),)
    assert factory.created[-1][1] == Isolation.REPEATABLE_READ
