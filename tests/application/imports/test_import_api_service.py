"""The API import pipeline against in-memory fakes, branch by branch in the order the contract
fixes: upload checks, audit start, workbook/format, claims, selections, questions, import."""

import hashlib
import io
import zipfile
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import (
    ImportStatus,
    IssuerCode,
    IssueSeverity,
    ReviewReason,
    RowIssueCode,
)
from bank_transactions.domain.errors import (
    DataAlreadyOwnedError,
    FileTooLargeError,
    ImportInProgressError,
    NotFoundError,
    ParseError,
    UnknownFormatError,
    UnreadableWorkbookError,
    UnsupportedFileError,
    ValidationFailedError,
)
from bank_transactions.domain.issues import RowIssue, SourceLocation
from bank_transactions.domain.models import CardHintKey, ParsedFxDetail, StatementTotals
from bank_transactions.domain.money import ILS, Money
from bank_transactions.identity.models import IdentityIssue
from bank_transactions.services.import_api_service import (
    MAX_UPLOAD_BYTES,
    SelectionAction,
    StatementUpload,
)

QUESTION = "q-MAX-4396"


def field_errors(error: ValidationFailedError) -> list[tuple[str, str]]:
    return [(e.field, e.code) for e in error.errors]


# --- the upload itself --------------------------------------------------------------------


def test_a_file_over_the_limit_is_too_large():
    with pytest.raises(FileTooLargeError):
        StatementUpload(b"x" * (MAX_UPLOAD_BYTES + 1))


@pytest.mark.parametrize("data", [b"", b"plain text", b"PK\x03\x04truncated"])
def test_bytes_that_are_not_a_zip_are_unsupported(data):
    with pytest.raises(UnsupportedFileError):
        StatementUpload(data)


def test_a_zip_that_is_not_a_workbook_is_unsupported():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", "hello")

    with pytest.raises(UnsupportedFileError):
        StatementUpload(buffer.getvalue())


def test_a_word_document_container_is_not_a_spreadsheet():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types><Override ContentType="application/vnd.openxmlformats-officedocument'
            '.wordprocessingml.document.main+xml"/></Types>',
        )

    with pytest.raises(UnsupportedFileError):
        StatementUpload(buffer.getvalue())


def test_selection_syntax_errors_are_reported_before_the_audit_starts(world, api):
    selections = (api.create_card(QUESTION), api.create_card(QUESTION))

    with pytest.raises(ValidationFailedError) as raised:
        api.run(selections)

    assert field_errors(raised.value) == [
        ("identitySelections[1].questionId", "DUPLICATE_QUESTION")
    ]
    assert world.runs == []


# --- workbook and format ------------------------------------------------------------------


def test_a_workbook_that_cannot_be_opened_is_unreadable_and_audited_as_failed(
    world, api, monkeypatch
):
    def broken(data):
        raise ParseError("not a workbook")

    monkeypatch.setattr(world.reader, "open_bytes", broken)

    with pytest.raises(UnreadableWorkbookError):
        api.run()

    (run,) = world.runs
    assert run.status is ImportStatus.FAILED


def test_an_unknown_statement_format_is_audited_as_failed(world, api):
    world.selector.unknown = True

    with pytest.raises(UnknownFormatError):
        api.run()

    (run,) = world.runs
    assert run.status is ImportStatus.FAILED


def test_a_recognised_statement_that_cannot_be_parsed_is_a_failed_result(world, api, make_tx):
    world.parser.context_failure = ParseError("the header row is missing")

    outcome = api.run()

    assert outcome.status is ImportStatus.FAILED
    assert outcome.issuer is IssuerCode.MAX
    assert (outcome.counts.read, outcome.counts.imported) == (0, 0)
    assert any("could not be read" in warning for warning in outcome.warnings)
    (run,) = world.runs
    assert run.status is ImportStatus.FAILED


def test_a_second_upload_of_a_file_being_imported_is_a_conflict(world, api):
    world.db.fail_start_run = ImportInProgressError("already being imported")

    with pytest.raises(ImportInProgressError):
        api.run()


# --- claims -------------------------------------------------------------------------------


def test_a_card_another_user_owns_is_a_conflict_even_in_a_dry_run(world, api, make_tx):
    api.seed_card_for_other_user("4396")
    world.statement(world.context(), world.result([make_tx()]))

    for dry_run in (True, False):
        with pytest.raises(DataAlreadyOwnedError):
            api.run(dry_run=dry_run)

    assert world.stored == []
    assert {c.user_id for c in world.db.domain.claims.values()} == {api.other_id}


def test_a_claim_conflict_wins_over_selection_errors(world, api, make_tx):
    api.seed_card_for_other_user("4396")
    world.statement(world.context(), world.result([make_tx()]))

    with pytest.raises(DataAlreadyOwnedError):
        api.run((api.use_card("q-MAX-0000", "crd_999"),))


# --- selections ---------------------------------------------------------------------------


def test_an_existing_card_the_caller_cannot_see_is_not_found(world, api, make_tx):
    others = api.seed_card_for_other_user("1111")
    world.statement(world.context(), world.result([make_tx()]))

    with pytest.raises(NotFoundError):
        api.run((api.use_card(QUESTION, api.public_id_of(others)),))

    assert world.stored == []


def test_a_question_the_file_never_asked_is_rejected(world, api, make_tx):
    world.statement(world.context(), world.result([make_tx()]))

    with pytest.raises(ValidationFailedError) as raised:
        api.run((api.create_card("q-MAX-9999"),))

    assert field_errors(raised.value) == [("identitySelections[0].questionId", "UNKNOWN_QUESTION")]


def test_an_own_card_that_does_not_match_the_file_is_an_invalid_selection(world, api, make_tx):
    elsewhere = world.seed_card(last4="1111")
    world.statement(world.context(), world.result([make_tx()]))

    with pytest.raises(ValidationFailedError) as raised:
        api.run((api.use_card(QUESTION, api.public_id_of(elsewhere.card_id)),))

    assert field_errors(raised.value) == [("identitySelections", "INVALID_SELECTION")]
    assert world.stored == []


# --- questions ----------------------------------------------------------------------------


def test_an_unknown_card_asks_a_question_and_commits_nothing(world, api, make_tx):
    world.statement(world.context(), world.result([make_tx()]))

    outcome = api.run()

    assert outcome.status is ImportStatus.NEEDS_INPUT
    (question,) = outcome.questions
    assert question.question_id == QUESTION
    assert question.reason is IdentityIssue.NO_MATCHING_CARD
    assert (question.issuer, question.last4, question.candidates) == (IssuerCode.MAX, "4396", ())
    assert question.allowed_actions == (SelectionAction.CREATE_CARD,)
    assert (world.stored, world.db.domain.claims, world.db.domain.cards) == ([], {}, {})
    assert world.parser.transaction_reads == 0


def test_questions_offer_only_the_callers_cards(world, api, make_tx):
    first = world.seed_card(owner="Dana Levi", account_number="111")
    second = world.seed_card(owner="Dana Mizrahi", account_number="222")
    api.seed_card_for_other_user("1111")
    world.statement(world.context(), world.result([make_tx()]))

    outcome = api.run()

    (question,) = outcome.questions
    assert question.reason is IdentityIssue.AMBIGUOUS_CARDS
    assert {c.public_id for c in question.candidates} == {
        api.public_id_of(first.card_id),
        api.public_id_of(second.card_id),
    }
    assert question.allowed_actions == (
        SelectionAction.USE_EXISTING_CARD,
        SelectionAction.CREATE_CARD,
    )
    assert all(c.last4 == "4396" for c in question.candidates)


def test_answering_with_an_existing_card_imports_into_it(world, api, make_tx):
    first = world.seed_card(owner="Dana Levi", account_number="111")
    world.seed_card(owner="Dana Mizrahi", account_number="222")
    world.statement(world.context(), world.result([make_tx()]))

    outcome = api.run((api.use_card(QUESTION, api.public_id_of(first.card_id)),))

    assert outcome.status is ImportStatus.SUCCESS
    assert outcome.questions == ()
    (stored,) = world.stored
    assert stored.transaction.card_id == first.card_id


# --- creating a card ----------------------------------------------------------------------


def test_creating_a_card_claims_it_for_the_caller_with_the_name_the_file_gives(world, api, make_tx):
    world.statement(world.context(owner="Dana Levi"), world.result([make_tx()]))

    outcome = api.run((api.create_card(QUESTION, "  Daily card  "),))

    assert outcome.status is ImportStatus.SUCCESS
    assert outcome.counts.imported == 1
    (claim,) = world.db.domain.claims.values()
    assert claim.user_id == world.user.user_id
    (card,) = world.db.domain.cards.values()
    assert card.display_name == "Daily card"
    assert [o.display_name for o in world.db.domain.owners.values()] == ["Dana Levi"]


def test_cards_without_an_owner_in_the_file_share_the_users_placeholder_owner(world, api, make_tx):
    world.statement(world.context(("4396",)), world.result([make_tx()]))
    api.run((api.create_card(QUESTION),))
    world.statement(world.context(("2222",)), world.result([make_tx(card_key=_key("2222"))]))

    api.run((api.create_card("q-MAX-2222"),), data=api.xlsx("second"))

    domain = world.db.domain
    assert len(domain.cards) == 2
    assert len(domain.placeholder_owners) == 1
    assert {c.owner_id for c in domain.cards.values()} == set(domain.placeholder_owners.values())
    assert len(domain.accounts) == 1
    (account,) = domain.accounts.values()
    assert (account.bank_code, account.account_number_enc) == (None, None)


def test_a_card_created_for_a_placeholder_owner_shows_the_users_name(world, api, make_tx):
    world.statement(world.context(), world.result([make_tx()]))
    api.run((api.create_card(QUESTION),))
    world.statement(world.context(), world.result([make_tx(merchant_name_norm="again")]))
    outcome = api.run(data=api.xlsx("again"))

    assert outcome.status is ImportStatus.SUCCESS
    visible = world.factory.create().visible_cards.list_visible(world.user.user_id)
    assert [v.owner_display_name for v in visible] == [None]


def test_a_dry_run_with_an_answer_commits_no_claim_card_or_transaction(world, api, make_tx):
    world.statement(world.context(owner="Dana Levi"), world.result([make_tx()]))

    outcome = api.run((api.create_card(QUESTION),), dry_run=True)

    assert (outcome.dry_run, outcome.status, outcome.counts.imported) == (
        True,
        ImportStatus.SUCCESS,
        1,
    )
    domain = world.db.domain
    assert (domain.claims, domain.cards, domain.owners, domain.accounts) == ({}, {}, {}, {})
    assert world.stored == []
    (run,) = world.runs
    assert run.status is ImportStatus.SUCCESS


def test_a_real_import_after_the_dry_run_creates_what_the_dry_run_only_proposed(
    world, api, make_tx
):
    world.statement(world.context(owner="Dana Levi"), world.result([make_tx()]))
    selections = (api.create_card(QUESTION),)
    api.run(selections, dry_run=True)

    outcome = api.run(selections)

    assert (outcome.status, outcome.counts.imported) == (ImportStatus.SUCCESS, 1)
    assert len(world.db.domain.claims) == 1
    assert len(world.stored) == 1


# --- the result ---------------------------------------------------------------------------


def test_the_result_carries_the_run_identity_hash_and_start_time(world, api, make_tx):
    world.seed_card()
    world.statement(world.context(), world.result([make_tx()]))
    data = api.xlsx("identity")

    outcome = api.run(data=data)

    assert outcome.sha256 == hashlib.sha256(data).hexdigest()
    assert outcome.public_id == "imp_1"
    assert outcome.started_at == api.clock.now()
    assert outcome.issuer is IssuerCode.MAX


def test_only_a_pending_currency_counts_as_needs_review(world, api, make_tx):
    world.seed_card()
    pending = make_tx(
        merchant_name_norm="abroad",
        billing=Money(Decimal("365.00"), ILS),
        original=Money(Decimal("100.00"), None),
        fx=ParsedFxDetail(Decimal("1.50"), None, Decimal("3.65")),
        review_reasons=frozenset({ReviewReason.UNKNOWN_CURRENCY}),
    )
    internal = make_tx(
        merchant_name_norm="fee", review_reasons=frozenset({ReviewReason.UNMATCHED_FOREIGN_FEE})
    )
    world.statement(world.context(), world.result([pending, internal, make_tx()]))

    outcome = api.run()

    assert outcome.counts.imported == 3
    assert outcome.counts.needs_review == 1


def test_a_file_level_issue_has_no_sheet_position_or_row(world, api, make_tx):
    world.seed_card()
    world.statement(
        world.context(declared=StatementTotals.of(Money(Decimal("100.00"), ILS))),
        world.result([make_tx()], calculated=StatementTotals.of(Money(Decimal("90.00"), ILS))),
    )

    outcome = api.run()

    (mismatch,) = [i for i in outcome.issues if i.code is RowIssueCode.TOTALS_MISMATCH]
    assert (mismatch.sheet_position, mismatch.row) == (None, None)
    assert mismatch.severity is IssueSeverity.WARNING


def test_a_row_issue_keeps_its_sheet_position_and_row(world, api, make_tx):
    world.seed_card()
    problem = RowIssue(
        SourceLocation(2, 7), RowIssueCode.MALFORMED_ROW, IssueSeverity.ERROR, "row is malformed"
    )
    world.statement(world.context(), world.result([make_tx()], issues=[problem], rows_seen=2))

    outcome = api.run()

    assert outcome.status is ImportStatus.PARTIAL
    (issue,) = outcome.issues
    assert (issue.sheet_position, issue.row) == (2, 7)


def _key(last4):
    return CardHintKey(IssuerCode.MAX, last4)
