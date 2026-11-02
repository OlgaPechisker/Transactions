import pytest

from bank_transactions.domain.enums import IdentifierType, IssuerCode
from bank_transactions.domain.errors import PersistenceError
from bank_transactions.domain.ids import AccountId, OwnerId
from bank_transactions.domain.keys import OwnerMatchKey
from bank_transactions.persistence.commands import NewAccount, NewCard, NewOwner
from bank_transactions.persistence.records import CardLookup

pytestmark = pytest.mark.postgres


@pytest.fixture
def user(seed_user):
    return seed_user()


@pytest.fixture
def other_user(seed_user):
    return seed_user("other.user@example.com", "Other User")


def new_owner(user, name: str = "Dana Levi") -> NewOwner:
    return NewOwner(name, OwnerMatchKey.from_display_name(name), user.user_id)


def new_card(uow, user, owner, account, last4, display_name=None, issuer=IssuerCode.MAX) -> NewCard:
    claim = uow.claims.create_or_get(user.user_id, issuer, last4)
    return NewCard(owner.owner_id, account.account_id, issuer, last4, display_name, claim.claim_id)


def test_a_created_owner_has_its_real_id_and_is_visible_in_the_same_unit_of_work(factory, user):
    with factory.create() as uow:
        owner = uow.owners.create(new_owner(user))

        assert owner.owner_id > 0
        assert uow.owners.find_by_match_key(OwnerMatchKey("dana levi"), user.user_id) == owner
        assert uow.owners.get(owner.owner_id, user.user_id) == owner


def test_an_uncommitted_owner_is_invisible_to_other_units_of_work_and_vanishes_on_rollback(
    factory, user
):
    with factory.create() as uow:
        uow.owners.create(new_owner(user))
        with factory.create() as other:
            assert other.owners.list_all(user.user_id) == ()

    with factory.create() as after:
        assert after.owners.list_all(user.user_id) == ()


def test_owners_are_listed_by_id_and_a_missing_owner_is_none(factory, user):
    with factory.create() as uow:
        first = uow.owners.create(new_owner(user, "Dana Levi"))
        second = uow.owners.create(new_owner(user, "Avi Cohen"))
        uow.commit()

    with factory.create() as uow:
        assert uow.owners.list_all(user.user_id) == (first, second)
        assert uow.owners.get(OwnerId(99999), user.user_id) is None
        assert uow.owners.find_by_match_key(OwnerMatchKey("nobody"), user.user_id) is None


def test_a_duplicate_owner_match_key_for_one_user_raises_a_neutral_persistence_error(factory, user):
    with factory.create() as uow:
        uow.owners.create(new_owner(user, "Dana Levi"))
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError) as raised:
        uow.owners.create(new_owner(user, "DANA   levi"))

    assert "dana levi" not in str(raised.value).lower()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_two_users_may_each_have_an_owner_with_the_same_name_and_never_see_each_other(
    factory, user, other_user
):
    with factory.create() as uow:
        mine = uow.owners.create(new_owner(user))
        theirs = uow.owners.create(new_owner(other_user))
        uow.commit()

    with factory.create() as uow:
        assert uow.owners.list_all(user.user_id) == (mine,)
        assert uow.owners.list_all(other_user.user_id) == (theirs,)
        assert uow.owners.get(theirs.owner_id, user.user_id) is None
        assert uow.owners.find_by_match_key(mine.match_key, other_user.user_id) == theirs


def test_an_owner_cannot_be_created_without_a_user(factory):
    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.owners.create(NewOwner("Dana", OwnerMatchKey("dana")))


def test_account_lookup_is_an_exact_ciphertext_pair_match(factory, cipher, user):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    other_number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221039")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner(user))
        account = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", number, "main"))
        uow.commit()

    with factory.create() as uow:
        assert uow.accounts.find_by_number("LEUMI", number, user.user_id) == (account,)
        assert uow.accounts.find_by_number("HAPOALIM", number, user.user_id) == ()
        assert uow.accounts.find_by_number("LEUMI", other_number, user.user_id) == ()
        assert uow.accounts.get(account.account_id, user.user_id) == account
        assert uow.accounts.list_all(user.user_id) == (account,)


def test_accounts_are_scoped_to_their_user_and_two_users_may_share_a_number(
    factory, cipher, user, other_user
):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    with factory.create() as uow:
        mine = uow.accounts.create(
            NewAccount(uow.owners.create(new_owner(user)).owner_id, "LEUMI", number, None)
        )
        theirs = uow.accounts.create(
            NewAccount(uow.owners.create(new_owner(other_user)).owner_id, "LEUMI", number, None)
        )
        uow.commit()

    with factory.create() as uow:
        assert uow.accounts.find_by_number("LEUMI", number, user.user_id) == (mine,)
        assert uow.accounts.find_by_number("LEUMI", number, other_user.user_id) == (theirs,)
        assert uow.accounts.get(theirs.account_id, user.user_id) is None
        assert uow.accounts.list_all(other_user.user_id) == (theirs,)


def test_account_lookup_without_a_bank_code_is_null_safe(factory, cipher, user):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner(user))
        unbanked = uow.accounts.create(NewAccount(owner.owner_id, None, number, None))
        banked = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", number, None))

        assert uow.accounts.find_by_number(None, number, user.user_id) == (unbanked,)
        assert uow.accounts.find_by_number("LEUMI", number, user.user_id) == (banked,)


def test_the_same_bank_and_account_number_cannot_be_stored_twice_for_one_user(
    factory, cipher, user
):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner(user))
        uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", number, None))
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", number, None))


def test_one_number_without_a_bank_code_may_belong_to_several_accounts(factory, cipher, user):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    with factory.create() as uow:
        first_owner = uow.owners.create(new_owner(user, "Dana Levi"))
        second_owner = uow.owners.create(new_owner(user, "Avi Cohen"))
        first = uow.accounts.create(NewAccount(first_owner.owner_id, None, number, "one"))
        second = uow.accounts.create(NewAccount(second_owner.owner_id, None, number, "two"))
        same_owner = uow.accounts.create(NewAccount(first_owner.owner_id, None, number, "three"))
        uow.commit()

    with factory.create() as uow:
        assert uow.accounts.find_by_number(None, number, user.user_id) == (
            first,
            second,
            same_owner,
        )


def test_lookup_with_a_bank_code_never_returns_accounts_of_unknown_banks(factory, cipher, user):
    number = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner(user))
        uow.accounts.create(NewAccount(owner.owner_id, None, number, None))
        known = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", number, None))
        uow.accounts.create(NewAccount(owner.owner_id, "HAPOALIM", number, None))

        assert uow.accounts.find_by_number("LEUMI", number, user.user_id) == (known,)


def test_an_owner_has_at_most_one_account_without_a_bank_or_number(factory, user):
    with factory.create() as uow:
        owner = uow.owners.create(new_owner(user, "Dana Levi"))
        other = uow.owners.create(new_owner(user, "Avi Cohen"))
        uow.accounts.create(NewAccount(owner.owner_id, None, None, "cash"))
        uow.accounts.create(NewAccount(other.owner_id, None, None, "cash"))
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.accounts.create(NewAccount(owner.owner_id, None, None, "other"))


def test_cards_of_one_claim_are_all_stored_and_found_in_id_order(factory, cipher, user):
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    with factory.create() as uow:
        first_owner = uow.owners.create(new_owner(user, "Dana Levi"))
        second_owner = uow.owners.create(new_owner(user, "Avi Cohen"))
        first_account = uow.accounts.create(NewAccount(first_owner.owner_id, "LEUMI", None, None))
        second_account = uow.accounts.create(NewAccount(second_owner.owner_id, "LEUMI", None, None))
        first = uow.cards.create(new_card(uow, user, first_owner, first_account, last4))
        second = uow.cards.create(new_card(uow, user, second_owner, second_account, last4, "b"))
        uow.commit()

    with factory.create() as uow:
        lookup = CardLookup(IssuerCode.MAX, last4, user_id=user.user_id)
        assert uow.cards.find_candidates(lookup) == (first, second)
        assert uow.cards.get(second.card_id, user.user_id) == second


def test_card_candidates_narrow_by_issuer_account_owner_and_ciphertext(factory, cipher, user):
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner(user, "Dana Levi"))
        other_owner = uow.owners.create(new_owner(user, "Avi Cohen"))
        account = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", None, None))
        other_account = uow.accounts.create(NewAccount(other_owner.owner_id, "LEUMI", None, None))
        mine = uow.cards.create(new_card(uow, user, owner, account, last4))
        theirs = uow.cards.create(new_card(uow, user, other_owner, other_account, last4))
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


def test_another_users_cards_are_never_candidates(factory, seed_claimed_card, other_user, cipher):
    mine = seed_claimed_card(last4="4396")
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")

    with factory.create() as uow:
        assert (
            uow.cards.find_candidates(CardLookup(IssuerCode.MAX, last4, user_id=mine.user_id)) != ()
        )
        assert (
            uow.cards.find_candidates(CardLookup(IssuerCode.MAX, last4, user_id=other_user.user_id))
            == ()
        )
        assert uow.cards.get(mine.card_id, other_user.user_id) is None


def test_a_card_cannot_belong_to_an_owner_other_than_its_accounts_owner(factory, cipher, user):
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner(user, "Dana Levi"))
        other_owner = uow.owners.create(new_owner(user, "Avi Cohen"))
        account = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", None, None))

        with pytest.raises(PersistenceError) as raised:
            uow.cards.create(new_card(uow, user, other_owner, account, last4))

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_a_card_for_an_unknown_account_is_rejected(factory, cipher, user):
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner(user))
        claim = uow.claims.create_or_get(user.user_id, IssuerCode.MAX, last4)

        with pytest.raises(PersistenceError):
            uow.cards.create(
                NewCard(
                    owner.owner_id, AccountId(99999), IssuerCode.MAX, last4, None, claim.claim_id
                )
            )


def test_a_card_without_an_ownership_claim_is_rejected(factory, cipher, user):
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    with factory.create() as uow:
        owner = uow.owners.create(new_owner(user))
        account = uow.accounts.create(NewAccount(owner.owner_id, "LEUMI", None, None))

        with pytest.raises(PersistenceError):
            uow.cards.create(
                NewCard(owner.owner_id, account.account_id, IssuerCode.MAX, last4, None)
            )


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
