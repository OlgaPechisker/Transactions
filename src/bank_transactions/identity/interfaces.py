from abc import ABC, abstractmethod

from bank_transactions.identity.models import IdentityQuestion, IdentitySelection


class IdentityResolver(ABC):
    """Answers one unresolved card at a time. It never touches a database: it picks from the
    choices in the question or asks for new records, and the service persists the answer inside
    its single atomic identity gate. It must not auto-pick the first of several matches, and
    may answer owner/account separately from the card (see CreateCard). Non-interactive
    implementations raise IdentityUnresolvedError([question.hint.card.key])."""

    @abstractmethod
    def complete(self, question: IdentityQuestion) -> IdentitySelection: ...
