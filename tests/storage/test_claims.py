"""Claims under real PostgreSQL: concurrent claimers of one card key, rolled-back claims and the
gate's claim guard."""

import threading

import pytest

from bank_transactions.domain.enums import IdentifierType, IssuerCode
from bank_transactions.domain.errors import DataAlreadyOwnedError, IdentityUnresolvedError
from bank_transactions.domain.models import CardHint, CardHintKey, StatementContext
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import IdentityQuestion, IdentitySelection
from bank_transactions.services.identity_gate import IdentityGate

pytestmark = pytest.mark.postgres


class NoQuestions(IdentityResolver):
    def complete(self, question: IdentityQuestion) -> IdentitySelection:
        raise IdentityUnresolvedError([question.hint.card.key])


def statement(*last4s: str) -> StatementContext:
    cards = tuple(CardHint(CardHintKey(IssuerCode.MAX, last4), None) for last4 in last4s)
    return StatementContext(IssuerCode.MAX, cards, None, None)


def test_two_users_racing_for_one_card_key_leave_exactly_one_owner(factory, seed_user, cipher):
    first, second = seed_user(), seed_user("other@example.com", "Other")
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    start = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def claim(name, user):
        start.wait()
        try:
            with factory.create() as uow:
                outcomes[name] = uow.claims.create_or_get(user.user_id, IssuerCode.MAX, last4)
                uow.commit()
        except DataAlreadyOwnedError as error:
            outcomes[name] = error

    threads = [
        threading.Thread(target=claim, args=("first", first)),
        threading.Thread(target=claim, args=("second", second)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    losers = [o for o in outcomes.values() if isinstance(o, DataAlreadyOwnedError)]
    assert len(outcomes) == 2 and len(losers) == 1
    with factory.create() as uow:
        stored = uow.claims.find(IssuerCode.MAX, last4)
    assert stored is not None
    assert stored.user_id in (first.user_id, second.user_id)


def test_a_claim_made_in_a_rolled_back_unit_of_work_is_gone(factory, seed_user, cipher):
    user = seed_user()
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")

    with factory.create() as uow:
        uow.claims.create_or_get(user.user_id, IssuerCode.MAX, last4)
        uow.rollback()

    with factory.create() as uow:
        assert uow.claims.find(IssuerCode.MAX, last4) is None


def test_lock_of_an_unclaimed_key_finds_nothing(factory, cipher):
    last4 = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")

    with factory.create() as uow:
        assert uow.claims.lock(IssuerCode.MAX, last4) is None


def test_the_claim_guard_refuses_a_card_another_user_claimed(
    factory, seed_claimed_card, seed_user, cipher
):
    seed_claimed_card(last4="4396")
    caller = seed_user("other@example.com", "Other")

    with factory.create() as uow, pytest.raises(DataAlreadyOwnedError) as raised:
        IdentityGate(cipher, NoQuestions()).guard_claims(
            statement("1111", "4396"), uow, caller.user_id
        )

    assert "seed.user@example.com" not in str(raised.value)


def test_the_claim_guard_lets_an_owner_and_unclaimed_keys_through(
    factory, seed_claimed_card, cipher
):
    card = seed_claimed_card(last4="4396")

    with factory.create() as uow:
        IdentityGate(cipher, NoQuestions()).guard_claims(
            statement("4396", "9999"), uow, card.user_id
        )
