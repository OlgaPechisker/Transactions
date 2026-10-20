import pytest

from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.ids import AccountId, CardId, OwnerId
from bank_transactions.domain.models import (
    AccountHint,
    CardHint,
    CardHintKey,
    CardIdentityHint,
    OwnerHint,
)
from bank_transactions.identity.cli_resolver import CliIdentityResolver
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import (
    AccountChoice,
    CardCandidate,
    CreateAccount,
    CreateCard,
    CreateOwner,
    IdentityIssue,
    IdentityQuestion,
    OwnerChoice,
    UseAccount,
    UseCard,
    UseOwner,
)

ACCOUNT_NUMBER = "8917221038"


class ScriptedConsole:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.output: list[str] = []

    def read(self, prompt: str) -> str:
        self.output.append(prompt)
        if not self.answers:
            raise AssertionError("the resolver asked more questions than the script answers")
        return self.answers.pop(0)

    def write(self, text: str) -> None:
        self.output.append(text)

    @property
    def transcript(self) -> str:
        return "\n".join(self.output)

    def resolver(self) -> CliIdentityResolver:
        return CliIdentityResolver(read=self.read, write=self.write)


def hint(owner: str | None = "Dana Levi", account: bool = True) -> CardIdentityHint:
    return CardIdentityHint(
        CardHint(CardHintKey(IssuerCode.CAL, "4396")),
        OwnerHint(owner) if owner else None,
        AccountHint("LEUMI", ACCOUNT_NUMBER) if account else None,
    )


def candidate(card_id: int, last4: str = "4396") -> CardCandidate:
    return CardCandidate(
        CardId(card_id), OwnerId(card_id), AccountId(card_id), IssuerCode.CAL, None, last4
    )


def question(**overrides) -> IdentityQuestion:
    return IdentityQuestion(
        **{"hint": hint(), "issue": IdentityIssue.NO_MATCHING_CARD, **overrides}
    )


def test_cli_resolver_is_an_identity_resolver():
    assert isinstance(CliIdentityResolver(), IdentityResolver)


def test_the_user_explicitly_selects_one_of_several_matching_cards():
    console = ScriptedConsole("2")

    selection = console.resolver().complete(
        question(issue=IdentityIssue.AMBIGUOUS_CARDS, candidates=(candidate(5), candidate(9)))
    )

    assert selection == UseCard(CardId(9))


def test_an_empty_answer_never_picks_the_first_match():
    console = ScriptedConsole("", "1")

    selection = console.resolver().complete(
        question(issue=IdentityIssue.AMBIGUOUS_CARDS, candidates=(candidate(5), candidate(9)))
    )

    assert selection == UseCard(CardId(5))
    assert console.answers == []


def test_an_out_of_range_card_number_is_asked_again():
    console = ScriptedConsole("7", "2")

    selection = console.resolver().complete(
        question(issue=IdentityIssue.AMBIGUOUS_CARDS, candidates=(candidate(5), candidate(9)))
    )

    assert selection == UseCard(CardId(9))


def test_new_owner_account_and_card_are_created_from_file_suggestions():
    console = ScriptedConsole("", "Checking", "Travel card")

    selection = console.resolver().complete(question())

    assert selection == CreateCard(
        owner=CreateOwner("Dana Levi"),
        account=CreateAccount("Checking"),
        display_name="Travel card",
    )


def test_a_typed_owner_name_overrides_the_file_suggestion():
    console = ScriptedConsole("Yossi Cohen", "", "")

    selection = console.resolver().complete(question())

    assert selection == CreateCard(
        owner=CreateOwner("Yossi Cohen"), account=CreateAccount(None), display_name=None
    )


def test_owner_name_is_required_when_the_file_has_no_suggestion():
    console = ScriptedConsole("", "Yossi Cohen", "", "")

    selection = console.resolver().complete(question(hint=hint(owner=None)))

    assert selection == CreateCard(
        owner=CreateOwner("Yossi Cohen"), account=CreateAccount(None), display_name=None
    )


def test_an_existing_owner_and_one_of_its_accounts_can_be_selected():
    owners = (OwnerChoice(OwnerId(1), "Dana Levi"), OwnerChoice(OwnerId(2), "Yossi Cohen"))
    accounts = (
        AccountChoice(AccountId(10), OwnerId(1), "LEUMI", None),
        AccountChoice(AccountId(11), OwnerId(2), "LEUMI", "Joint"),
        AccountChoice(AccountId(12), OwnerId(2), "HAPOALIM", None),
    )
    console = ScriptedConsole("2", "2", "")

    selection = console.resolver().complete(question(owners=owners, accounts=accounts))

    assert selection == CreateCard(
        owner=UseOwner(OwnerId(2)), account=UseAccount(AccountId(12)), display_name=None
    )


def test_only_accounts_of_the_chosen_owner_are_offered():
    owners = (OwnerChoice(OwnerId(1), "Dana Levi"), OwnerChoice(OwnerId(2), "Yossi Cohen"))
    accounts = (
        AccountChoice(AccountId(10), OwnerId(1), "LEUMI", "Dana account"),
        AccountChoice(AccountId(11), OwnerId(2), "LEUMI", "Yossi account"),
    )
    console = ScriptedConsole("1", "n", "", "")

    selection = console.resolver().complete(question(owners=owners, accounts=accounts))

    assert "Yossi account" not in console.transcript
    assert selection == CreateCard(
        owner=UseOwner(OwnerId(1)), account=CreateAccount(None), display_name=None
    )


def test_a_new_owner_can_only_get_a_new_account():
    owners = (OwnerChoice(OwnerId(1), "Dana Levi"),)
    accounts = (AccountChoice(AccountId(10), OwnerId(1), "LEUMI", "Dana account"),)
    console = ScriptedConsole("n", "", "", "")

    selection = console.resolver().complete(question(owners=owners, accounts=accounts))

    assert "Dana account" not in console.transcript
    assert selection == CreateCard(
        owner=CreateOwner("Dana Levi"), account=CreateAccount(None), display_name=None
    )


def test_determined_owner_and_account_leave_only_the_card_label_to_ask():
    console = ScriptedConsole("Backup card")
    determined_owner = OwnerChoice(OwnerId(1), "Dana Levi")
    determined_account = AccountChoice(AccountId(10), OwnerId(1), "LEUMI", None)

    selection = console.resolver().complete(
        question(determined_owner=determined_owner, determined_account=determined_account)
    )

    assert selection == CreateCard(owner=None, account=None, display_name="Backup card")


def test_a_determined_account_fixes_the_owner_to_the_account_owner():
    console = ScriptedConsole("")
    determined_account = AccountChoice(AccountId(10), OwnerId(4), "LEUMI", None)

    selection = console.resolver().complete(question(determined_account=determined_account))

    assert selection == CreateCard(owner=UseOwner(OwnerId(4)), account=None, display_name=None)


def test_a_determined_owner_still_asks_for_the_account():
    determined_owner = OwnerChoice(OwnerId(1), "Dana Levi")
    accounts = (AccountChoice(AccountId(10), OwnerId(1), "LEUMI", "Main"),)
    console = ScriptedConsole("1", "")

    selection = console.resolver().complete(
        question(determined_owner=determined_owner, accounts=accounts)
    )

    assert selection == CreateCard(owner=None, account=UseAccount(AccountId(10)), display_name=None)


@pytest.mark.parametrize("leaking_label", ["card 4396", "acct 8917221038", "891-7221038"])
def test_labels_that_contain_known_identifiers_are_rejected_and_asked_again(leaking_label):
    console = ScriptedConsole("", leaking_label, "Safe label", "")

    selection = console.resolver().complete(question())

    assert selection == CreateCard(
        owner=CreateOwner("Dana Levi"), account=CreateAccount("Safe label"), display_name=None
    )
    assert "card or account number" in console.transcript


def test_a_card_label_with_the_last_four_digits_is_rejected():
    console = ScriptedConsole("", "", "my 4396", "Wallet")

    selection = console.resolver().complete(question())

    assert selection == CreateCard(
        owner=CreateOwner("Dana Levi"), account=CreateAccount(None), display_name="Wallet"
    )


def test_a_leaking_file_suggestion_is_not_offered_as_the_owner_default():
    leaking = hint(owner="Dana 4396")
    console = ScriptedConsole("", "Dana Levi", "", "")

    selection = console.resolver().complete(question(hint=leaking))

    assert "Dana 4396" not in console.transcript
    assert selection.owner == CreateOwner("Dana Levi")  # type: ignore[union-attr]


def test_the_display_masks_cards_and_never_prints_the_account_number():
    owners = (OwnerChoice(OwnerId(1), "Dana Levi"),)
    accounts = (
        AccountChoice(AccountId(10), OwnerId(1), "LEUMI", "Main", account_number=ACCOUNT_NUMBER),
    )
    console = ScriptedConsole("1", "1", "")

    console.resolver().complete(question(owners=owners, accounts=accounts))
    other = ScriptedConsole("1")
    other.resolver().complete(
        question(issue=IdentityIssue.AMBIGUOUS_CARDS, candidates=(candidate(5, "1111"),))
    )

    assert ACCOUNT_NUMBER not in console.transcript
    assert "891-7221038" not in console.transcript
    assert "••••1111" in other.transcript
    assert "CAL" in other.transcript
