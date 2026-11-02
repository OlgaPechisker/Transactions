"""Users, card-key claims and the public ids and constraints that tie them to cards."""

import re

import pytest
from sqlalchemy.exc import IntegrityError

from bank_transactions.domain.enums import IdentifierType, IssuerCode
from bank_transactions.domain.errors import DataAlreadyOwnedError, PersistenceError
from bank_transactions.persistence.commands import NewUser

pytestmark = pytest.mark.postgres

PUBLIC_ID = r"{prefix}_[a-z2-7]{{26}}"


def new_user(email="dana@example.com", key=None, name="Dana"):
    return NewUser(email, key or email.strip().casefold(), name, "hash-1")


def test_a_created_user_is_found_by_the_normalised_email_and_by_id(factory):
    with factory.create() as uow:
        created = uow.users.create(new_user("Dana@Example.com", "dana@example.com"))
        uow.commit()

    with factory.create() as uow:
        by_email = uow.users.find_by_email_key("dana@example.com")
        by_id = uow.users.get(created.user_id)
        missing = uow.users.find_by_email_key("nobody@example.com")

    assert by_email == by_id == created
    assert created.email == "Dana@Example.com"
    assert re.fullmatch(PUBLIC_ID.format(prefix="usr"), created.public_id)
    assert missing is None


def test_two_users_cannot_share_an_email(factory):
    with factory.create() as uow:
        uow.users.create(new_user())
        uow.commit()

    with factory.create() as uow, pytest.raises(PersistenceError):
        uow.users.create(new_user("DANA@example.com", "dana@example.com"))


def test_a_changed_password_hash_is_stored_and_a_locked_user_can_still_be_read(factory):
    with factory.create() as uow:
        user = uow.users.create(new_user())
        uow.commit()

    with factory.create() as uow:
        uow.users.lock(user.user_id)
        uow.users.update_password_hash(user.user_id, "hash-2")
        uow.commit()

    with factory.create() as uow:
        assert uow.users.get(user.user_id).password_hash == "hash-2"


def test_claiming_a_card_key_twice_returns_the_same_claim(factory, seed_user, cipher):
    user = seed_user()
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")

    with factory.create() as uow:
        first = uow.claims.create_or_get(user.user_id, IssuerCode.MAX, last4)
        uow.commit()
    with factory.create() as uow:
        again = uow.claims.create_or_get(user.user_id, IssuerCode.MAX, last4)
        found = uow.claims.find(IssuerCode.MAX, last4)
        locked = uow.claims.lock(IssuerCode.MAX, last4)

    assert first == again == found == locked
    assert first.user_id == user.user_id


def test_a_card_key_claimed_by_one_user_cannot_be_claimed_by_another(factory, seed_user, cipher):
    owner, other = seed_user(), seed_user("other@example.com", "Other")
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    with factory.create() as uow:
        uow.claims.create_or_get(owner.user_id, IssuerCode.MAX, last4)
        uow.commit()

    with factory.create() as uow, pytest.raises(DataAlreadyOwnedError) as raised:
        uow.claims.create_or_get(other.user_id, IssuerCode.MAX, last4)

    assert "other@example.com" not in str(raised.value)
    assert owner.email not in str(raised.value)


def test_the_same_last4_of_another_issuer_is_a_different_claim(factory, seed_user, cipher):
    owner, other = seed_user(), seed_user("other@example.com", "Other")
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")

    with factory.create() as uow:
        mine = uow.claims.create_or_get(owner.user_id, IssuerCode.MAX, last4)
        theirs = uow.claims.create_or_get(other.user_id, IssuerCode.ISRACARD, last4)

    assert mine.claim_id != theirs.claim_id


def test_a_card_cannot_point_at_a_claim_of_a_different_last4(seed_claimed_card, cipher, sql):
    card = seed_claimed_card(last4="4396")
    other_last4 = bytes(cipher.encrypt(IdentifierType.CARD_LAST4, "9999"))

    with pytest.raises(IntegrityError, match="fk_cards_claim"):
        sql(
            "INSERT INTO cards (public_id, owner_id, account_id, user_id, claim_id, issuer_code,"
            " last4_enc) VALUES ('crd_x', :o, :a, :u, :c, 'MAX', :l)",
            o=card.owner_id,
            a=card.account_id,
            u=card.user_id,
            c=card.claim_id,
            l=other_last4,
        )


def test_a_card_cannot_use_another_users_claim(seed_claimed_card, seed_user, cipher, sql):
    mine = seed_claimed_card(last4="4396")
    other = seed_user("other@example.com", "Other")
    theirs = seed_claimed_card(user=other, last4="1111", owner_name="Other Owner")
    last4 = bytes(cipher.encrypt(IdentifierType.CARD_LAST4, "1111"))

    with pytest.raises(IntegrityError, match="fk_cards_claim"):
        sql(
            "INSERT INTO cards (public_id, owner_id, account_id, user_id, claim_id, issuer_code,"
            " last4_enc) VALUES ('crd_y', :o, :a, :u, :c, 'MAX', :l)",
            o=mine.owner_id,
            a=mine.account_id,
            u=mine.user_id,
            c=theirs.claim_id,
            l=last4,
        )


def test_cards_and_transactions_get_opaque_public_ids(seed_card, seed_transaction, sql):
    card = seed_card()
    seed_transaction(card)

    (card_public,) = sql("SELECT public_id FROM cards")[0]
    (transaction_public,) = sql("SELECT public_id FROM transactions")[0]

    assert re.fullmatch(PUBLIC_ID.format(prefix="crd"), card_public)
    assert re.fullmatch(PUBLIC_ID.format(prefix="txn"), transaction_public)


def test_two_users_may_each_have_the_same_owner_name_and_account_number(
    seed_claimed_card, seed_user
):
    first = seed_claimed_card(last4="1111")
    second = seed_claimed_card(user=seed_user("other@example.com", "Other"), last4="2222")

    assert first.owner_id != second.owner_id
    assert first.account_id != second.account_id
