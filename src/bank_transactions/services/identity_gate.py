"""Binds every source card of a statement to a stored card inside one live unit of work.

The gate never picks "the first" of several matches: a card is reused only when exactly one stored
card is consistent with what the file says, otherwise the resolver is asked an explicit question.
All lookups use ciphertext; plaintext identifiers exist only in memory. Nothing is committed here.
"""

from dataclasses import dataclass

from bank_transactions.domain.enums import IdentifierType
from bank_transactions.domain.errors import IdentityUnresolvedError, InvalidIdentitySelectionError
from bank_transactions.domain.ids import AccountId, CardId, IdentifierToken, OwnerId
from bank_transactions.domain.keys import OwnerMatchKey
from bank_transactions.domain.models import CardHintKey, CardIdentityHint, StatementContext
from bank_transactions.domain.text import contains_identifier
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import (
    AccountChoice,
    CardCandidate,
    CreateAccount,
    CreateCard,
    CreateOwner,
    IdentityIssue,
    IdentityQuestion,
    IdentitySelection,
    OwnerChoice,
    ResolvedCards,
    UseAccount,
    UseCard,
    UseOwner,
)
from bank_transactions.persistence.commands import NewAccount, NewCard, NewOwner
from bank_transactions.persistence.interfaces import UnitOfWork
from bank_transactions.persistence.records import (
    AccountRecord,
    CardLookup,
    CardRecord,
    OwnerRecord,
)
from bank_transactions.ports.identifier_protector import IdentifierProtector


def _invalid(reason: str) -> InvalidIdentitySelectionError:
    return InvalidIdentitySelectionError(reason)


@dataclass(frozen=True)
class _Evidence:
    """What the stored data says about one source card, given the file's hints."""

    hint: CardIdentityHint
    last4_enc: IdentifierToken
    number_enc: IdentifierToken | None
    owner: OwnerRecord | None
    accounts: tuple[AccountRecord, ...]
    candidates: tuple[CardRecord, ...]

    @property
    def owner_named(self) -> bool:
        return self.hint.owner is not None

    def consistent_cards(self) -> tuple[CardRecord, ...]:
        return tuple(card for card in self.candidates if self._agrees(card))

    def _agrees(self, card: CardRecord) -> bool:
        if self.owner_named and (self.owner is None or card.owner_id != self.owner.owner_id):
            return False
        if self.number_enc is None:
            return True
        return card.account_id in {account.account_id for account in self.accounts}

    def contradicts_owner(self) -> bool:
        """The named owner exists, the account number exists, yet never for that owner."""
        if self.owner is None or not self.accounts:
            return False
        return all(account.owner_id != self.owner.owner_id for account in self.accounts)

    def numbered_accounts(self) -> tuple[AccountRecord, ...]:
        if self.owner_named and self.owner is None:
            return ()
        if self.owner is None:
            return self.accounts
        return tuple(a for a in self.accounts if a.owner_id == self.owner.owner_id)


class _Lookup:
    """Reads stored identity data for one source card."""

    def __init__(self, protector: IdentifierProtector, uow: UnitOfWork) -> None:
        self._protector = protector
        self._uow = uow

    def evidence(self, hint: CardIdentityHint) -> _Evidence:
        key = hint.card.key
        last4_enc = self._protector.encrypt(IdentifierType.CARD_LAST4, key.last4)
        account_hint = hint.account
        number = account_hint.account_number if account_hint else None
        number_enc = (
            self._protector.encrypt(IdentifierType.ACCOUNT_NUMBER, number) if number else None
        )
        owner = self._owner(hint)
        accounts = self._accounts(hint, number_enc)
        lookup = CardLookup(key.issuer_code, last4_enc)
        candidates = self._uow.cards.find_candidates(lookup)
        return _Evidence(hint, last4_enc, number_enc, owner, accounts, candidates)

    def determined_account(self, evidence: _Evidence) -> AccountRecord | None:
        if evidence.number_enc is not None:
            usable = evidence.numbered_accounts()
            return usable[0] if len(usable) == 1 else None
        if evidence.owner is None:
            return None
        owned = [a for a in self._uow.accounts.list_all() if a.owner_id == evidence.owner.owner_id]
        return owned[0] if len(owned) == 1 else None

    def determined_owner(
        self, evidence: _Evidence, account: AccountRecord | None
    ) -> OwnerRecord | None:
        if evidence.owner_named:
            return evidence.owner
        return self._uow.owners.get(account.owner_id) if account else None

    def _owner(self, hint: CardIdentityHint) -> OwnerRecord | None:
        if hint.owner is None:
            return None
        return self._uow.owners.find_by_match_key(
            OwnerMatchKey.from_display_name(hint.owner.display_name)
        )

    def _accounts(
        self, hint: CardIdentityHint, number_enc: IdentifierToken | None
    ) -> tuple[AccountRecord, ...]:
        if number_enc is None or hint.account is None:
            return ()
        return self._uow.accounts.find_by_number(hint.account.bank_code, number_enc)

    def question(
        self,
        evidence: _Evidence,
        issue: IdentityIssue,
        candidates: tuple[CardRecord, ...],
        account: AccountRecord | None,
        owner: OwnerRecord | None,
    ) -> IdentityQuestion:
        return IdentityQuestion(
            evidence.hint,
            issue,
            owners=tuple(
                OwnerChoice(o.owner_id, o.display_name) for o in self._uow.owners.list_all()
            ),
            accounts=tuple(self._account_choice(a) for a in self._uow.accounts.list_all()),
            candidates=tuple(self._candidate(c) for c in candidates),
            determined_owner=OwnerChoice(owner.owner_id, owner.display_name) if owner else None,
            determined_account=self._account_choice(account) if account else None,
        )

    def _account_choice(self, account: AccountRecord) -> AccountChoice:
        number = account.account_number_enc
        plain = self._protector.decrypt(IdentifierType.ACCOUNT_NUMBER, number) if number else None
        return AccountChoice(
            account.account_id, account.owner_id, account.bank_code, account.display_name, plain
        )

    def _candidate(self, card: CardRecord) -> CardCandidate:
        last4 = self._protector.decrypt(IdentifierType.CARD_LAST4, card.last4_enc)
        return CardCandidate(
            card.card_id,
            card.owner_id,
            card.account_id,
            card.issuer_code,
            card.display_name,
            last4,
        )


class _Applier:
    """Validates a resolver's selection against the question and persists any creations."""

    def __init__(
        self,
        uow: UnitOfWork,
        protector: IdentifierProtector,
        secrets: tuple[str, ...],
    ) -> None:
        self._uow = uow
        self._protector = protector
        self._secrets = secrets

    def apply(
        self,
        selection: IdentitySelection,
        evidence: _Evidence,
        question: IdentityQuestion,
    ) -> CardId:
        if isinstance(selection, UseCard):
            return self._use_card(selection, question)
        if isinstance(selection, CreateCard):
            return self._create_card(selection, evidence, question)
        raise _invalid("the selection is not a supported identity answer")

    @staticmethod
    def _use_card(selection: UseCard, question: IdentityQuestion) -> CardId:
        if selection.card_id not in {card.card_id for card in question.candidates}:
            raise _invalid("the selected card was not offered for this source card")
        return selection.card_id

    def _create_card(
        self, selection: CreateCard, evidence: _Evidence, question: IdentityQuestion
    ) -> CardId:
        self._reject_identifiers(selection.display_name)
        owner = self._owner(selection, question)
        account = self._account(selection, evidence, question, owner)
        card = NewCard(
            owner.owner_id,
            account.account_id,
            evidence.hint.card.key.issuer_code,
            evidence.last4_enc,
            selection.display_name,
        )
        return self._uow.cards.create(card).card_id

    def _owner(self, selection: CreateCard, question: IdentityQuestion) -> OwnerRecord:
        determined = question.determined_owner
        chosen = selection.owner
        if determined is not None:
            if chosen is not None and chosen != UseOwner(determined.owner_id):
                raise _invalid("the owner is already determined for this card")
            return self._existing_owner(determined.owner_id)
        if chosen is None:
            raise _invalid("an owner is required for a new card")
        if isinstance(chosen, UseOwner):
            return self._existing_owner(chosen.owner_id)
        return self._created_owner(chosen)

    def _existing_owner(self, owner_id: OwnerId) -> OwnerRecord:
        record = self._uow.owners.get(owner_id)
        if record is None:
            raise _invalid("the selected owner does not exist")
        return record

    def _created_owner(self, chosen: CreateOwner) -> OwnerRecord:
        self._reject_identifiers(chosen.display_name)
        key = OwnerMatchKey.from_display_name(chosen.display_name)
        return self._uow.owners.find_by_match_key(key) or self._uow.owners.create(
            NewOwner(chosen.display_name, key)
        )

    def _account(
        self,
        selection: CreateCard,
        evidence: _Evidence,
        question: IdentityQuestion,
        owner: OwnerRecord,
    ) -> AccountRecord:
        determined = question.determined_account
        chosen = selection.account
        if determined is not None:
            if chosen is not None and chosen != UseAccount(determined.account_id):
                raise _invalid("the account is already determined for this card")
            return self._existing_account(determined.account_id, owner, evidence)
        if chosen is None:
            raise _invalid("an account is required for a new card")
        if isinstance(chosen, UseAccount):
            return self._existing_account(chosen.account_id, owner, evidence)
        return self._created_account(chosen, evidence, owner)

    def _existing_account(
        self, account_id: AccountId, owner: OwnerRecord, evidence: _Evidence
    ) -> AccountRecord:
        record = self._uow.accounts.get(account_id)
        if record is None:
            raise _invalid("the selected account does not exist")
        if record.owner_id != owner.owner_id:
            raise _invalid("the selected account belongs to another owner")
        stated = evidence.number_enc
        if stated is not None and record.account_number_enc not in (None, stated):
            raise _invalid("the selected account contradicts the account number in the file")
        return record

    def _created_account(
        self, chosen: CreateAccount, evidence: _Evidence, owner: OwnerRecord
    ) -> AccountRecord:
        self._reject_identifiers(chosen.display_name)
        hint = evidence.hint.account
        bank_code = hint.bank_code if hint else None
        number = evidence.number_enc
        if number is not None:
            same_number = self._uow.accounts.find_by_number(bank_code, number)
            return self._reuse_or_create(same_number, owner, bank_code, number, chosen)
        return self._create_account(owner, bank_code, None, chosen)

    def _reuse_or_create(
        self,
        same_number: tuple[AccountRecord, ...],
        owner: OwnerRecord,
        bank_code: str | None,
        number: IdentifierToken,
        chosen: CreateAccount,
    ) -> AccountRecord:
        owned = [a for a in same_number if a.owner_id == owner.owner_id]
        if len(owned) == 1 and len(same_number) == 1:
            return owned[0]
        if same_number:
            raise _invalid("that account number already belongs to another or several records")
        return self._create_account(owner, bank_code, number, chosen)

    def _create_account(
        self,
        owner: OwnerRecord,
        bank_code: str | None,
        number: IdentifierToken | None,
        chosen: CreateAccount,
    ) -> AccountRecord:
        return self._uow.accounts.create(
            NewAccount(owner.owner_id, bank_code, number, chosen.display_name)
        )

    def _reject_identifiers(self, label: str | None) -> None:
        if label and contains_identifier(label, self._secrets):
            raise _invalid("a label must not contain a card or account number")


class IdentityGate:
    """Resolves all cards of one statement; raises IdentityUnresolvedError listing every card
    that still needs input after the others were bound."""

    def __init__(self, protector: IdentifierProtector, resolver: IdentityResolver) -> None:
        self._protector = protector
        self._resolver = resolver

    def resolve(self, context: StatementContext, uow: UnitOfWork) -> ResolvedCards:
        lookup = _Lookup(self._protector, uow)
        applier = _Applier(uow, self._protector, statement_identifiers(context))
        hints = context.identity_hints()
        resolved: dict[CardHintKey, CardId] = {}
        unresolved: list[CardHintKey] = []
        for position, hint in enumerate(hints):
            try:
                resolved[hint.card.key] = self._bind(hint, lookup, applier)
            except IdentityUnresolvedError:
                unresolved.append(hint.card.key)
            except EOFError:
                unresolved.extend(rest.card.key for rest in hints[position:])
                break
        if unresolved:
            raise IdentityUnresolvedError(unresolved)
        return ResolvedCards(resolved)

    def _bind(self, hint: CardIdentityHint, lookup: _Lookup, applier: _Applier) -> CardId:
        evidence = lookup.evidence(hint)
        consistent = evidence.consistent_cards()
        if len(consistent) == 1:
            return consistent[0].card_id
        account = lookup.determined_account(evidence)
        owner = lookup.determined_owner(evidence, account)
        if len(consistent) > 1:
            issue, offered = IdentityIssue.AMBIGUOUS_CARDS, consistent
        elif evidence.candidates or evidence.contradicts_owner():
            issue, offered = IdentityIssue.CONFLICTING_HINTS, evidence.candidates
        else:
            issue, offered = IdentityIssue.NO_MATCHING_CARD, ()
        question = lookup.question(evidence, issue, offered, account, owner)
        selection = self._resolver.complete(question)
        return applier.apply(selection, evidence, question)


def statement_identifiers(context: StatementContext) -> tuple[str, ...]:
    """Every card last 4 and account number the file names; used only to keep them out of text."""
    numbers = [
        hint.account.account_number
        for hint in context.identity_hints()
        if hint.account and hint.account.account_number
    ]
    return tuple(dict.fromkeys([*(card.key.last4 for card in context.cards), *numbers]))
