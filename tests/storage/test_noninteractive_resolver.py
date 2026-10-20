import pytest

from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.errors import IdentityUnresolvedError
from bank_transactions.domain.models import CardHint, CardHintKey, CardIdentityHint
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import IdentityIssue, IdentityQuestion
from bank_transactions.identity.noninteractive import NonInteractiveResolver


def question_for(last4: str) -> IdentityQuestion:
    hint = CardIdentityHint(CardHint(CardHintKey(IssuerCode.MAX, last4)))
    return IdentityQuestion(hint, IdentityIssue.NO_MATCHING_CARD)


def test_non_interactive_resolver_is_an_identity_resolver():
    assert isinstance(NonInteractiveResolver(), IdentityResolver)


def test_it_reports_the_card_that_needs_input_without_choosing_anything():
    question = question_for("4396")

    with pytest.raises(IdentityUnresolvedError) as raised:
        NonInteractiveResolver().complete(question)

    assert raised.value.keys == (CardHintKey(IssuerCode.MAX, "4396"),)


def test_the_error_message_carries_no_card_digits():
    with pytest.raises(IdentityUnresolvedError) as raised:
        NonInteractiveResolver().complete(question_for("4396"))

    assert "4396" not in str(raised.value)
