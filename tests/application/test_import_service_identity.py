"""Identity binding through the import service: when a card is reused, asked about or refused."""

import pytest

from bank_transactions.domain.enums import IdentifierType, ImportStatus, IssuerCode
from bank_transactions.domain.errors import InvalidIdentitySelectionError
from bank_transactions.domain.models import AccountHint
from bank_transactions.identity.models import (
    CreateAccount,
    CreateCard,
    CreateOwner,
    IdentityIssue,
    UseAccount,
    UseCard,
    UseOwner,
)

NUMBER = "8917221038"


def create_dana(**overrides):
    return CreateCard(CreateOwner("Dana Levi"), CreateAccount(), **overrides)


# --- no answer available ------------------------------------------------------------------


def test_unknown_cards_need_input_and_nothing_is_parsed_or_persisted(world, make_tx):
    world.statement(world.context(("4396", "1111")), world.result([make_tx()]))

    report = world.service().import_file(world.statement_file())

    assert report.status is ImportStatus.NEEDS_INPUT
    assert report.unresolved_cards == ("MAX card 1 of 2", "MAX card 2 of 2")
    assert world.parser.transaction_reads == 0
    assert world.stored == []
    assert (world.db.domain.owners, world.db.domain.cards) == ({}, {})
    (run,) = world.runs
    assert run.status is ImportStatus.NEEDS_INPUT
    assert run.error_summary == "identity input required for 2 card(s)"
    assert len(world.resolver.questions) == 2


def test_the_report_and_audit_never_contain_the_unresolved_identifiers(world, make_tx):
    world.statement(
        world.context(("4396",), account=AccountHint("LEUMI", NUMBER)), world.result([])
    )

    report = world.service().import_file(world.statement_file())

    visible = repr(report) + repr(world.runs)
    assert "4396" not in visible and NUMBER not in visible


def test_a_partly_resolved_statement_rolls_back_every_identity_change(world, make_tx):
    world.statement(world.context(("4396", "1111")), world.result([make_tx()]))

    def answer(question):
        if question.hint.card.key.last4 == "4396":
            return create_dana()
        return world.resolver._refuse(question)

    world.resolver.decide = answer

    report = world.service().import_file(world.statement_file())

    assert report.status is ImportStatus.NEEDS_INPUT
    assert report.unresolved_cards == ("MAX card 2 of 2",)
    assert (world.db.domain.owners, world.db.domain.accounts, world.db.domain.cards) == (
        {},
        {},
        {},
    )
    assert world.parser.transaction_reads == 0


def test_end_of_input_leaves_this_and_the_remaining_cards_unresolved(world, make_tx):
    world.statement(world.context(("4396", "1111", "2222")), world.result([]))
    answers = iter([create_dana()])

    def answer(question):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    world.resolver.decide = answer

    report = world.service().import_file(world.statement_file())

    assert report.status is ImportStatus.NEEDS_INPUT
    assert report.unresolved_cards == ("MAX card 2 of 3", "MAX card 3 of 3")
    assert world.db.domain.cards == {}


def test_an_interrupt_fails_the_run_without_claiming_success_and_propagates(world, make_tx):
    world.statement(world.context(), world.result([make_tx()]))

    def answer(question):
        raise KeyboardInterrupt

    world.resolver.decide = answer

    with pytest.raises(KeyboardInterrupt):
        world.service().import_file(world.statement_file())

    (run,) = world.runs
    assert (run.status, run.error_summary) == (ImportStatus.FAILED, "import interrupted")
    assert world.db.domain.owners == {}


# --- reuse without asking ----------------------------------------------------------------


def test_a_unique_stored_card_is_reused_even_when_the_file_names_no_owner_or_account(
    world, make_tx
):
    card = world.seed_card()
    world.statement(world.context(), world.result([make_tx()]))

    world.service().import_file(world.statement_file())

    assert world.resolver.questions == []
    assert [a.transaction.card_id for a in world.stored] == [card.card_id]


def test_a_matching_owner_hint_picks_between_cards_sharing_issuer_and_last4(world, make_tx):
    world.seed_card(owner="Dana Levi", account_number="1111111")
    noa = world.seed_card(owner="Noa Cohen", account_number="2222222")
    world.statement(world.context(owner="  noa  COHEN "), world.result([make_tx()]))

    world.service().import_file(world.statement_file())

    assert world.resolver.questions == []
    assert world.stored[0].transaction.card_id == noa.card_id


def test_a_matching_account_number_picks_between_cards_sharing_issuer_and_last4(world, make_tx):
    world.seed_card(owner="Dana Levi", account_number="1111111")
    noa = world.seed_card(owner="Noa Cohen", account_number="2222222")
    world.statement(
        world.context(account=AccountHint("LEUMI", "222-2222")), world.result([make_tx()])
    )

    world.service().import_file(world.statement_file())

    assert world.resolver.questions == []
    assert world.stored[0].transaction.card_id == noa.card_id


# --- explicit questions ------------------------------------------------------------------


def test_several_matching_cards_are_never_resolved_by_taking_the_first(world, make_tx):
    first = world.seed_card(owner="Dana Levi", account_number="1111111")
    second = world.seed_card(owner="Noa Cohen", account_number="2222222")
    world.statement(world.context(), world.result([make_tx()]))
    world.resolver.decide = lambda question: UseCard(second.card_id)

    world.service().import_file(world.statement_file())

    (question,) = world.resolver.questions
    assert question.issue is IdentityIssue.AMBIGUOUS_CARDS
    assert [c.card_id for c in question.candidates] == [first.card_id, second.card_id]
    assert world.stored[0].transaction.card_id == second.card_id


def test_without_an_answer_several_matching_cards_need_input(world, make_tx):
    world.seed_card(owner="Dana Levi", account_number="1111111")
    world.seed_card(owner="Noa Cohen", account_number="2222222")
    world.statement(world.context(), world.result([make_tx()]))

    report = world.service().import_file(world.statement_file())

    assert report.status is ImportStatus.NEEDS_INPUT
    assert world.stored == []


def test_an_owner_who_differs_from_the_stored_card_is_a_conflict_with_no_card_to_reuse(
    world, make_tx
):
    world.seed_card(owner="Dana Levi")
    world.statement(world.context(owner="Noa Cohen"), world.result([make_tx()]))
    world.resolver.decide = lambda question: CreateCard(CreateOwner("Noa Cohen"), CreateAccount())

    world.service().import_file(world.statement_file())

    (question,) = world.resolver.questions
    assert question.issue is IdentityIssue.CONFLICTING_HINTS
    assert question.candidates == ()
    assert len(world.db.domain.cards) == 2
    assert world.stored[0].transaction.card_id == 2


def test_an_account_number_that_belongs_to_another_owner_than_the_named_one_is_a_conflict(
    world, make_tx
):
    world.seed_card(last4="1111", owner="Dana Levi", account_number=NUMBER)
    world.seed_card(last4="2222", owner="Noa Cohen", account_number="2222222")
    world.statement(
        world.context(owner="Noa Cohen", account=AccountHint("LEUMI", NUMBER)),
        world.result([]),
    )

    world.service().import_file(world.statement_file())

    (question,) = world.resolver.questions
    assert question.issue is IdentityIssue.CONFLICTING_HINTS


def test_a_known_account_number_settles_owner_and_account_for_a_new_card(world, make_tx):
    world.seed_card(last4="1111", owner="Dana Levi", account_number=NUMBER)
    world.statement(
        world.context(account=AccountHint("LEUMI", "891-7221038")), world.result([make_tx()])
    )
    world.resolver.decide = lambda question: CreateCard(display_name="Visa")

    world.service().import_file(world.statement_file())

    (question,) = world.resolver.questions
    assert question.issue is IdentityIssue.NO_MATCHING_CARD
    assert (question.determined_owner.display_name, question.determined_account.account_id) == (
        "Dana Levi",
        1,
    )
    assert question.determined_account.account_number == NUMBER
    created = world.db.domain.cards[2]
    assert (created.owner_id, created.account_id, created.display_name) == (1, 1, "Visa")
    assert len(world.db.domain.accounts) == 1


def test_an_account_number_shared_by_several_unknown_bank_accounts_settles_nothing(world, make_tx):
    world.seed_card(last4="1111", owner="Dana Levi", account_number=NUMBER, bank_code=None)
    world.seed_card(last4="2222", owner="Noa Cohen", account_number=NUMBER, bank_code=None)
    world.statement(world.context(account=AccountHint(None, NUMBER)), world.result([]))

    world.service().import_file(world.statement_file())

    (question,) = world.resolver.questions
    assert question.determined_owner is None and question.determined_account is None
    assert len(question.accounts) == 2


def test_a_named_owner_with_one_account_settles_it_only_when_the_file_gives_no_number(
    world, make_tx
):
    world.seed_card(last4="1111", owner="Dana Levi", account_number=NUMBER)
    service = world.service()

    world.statement(world.context(owner="Dana Levi"), world.result([]))
    service.import_file(world.statement_file())
    world.statement(
        world.context(owner="Dana Levi", account=AccountHint("LEUMI", "5550001")), world.result([])
    )
    service.import_file(world.statement_file())

    without_number, with_number = world.resolver.questions
    assert without_number.determined_account is not None
    assert with_number.determined_owner is not None and with_number.determined_account is None


def test_a_bank_code_alone_is_not_evidence_for_a_stored_card(world, make_tx):
    card = world.seed_card(account_number="5550001", bank_code="LEUMI")
    world.statement(world.context(account=AccountHint("POALIM")), world.result([make_tx()]))

    world.service().import_file(world.statement_file())

    assert world.resolver.questions == []
    assert world.stored[0].transaction.card_id == card.card_id


# --- what a creation stores -------------------------------------------------------------


def test_created_identity_is_stored_as_ciphertext_and_reused_next_time(world, make_tx):
    world.statement(
        world.context(owner="Dana Levi", account=AccountHint("LEUMI", NUMBER)),
        world.result([make_tx()]),
    )
    world.resolver.decide = lambda question: create_dana(display_name="Dana's card")
    service = world.service()

    service.import_file(world.statement_file())
    service.import_file(world.statement_file())

    (card,) = world.db.domain.cards.values()
    (account,) = world.db.domain.accounts.values()
    assert card.last4_enc == world.token(IdentifierType.CARD_LAST4, "4396")
    assert account.account_number_enc == world.token(IdentifierType.ACCOUNT_NUMBER, NUMBER)
    assert (account.bank_code, card.issuer_code) == ("LEUMI", IssuerCode.MAX)
    stored = repr(world.db.domain.owners) + repr(world.db.domain.accounts) + repr(card)
    assert "4396" not in stored and NUMBER not in stored
    assert len(world.resolver.questions) == 1


def test_a_second_card_of_the_same_file_reuses_the_owner_and_account_just_created(world, make_tx):
    world.statement(
        world.context(("4396", "1111"), owner="Dana Levi"),
        world.result([make_tx()]),
    )
    world.resolver.decide = lambda question: (
        create_dana() if question.determined_owner is None else CreateCard()
    )

    report = world.service().import_file(world.statement_file())

    assert report.status is ImportStatus.SUCCESS
    assert len(world.db.domain.owners) == 1 and len(world.db.domain.accounts) == 1
    assert [c.account_id for c in world.db.domain.cards.values()] == [1, 1]


def test_creating_an_owner_with_a_matching_name_reuses_the_stored_owner(world, make_tx):
    world.seed_card(last4="1111", owner="Dana Levi")
    world.statement(world.context(), world.result([make_tx()]))
    world.resolver.decide = lambda question: CreateCard(
        CreateOwner(" dana   LEVI"), CreateAccount()
    )

    world.service().import_file(world.statement_file())

    assert len(world.db.domain.owners) == 1


def test_an_existing_owner_and_account_can_be_chosen(world, make_tx):
    world.seed_card(last4="1111", owner="Dana Levi", account_number="5550001")
    world.statement(world.context(), world.result([make_tx()]))
    world.resolver.decide = lambda question: CreateCard(UseOwner(1), UseAccount(1))

    world.service().import_file(world.statement_file())

    assert world.stored[0].transaction.card_id == 2
    assert len(world.db.domain.owners) == 1


# --- refusing bad answers ----------------------------------------------------------------


@pytest.mark.parametrize(
    "selection",
    [
        UseCard(99),
        CreateCard(UseOwner(99), CreateAccount()),
        CreateCard(CreateOwner("Dana 4396"), CreateAccount()),
        CreateCard(CreateOwner("Dana Levi"), CreateAccount("main 4396")),
        CreateCard(CreateOwner("Dana Levi"), CreateAccount(), display_name="card 4396"),
        CreateCard(CreateOwner("Dana Levi"), CreateAccount(), display_name="acct 891-7221038"),
        CreateCard(None, CreateAccount()),
        CreateCard(CreateOwner("Dana Levi"), None),
    ],
    ids=[
        "card-not-offered",
        "unknown-owner",
        "owner-leaks-last4",
        "account-label-leaks-last4",
        "card-label-leaks-last4",
        "card-label-leaks-account",
        "no-owner",
        "no-account",
    ],
)
def test_an_answer_the_question_did_not_allow_fails_the_run_and_stores_nothing(
    world, make_tx, selection
):
    world.statement(world.context(account=AccountHint("LEUMI", NUMBER)), world.result([make_tx()]))
    world.resolver.decide = lambda question: selection

    with pytest.raises(InvalidIdentitySelectionError):
        world.service().import_file(world.statement_file())

    (run,) = world.runs
    assert run.status is ImportStatus.FAILED
    assert "InvalidIdentitySelectionError" in run.error_summary
    assert "4396" not in run.error_summary and NUMBER not in run.error_summary
    assert (world.db.domain.owners, world.db.domain.cards, world.stored) == ({}, {}, [])


def test_an_account_of_another_owner_cannot_be_attached_to_the_chosen_owner(world, make_tx):
    world.seed_card(last4="1111", owner="Dana Levi", account_number="5550001")
    world.statement(world.context(), world.result([make_tx()]))
    world.resolver.decide = lambda question: CreateCard(CreateOwner("Noa Cohen"), UseAccount(1))

    with pytest.raises(InvalidIdentitySelectionError):
        world.service().import_file(world.statement_file())

    assert len(world.db.domain.owners) == 1


def test_an_account_whose_number_contradicts_the_file_cannot_be_chosen(world, make_tx):
    world.seed_card(last4="1111", owner="Dana Levi", account_number="5550001")
    world.statement(world.context(account=AccountHint("LEUMI", NUMBER)), world.result([make_tx()]))
    world.resolver.decide = lambda question: CreateCard(UseOwner(1), UseAccount(1))

    with pytest.raises(InvalidIdentitySelectionError):
        world.service().import_file(world.statement_file())


def test_a_determined_owner_cannot_be_replaced_by_the_answer(world, make_tx):
    world.seed_card(last4="1111", owner="Dana Levi", account_number="5550001")
    world.seed_card(last4="2222", owner="Noa Cohen", account_number="6660001")
    world.statement(world.context(owner="Dana Levi"), world.result([make_tx()]))
    world.resolver.decide = lambda question: CreateCard(UseOwner(2), None)

    with pytest.raises(InvalidIdentitySelectionError):
        world.service().import_file(world.statement_file())


def test_an_account_number_already_owned_by_someone_else_cannot_be_created_again(world, make_tx):
    world.seed_card(last4="1111", owner="Dana Levi", account_number=NUMBER)
    world.statement(
        world.context(owner="Noa Cohen", account=AccountHint("LEUMI", NUMBER)),
        world.result([make_tx()]),
    )
    world.resolver.decide = lambda question: CreateCard(CreateOwner("Noa Cohen"), CreateAccount())

    with pytest.raises(InvalidIdentitySelectionError):
        world.service().import_file(world.statement_file())

    assert len(world.db.domain.owners) == 1
