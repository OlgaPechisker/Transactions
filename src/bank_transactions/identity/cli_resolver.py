"""Interactive resolver: the user explicitly picks or creates owner, account and card."""

from collections.abc import Callable, Sequence

from bank_transactions.domain.models import CardIdentityHint
from bank_transactions.domain.text import contains_identifier
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import (
    AccountChoice,
    AccountSelection,
    CardCandidate,
    CreateAccount,
    CreateCard,
    CreateOwner,
    IdentityQuestion,
    IdentitySelection,
    OwnerChoice,
    OwnerSelection,
    UseAccount,
    UseCard,
    UseOwner,
)

_NEW = "n"


class _IdentifierGuard:
    """Rejects typed text that would copy the card or account number from the file into a label."""

    def __init__(self, hint: CardIdentityHint) -> None:
        account_number = hint.account.account_number if hint.account else None
        self._identifiers = tuple(item for item in (hint.card.key.last4, account_number) if item)

    def leaks(self, text: str) -> bool:
        return contains_identifier(text, self._identifiers)


class _Console:
    def __init__(self, read: Callable[[str], str], write: Callable[[str], None]) -> None:
        self._read = read
        self._write = write

    def say(self, text: str) -> None:
        self._write(text)

    def ask(self, prompt: str) -> str:
        return self._read(prompt).strip()

    def choose(self, prompt: str, count: int) -> int | None:
        """A zero-based index, or None when the user wants a new record; never a default."""
        while True:
            answer = self.ask(prompt).lower()
            if answer == _NEW:
                return None
            if answer.isdigit() and 1 <= int(answer) <= count:
                return int(answer) - 1
            self._write(f"Enter a number from 1 to {count}, or '{_NEW}' for a new one.")

    def ask_text(self, prompt: str, guard: _IdentifierGuard, default: str | None = None) -> str:
        while True:
            answer = self.ask(prompt) or (default or "")
            if answer and guard.leaks(answer):
                self._write("That text contains a card or account number; enter another one.")
                continue
            return answer

    def ask_required_text(
        self, prompt: str, guard: _IdentifierGuard, default: str | None = None
    ) -> str:
        while True:
            answer = self.ask_text(prompt, guard, default)
            if answer:
                return answer
            self._write("A value is required.")


class CliIdentityResolver(IdentityResolver):
    def __init__(
        self,
        read: Callable[[str], str] = input,
        write: Callable[[str], None] = print,
    ) -> None:
        self._console = _Console(read, write)

    def complete(self, question: IdentityQuestion) -> IdentitySelection:
        guard = _IdentifierGuard(question.hint)
        self._announce(question)
        existing = self._pick_card(question.candidates)
        if existing is not None:
            return UseCard(existing.card_id)
        owner = self._owner(question, guard)
        account = self._account(question, owner, guard)
        label = self._console.ask_text("Card label (optional): ", guard) or None
        return CreateCard(owner=owner, account=account, display_name=label)

    def _announce(self, question: IdentityQuestion) -> None:
        key = question.hint.card.key
        self._console.say(
            f"Identity needed for {key.issuer_code.value} card ••••{key.last4} "
            f"({question.issue.value})"
        )

    def _pick_card(self, candidates: Sequence[CardCandidate]) -> CardCandidate | None:
        if not candidates:
            return None
        for number, card in enumerate(candidates, start=1):
            self._console.say(f"{number}) {_describe_card(card)}")
        prompt = "Existing card number, or 'n' for a new card: "
        index = self._console.choose(prompt, len(candidates))
        return None if index is None else candidates[index]

    def _owner(self, question: IdentityQuestion, guard: _IdentifierGuard) -> OwnerSelection | None:
        if question.determined_owner is not None:
            return None
        if question.determined_account is not None:
            return UseOwner(question.determined_account.owner_id)
        picked = self._pick_owner(question.owners)
        if picked is not None:
            return UseOwner(picked.owner_id)
        suggestion = question.hint.owner.display_name if question.hint.owner else None
        usable = None if suggestion is not None and guard.leaks(suggestion) else suggestion
        prompt = f"Owner name [{usable}]: " if usable else "Owner name: "
        return CreateOwner(self._console.ask_required_text(prompt, guard, usable))

    def _pick_owner(self, owners: Sequence[OwnerChoice]) -> OwnerChoice | None:
        if not owners:
            return None
        for number, owner in enumerate(owners, start=1):
            self._console.say(f"{number}) {owner.display_name}")
        index = self._console.choose("Owner number, or 'n' for a new owner: ", len(owners))
        return None if index is None else owners[index]

    def _account(
        self,
        question: IdentityQuestion,
        owner: OwnerSelection | None,
        guard: _IdentifierGuard,
    ) -> AccountSelection | None:
        if question.determined_account is not None:
            return None
        picked = self._pick_account(_accounts_of(question, owner))
        if picked is not None:
            return UseAccount(picked.account_id)
        label = self._console.ask_text("Account label (optional): ", guard) or None
        return CreateAccount(label)

    def _pick_account(self, accounts: Sequence[AccountChoice]) -> AccountChoice | None:
        if not accounts:
            return None
        for number, account in enumerate(accounts, start=1):
            self._console.say(f"{number}) {_describe_account(account)}")
        index = self._console.choose("Account number, or 'n' for a new account: ", len(accounts))
        return None if index is None else accounts[index]


def _accounts_of(
    question: IdentityQuestion, owner: OwnerSelection | None
) -> tuple[AccountChoice, ...]:
    if question.determined_owner is not None:
        owner_id = question.determined_owner.owner_id
    elif isinstance(owner, UseOwner):
        owner_id = owner.owner_id
    else:
        return ()
    return tuple(account for account in question.accounts if account.owner_id == owner_id)


def _describe_card(card: CardCandidate) -> str:
    label = f" {card.display_name}" if card.display_name else ""
    return f"{card.issuer_code.value} ••••{card.last4}{label} (owner {card.owner_id})"


def _describe_account(account: AccountChoice) -> str:
    bank = account.bank_code or "no bank"
    label = f" {account.display_name}" if account.display_name else ""
    return f"{bank}{label} (account {account.account_id})"
