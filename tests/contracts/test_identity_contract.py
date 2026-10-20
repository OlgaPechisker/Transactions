import dataclasses

import pytest

from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.errors import (
    BankTransactionsError,
    IdentityUnresolvedError,
    InvalidIdentitySelectionError,
)
from bank_transactions.domain.ids import AccountId, CardId, OwnerId
from bank_transactions.domain.models import AccountHint, CardHint, CardHintKey, OwnerHint
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import (
    AccountChoice,
    CardCandidate,
    CardIdentityHint,
    CreateAccount,
    CreateCard,
    CreateOwner,
    IdentityIssue,
    IdentityQuestion,
    OwnerChoice,
    ResolvedCardIdentity,
    ResolvedCards,
    UseAccount,
    UseCard,
    UseOwner,
)

KEY = CardHintKey(IssuerCode.MAX, "4396")


def hint():
    return CardIdentityHint(
        card=CardHint(KEY, label="Platinum"),
        owner=OwnerHint("Dana Levi"),
        account=AccountHint("LEUMI", "891-7221038"),
    )


def test_resolved_cards_maps_each_source_card_to_a_database_card():
    other = CardHintKey(IssuerCode.MAX, "1111")
    resolved = ResolvedCards({KEY: CardId(5), other: CardId(6)})

    assert resolved.card_id_for(KEY) == CardId(5)
    assert resolved.card_id_for(other) == CardId(6)
    assert KEY in resolved
    assert len(resolved) == 2


def test_resolved_cards_is_independent_of_the_source_mapping():
    source = {KEY: CardId(5)}
    resolved = ResolvedCards(source)

    source[CardHintKey(IssuerCode.MAX, "2222")] = CardId(9)

    assert len(resolved) == 1


def test_resolved_cards_cannot_be_mutated():
    resolved = ResolvedCards({KEY: CardId(5)})

    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.cards = {}  # type: ignore[misc]


def test_unknown_source_card_is_a_key_error_that_does_not_leak_the_last4():
    with pytest.raises(KeyError) as caught:
        ResolvedCards({}).card_id_for(KEY)

    assert "4396" not in str(caught.value)


def test_selection_names_must_not_be_blank():
    with pytest.raises(ValueError):
        CreateOwner("  ")


def test_card_creation_may_leave_owner_and_account_to_the_determined_ones():
    selection = CreateCard()

    assert selection.owner is None
    assert selection.account is None
    assert selection.display_name is None


def test_card_creation_can_pick_existing_owner_and_new_account():
    selection = CreateCard(owner=UseOwner(OwnerId(1)), account=CreateAccount(display_name="Joint"))

    assert selection.owner == UseOwner(OwnerId(1))
    assert selection.account == CreateAccount("Joint")


def test_existing_card_selection_names_the_card():
    assert UseCard(CardId(3)).card_id == CardId(3)
    assert UseAccount(AccountId(2)).account_id == AccountId(2)


def test_question_carries_choices_and_what_is_already_determined():
    owner = OwnerChoice(OwnerId(1), "Dana Levi")
    account = AccountChoice(AccountId(2), OwnerId(1), "LEUMI", None, account_number="8917221038")
    candidate = CardCandidate(CardId(3), OwnerId(1), AccountId(2), IssuerCode.MAX, None, "4396")

    question = IdentityQuestion(
        hint=hint(),
        issue=IdentityIssue.AMBIGUOUS_CARDS,
        owners=[owner],
        accounts=[account],
        candidates=[candidate],
        determined_owner=owner,
    )

    assert question.owners == (owner,)
    assert question.candidates == (candidate,)
    assert question.determined_account is None


def test_question_and_choices_never_show_identifiers_in_their_repr():
    account = AccountChoice(AccountId(2), OwnerId(1), "LEUMI", None, account_number="8917221038")
    candidate = CardCandidate(CardId(3), OwnerId(1), AccountId(2), IssuerCode.MAX, None, "4396")
    question = IdentityQuestion(
        hint(), IdentityIssue.NO_MATCHING_CARD, (), (account,), (candidate,)
    )

    text = repr(question)

    assert "4396" not in text
    assert "8917221038" not in text
    assert "7221038" not in text


def test_resolved_identity_names_owner_account_and_card():
    identity = ResolvedCardIdentity(CardId(3), OwnerId(1), AccountId(2))

    assert (identity.card_id, identity.owner_id, identity.account_id) == (3, 1, 2)


def test_unresolved_identity_error_lists_cards_without_leaking_last4():
    error = IdentityUnresolvedError([KEY])

    assert error.keys == (KEY,)
    assert "4396" not in str(error)
    assert isinstance(error, BankTransactionsError)


def test_invalid_selection_error_is_a_domain_error():
    assert issubclass(InvalidIdentitySelectionError, BankTransactionsError)


def test_identity_resolver_contract_is_one_question_per_card():
    assert IdentityResolver.__abstractmethods__ == {"complete"}
