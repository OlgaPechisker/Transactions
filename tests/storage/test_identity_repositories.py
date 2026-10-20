import pytest

from bank_transactions.domain.enums import IdentifierType, IssuerCode
from bank_transactions.domain.errors import PersistenceError
from bank_transactions.domain.ids import AccountId, OwnerId
from bank_transactions.domain.keys import OwnerMatchKey
from bank_transactions.persistence.commands import NewAccount, NewCard, NewOwner
from bank_transactions.persistence.records import CardLookup

pytestmark = pytest.mark.postgres


def new_owner(name: str = "Dana Levi") -> NewOwner:
    return NewOwner(name, OwnerMatchKey.from_display_name(name))


def test_a_created_owner_has_its_real_id_and_is_visible_in_the_same_unit_of_work(factory):
    with factory.create() as uow:
        owner = uow.owners.create(new_owner())

        assert owner.owner_id > 0
        assert uow.owners.find_by_match_key(OwnerMatchKey("dana levi")) == owner
        assert uow.owners.get(owner.owner_id) == owner


def test_an_uncommitted_owner_is_invisible_to_other_units_of_work_and_vanishes_on_rollback(
    factory,
):
    with factory.create() as uow:
        uow.owners.create(new_owner())
        with factory.create() as other:
            assert other.owners.list_all() == ()

    with factory.create() as after:
        assert after.owners.list_all() == ()


def test_owners_are_listed_by_id_and_a_missing_owner_is_none(factory):
    with factory.create() as uow:
        first = uow.owners.create(new_owner("Dana Levi"))
        second = uow.owners.create(new_owner("Avi Cohen"))
        uow.commit()

    with factory.create() as uow:
        assert uow.owners.list_all() == (first, second)
        assert uow.owners.get(OwnerId(99999)) is None
        assert uow.owners.find_by_match_key(OwnerMatchKey("nobody")) is None


def test_a_duplicate_owner_match_key_raises_a_neutral_persistence_error(factory):
    with factory.create() as uow:
        uow.owners.create(new_owner("Dana Levi"))
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError) as raised:
        uow.owners.create(new_owner("DANA   levi"))

    assert "dana levi" not in str(raised.value).lower()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_account_lookup_is_an_exact_ciphertext_pair_match(factory, cipher):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    other_number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221039")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner())
        account = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", number, "main"))
        uow.commit()

    with factory.create() as uow:
        assert uow.accounts.find_by_number("LEUMI", number) == (account,)
        assert uow.accounts.find_by_number("HAPOALIM", number) == ()
        assert uow.accounts.find_by_number("LEUMI", other_number) == ()
        assert uow.accounts.get(account.account_id) == account
        assert uow.accounts.list_all() == (account,)


def test_account_lookup_without_a_bank_code_is_null_safe(factory, cipher):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner())
        unbanked = uow.accounts.create(NewAccount(owner.owner_id, None, number, None))
        banked = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", number, None))

        assert uow.accounts.find_by_number(None, number) == (unbanked,)
        assert uow.accounts.find_by_number("LEUMI", number) == (banked,)


def test_the_same_bank_and_account_number_cannot_be_stored_twice(factory, cipher):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner())
        uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", number, None))
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", number, None))


def test_one_number_without_a_bank_code_may_belong_to_several_accounts(factory, cipher):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    with factory.create() as uow:
        first_owner = uow.owners.create(new_owner("Dana Levi"))
        second_owner = uow.owners.create(new_owner("Avi Cohen"))
        first = uow.accounts.create(NewAccount(first_owner.owner_id, None, number, "one"))
        second = uow.accounts.create(NewAccount(second_owner.owner_id, None, number, "two"))
        same_owner = uow.accounts.create(NewAccount(first_owner.owner_id, None, number, "three"))
        uow.commit()

    with factory.create() as uow:
        assert uow.accounts.find_by_number(None, number) == (first, second, same_owner)


def test_lookup_with_a_bank_code_never_returns_accounts_of_unknown_banks(factory, cipher):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner())
        uow.accounts.create(NewAccount(owner.owner_id, None, number, None))
        known = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", number, None))
        uow.accounts.create(NewAccount(owner.owner_id, "HAPOALIM", number, None))

        assert uow.accounts.find_by_number("LEUMI", number) == (known,)


def test_accounts_without_a_number_may_repeat(factory):
    with factory.create() as uow:
        owner = uow.owners.create(new_owner())
        first = uow.accounts.create(NewAccount(owner.owner_id, None, None, "cash"))
        second = uow.accounts.create(NewAccount(owner.owner_id, None, None, "other"))

        assert first.account_id != second.account_id


def test_cards_sharing_issuer_and_last4_are_all_stored_and_found_in_id_order(factory, cipher):
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    with factory.create() as uow:
        first_owner = uow.owners.create(new_owner("Dana Levi"))
        second_owner = uow.owners.create(new_owner("Avi Cohen"))
        first_account = uow.accounts.create(NewAccount(first_owner.owner_id, "LEUMI", None, None))
        second_account = uow.accounts.create(NewAccount(second_owner.owner_id, "LEUMI", None, None))
        first = uow.cards.create(
            NewCard(first_owner.owner_id, first_account.account_id, IssuerCode.MAX, last4, None)
        )
        second = uow.cards.create(
            NewCard(second_owner.owner_id, second_account.account_id, IssuerCode.MAX, last4, "b")
        )
        uow.commit()

    with factory.create() as uow:
        lookup = CardLookup(IssuerCode.MAX, last4)
        assert uow.cards.find_candidates(lookup) == (first, second)
        assert uow.cards.get(second.card_id) == second


def test_card_candidates_narrow_by_issuer_account_owner_and_ciphertext(factory, cipher):
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner("Dana Levi"))
        other_owner = uow.owners.create(new_owner("Avi Cohen"))
        account = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", None, None))
        other_account = uow.accounts.create(NewAccount(other_owner.owner_id, "LEUMI", None, None))
        mine = uow.cards.create(
            NewCard(owner.owner_id, account.account_id, IssuerCode.MAX, last4, None)
        )
        theirs = uow.cards.create(
            NewCard(other_owner.owner_id, other_account.account_id, IssuerCode.MAX, last4, None)
        )
        uow.commit()

    with factory.create() as uow:
        assert uow.cards.find_candidates(CardLookup(IssuerCode.CAL, last4)) == ()
        assert (
            uow.cards.find_candidates(
                CardLookup(IssuerCode.MAX, cipher.encrypt(IdentifierType.CARD_LAST4, "1111"))
            )
            == ()
        )
        assert uow.cards.find_candidates(
            CardLookup(IssuerCode.MAX, last4, account_id=account.account_id)
        ) == (mine,)
        assert uow.cards.find_candidates(
            CardLookup(IssuerCode.MAX, last4, owner_id=other_owner.owner_id)
        ) == (theirs,)
        assert (
            uow.cards.find_candidates(
                CardLookup(
                    IssuerCode.MAX,
                    last4,
                    account_id=account.account_id,
                    owner_id=other_owner.owner_id,
                )
            )
            == ()
        )


def test_a_card_cannot_belong_to_an_owner_other_than_its_accounts_owner(factory, cipher):
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner("Dana Levi"))
        other_owner = uow.owners.create(new_owner("Avi Cohen"))
        account = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", None, None))

        with pytest.raises(PersistenceError) as raised:
            uow.cards.create(
                NewCard(other_owner.owner_id, account.account_id, IssuerCode.MAX, last4, None)
            )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_a_card_for_an_unknown_account_is_rejected(factory, cipher):
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner())

        with pytest.raises(PersistenceError):
            uow.cards.create(NewCard(owner.owner_id, AccountId(99999), IssuerCode.MAX, last4, None))


def test_the_database_holds_only_ciphertext_for_card_and_account_numbers(seed_card, sql, cipher):
    seeded = seed_card(last4="4396", account_number="8917221038")

    last4_enc = bytes(sql("select last4_enc from cards")[0][0])
    number_enc = bytes(sql("select account_number_enc from accounts")[0][0])
    dump = " ".join(
        row[0] for row in sql("select row_to_json(t)::text from (select * from accounts) t")
    )

    assert last4_enc != b"4396"
    assert cipher.decrypt(IdentifierType.CARD_LAST4, last4_enc) == "4396"
    assert number_enc != b"8917221038"
    assert cipher.decrypt(IdentifierType.ACCOUNT_NUMBER, number_enc) == "8917221038"
    assert "8917221038" not in dump
    assert seeded.card_id > 0
