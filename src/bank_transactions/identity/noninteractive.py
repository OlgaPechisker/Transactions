from typing import NoReturn

from bank_transactions.domain.errors import IdentityUnresolvedError
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import IdentityQuestion


class NonInteractiveResolver(IdentityResolver):
    """For runs without a user: every question is left unanswered, so the service ends the run
    as NEEDS_INPUT without any identity or domain change."""

    def complete(self, question: IdentityQuestion) -> NoReturn:
        raise IdentityUnresolvedError([question.hint.card.key])
