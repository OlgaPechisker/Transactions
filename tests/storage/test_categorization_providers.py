"""Custom categories, mappings, rules and overrides on PostgreSQL: uniqueness, per-user scope, rule
statistics and the precedence override, newest rule, mapping, bank category."""

from collections.abc import Callable
from typing import Any

import pytest

from bank_transactions.domain.enums import CategorySource
from bank_transactions.domain.errors import ConflictError
from bank_transactions.domain.ids import PublicId
from bank_transactions.persistence.commands import NewCustomCategory, NewRule

pytestmark = pytest.mark.postgres


@pytest.fixture
def ann(seed_user):
    return seed_user("ann@example.com", "Ann")


@pytest.fixture
def ben(seed_user):
    return seed_user("ben@example.com", "Ben")


@pytest.fixture
def category(factory) -> Callable[[Any, str], Any]:
    def make(user: Any, name: str) -> Any:
        with factory.create() as uow:
            created = uow.categories.create(NewCustomCategory(user.user_id, name, name.casefold()))
            uow.commit()
        return created

    return make


@pytest.fixture
def pharmacy(ann, seed_claimed_card, seed_transaction, sql):
    """One transaction of Ann: merchant 'super pharm' with the bank category 'Pharmacy'."""
    card = seed_claimed_card(user=ann, last4="1111")
    transaction_id = seed_transaction(
        card,
        merchant_name_raw="Super Pharm",
        merchant_name_norm="super pharm",
        bank_category_raw="Pharmacy",
    )
    (row,) = sql("SELECT public_id FROM transactions WHERE id = :i", i=transaction_id)
    return transaction_id, PublicId(row[0])


@pytest.fixture
def effective(factory, ann, pharmacy) -> Callable[[], tuple[str | None, CategorySource]]:
    def read() -> tuple[str | None, CategorySource]:
        with factory.create() as uow:
            record = uow.transaction_query.get(ann.user_id, pharmacy[1])
        assert record is not None
        return record.effective_category, record.category_source

    return read


def add_rule(factory, user, category_record, *terms: str):
    with factory.create() as uow:
        rule = uow.rules.create(NewRule(user.user_id, category_record.category_id, terms))
        uow.commit()
    return rule


# --- custom categories ----------------------------------------------------------------------------


def test_a_category_keeps_its_name_and_is_unique_per_user_by_casefolded_key(
    factory, ann, ben, category
):
    created = category(ann, "Groceries")

    assert (created.name, created.name_key) == ("Groceries", "groceries")
    with pytest.raises(ConflictError) as raised:
        category(ann, "GROCERIES")
    assert raised.value.code == ConflictError.CATEGORY_EXISTS
    assert category(ben, "Groceries").category_id != created.category_id


def test_the_categories_of_a_user_are_listed_by_key_and_never_include_another_users(
    factory, ann, ben, category
):
    category(ann, "banana")
    category(ann, "Apple")
    category(ben, "Cherry")

    with factory.create() as uow:
        names = [c.name for c in uow.categories.list(ann.user_id)]
        found = uow.categories.find_by_key(ann.user_id, "apple")
        foreign = uow.categories.find_by_key(ann.user_id, "cherry")

    assert names == ["Apple", "banana"]
    assert found is not None and found.name == "Apple"
    assert foreign is None


# --- mappings -------------------------------------------------------------------------------------


def test_a_mapping_is_replaced_listed_with_the_category_name_and_deleted(
    factory, ann, ben, category
):
    health, care = category(ann, "Health"), category(ann, "Care")
    with factory.create() as uow:
        uow.mappings.upsert(ann.user_id, "Pharmacy", health.category_id)
        replaced = uow.mappings.upsert(ann.user_id, "Pharmacy", care.category_id)
        uow.mappings.upsert(ann.user_id, "Food", care.category_id)
        uow.commit()

    with factory.create() as uow:
        listed = [(m.bank_category, m.category_name) for m in uow.mappings.list(ann.user_id)]
        others = uow.mappings.list(ben.user_id)
        stranger_delete = uow.mappings.delete(ben.user_id, "Pharmacy")
        deleted = uow.mappings.delete(ann.user_id, "Pharmacy")
        deleted_again = uow.mappings.delete(ann.user_id, "Pharmacy")

    assert (replaced.bank_category, replaced.category_name) == ("Pharmacy", "Care")
    assert listed == [("Food", "Care"), ("Pharmacy", "Care")]
    assert others == ()
    assert (stranger_delete, deleted, deleted_again) == (False, True, False)


# --- rules ----------------------------------------------------------------------------------------


def test_rules_are_listed_newest_first_with_sorted_terms_and_a_public_id(factory, ann, category):
    coffee = category(ann, "Coffee")
    first = add_rule(factory, ann, coffee, "aroma", "cafe")
    second = add_rule(factory, ann, coffee, "zebra", "alpha")

    with factory.create() as uow:
        rules = uow.rules.list_newest_first(ann.user_id)

    assert [r.public_id for r in rules] == [second.public_id, first.public_id]
    assert [r.terms for r in rules] == [("alpha", "zebra"), ("aroma", "cafe")]
    assert {r.category_name for r in rules} == {"Coffee"}
    assert first.public_id.startswith("rul_")


def test_the_matched_count_is_the_visible_transactions_containing_any_term_once(
    factory, ann, ben, category, seed_claimed_card, seed_transaction
):
    coffee = category(ann, "Coffee")
    card = seed_claimed_card(user=ann, last4="1111")
    for merchant in ("aroma tlv", "cafe aroma", "bakery", "aroma"):
        seed_transaction(card, merchant_name_raw=merchant, merchant_name_norm=merchant)
    strangers_card = seed_claimed_card(user=ben, last4="2222")
    seed_transaction(strangers_card, merchant_name_raw="aroma", merchant_name_norm="aroma")
    rule = add_rule(factory, ann, coffee, "aroma", "cafe", "nothing")
    empty = add_rule(factory, ann, coffee, "nothing")

    with factory.create() as uow:
        counts = {r.public_id: r.matched_count for r in uow.rules.list_newest_first(ann.user_id)}

    assert rule.matched_count == 3
    assert counts == {rule.public_id: 3, empty.public_id: 0}


def test_a_rule_is_deleted_with_its_terms_and_only_by_its_owner(factory, sql, ann, ben, category):
    coffee = category(ann, "Coffee")
    rule = add_rule(factory, ann, coffee, "aroma", "cafe")

    with factory.create() as uow:
        stranger = uow.rules.delete(ben.user_id, rule.public_id)
        deleted = uow.rules.delete(ann.user_id, rule.public_id)
        again = uow.rules.delete(ann.user_id, rule.public_id)
        uow.commit()

    assert (stranger, deleted, again) == (False, True, False)
    assert sql("SELECT 1 FROM category_rule_terms") == []


# --- effective category ---------------------------------------------------------------------------


def test_precedence_is_override_then_newest_rule_then_mapping_then_the_bank(
    factory, ann, category, pharmacy, effective
):
    health, care, coffee, special = (
        category(ann, name) for name in ("Health", "Care", "Coffee", "Special")
    )
    assert effective() == ("Pharmacy", CategorySource.BANK)

    with factory.create() as uow:
        uow.mappings.upsert(ann.user_id, "Pharmacy", health.category_id)
        uow.commit()
    assert effective() == ("Health", CategorySource.MAPPING)

    add_rule(factory, ann, care, "pharm")
    add_rule(factory, ann, coffee, "super")
    assert effective() == ("Coffee", CategorySource.RULE)

    with factory.create() as uow:
        uow.overrides.upsert(ann.user_id, pharmacy[0], special.category_id)
        uow.commit()
    assert effective() == ("Special", CategorySource.OVERRIDE)

    with factory.create() as uow:
        uow.overrides.delete(ann.user_id, pharmacy[0])
        uow.commit()
    assert effective() == ("Coffee", CategorySource.RULE)


def test_an_override_is_replaced_and_clearing_a_missing_one_is_harmless(
    factory, ann, category, pharmacy, effective
):
    health, care = category(ann, "Health"), category(ann, "Care")
    with factory.create() as uow:
        uow.overrides.delete(ann.user_id, pharmacy[0])
        uow.overrides.upsert(ann.user_id, pharmacy[0], health.category_id)
        uow.overrides.upsert(ann.user_id, pharmacy[0], care.category_id)
        uow.commit()

    assert effective() == ("Care", CategorySource.OVERRIDE)


def test_the_rules_of_another_user_do_not_categorise_my_transactions(
    factory, ann, ben, category, effective
):
    add_rule(factory, ben, category(ben, "Theirs"), "super")

    assert effective() == ("Pharmacy", CategorySource.BANK)
