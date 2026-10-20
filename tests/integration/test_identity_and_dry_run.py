"""Dry runs, identity questions and rollback against a real PostgreSQL schema."""

from bank_transactions.domain.enums import ImportStatus, IssuerCode
from bank_transactions.domain.errors import IdentityUnresolvedError
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.models import IdentityIssue, UseCard

DOMAIN_TABLES = (
    "owners",
    "accounts",
    "cards",
    "installment_plans",
    "transactions",
    "transaction_review_reasons",
    "foreign_transaction_details",
)


def test_a_dry_run_changes_no_domain_data_but_still_leaves_an_audit_record(
    service, examples, scalar, sql
):
    report = service.import_file(examples["max_yearly"], dry_run=True)

    assert (report.status, report.dry_run) == (ImportStatus.SUCCESS, True)
    assert (report.rows_total, report.rows_imported) == (2169, 2169)
    for table in DOMAIN_TABLES:
        assert scalar(f"SELECT count(*) FROM {table}") == 0, table
    (run,) = sql("SELECT status, rows_total, rows_imported, error_summary FROM import_runs")
    assert run[:3] == ("SUCCESS", 2169, 0)
    assert run[3].startswith("DRY RUN: nothing was committed")


def test_a_real_import_after_a_dry_run_behaves_like_a_first_import(service, examples, scalar):
    service.import_file(examples["max_monthly"], dry_run=True)

    report = service.import_file(examples["max_monthly"])

    assert (report.rows_imported, report.rows_duplicate) == (158, 0)
    assert scalar("SELECT count(*) FROM transactions") == 158
    assert scalar("SELECT count(*) FROM cards") == 2


def test_a_dry_run_of_a_known_file_reports_everything_as_duplicate(service, examples, scalar):
    service.import_file(examples["max_monthly"])

    report = service.import_file(examples["max_monthly"], dry_run=True)

    assert (report.rows_imported, report.rows_duplicate) == (0, 158)
    assert scalar("SELECT count(*) FROM transactions") == 158


def test_unresolved_cards_need_input_and_nothing_is_written(
    make_service, refusing_resolver, examples, scalar, sql
):
    resolver = refusing_resolver
    report = make_service(resolver).import_file(examples["max_monthly"])

    assert report.status is ImportStatus.NEEDS_INPUT
    assert len(report.unresolved_cards) == 2
    assert len(resolver.questions) == 2
    assert (report.rows_imported, report.rows_total) == (0, 0)
    for table in DOMAIN_TABLES:
        assert scalar(f"SELECT count(*) FROM {table}") == 0, table
    (run,) = sql("SELECT status, rows_imported FROM import_runs")
    assert run == ("NEEDS_INPUT", 0)


def test_seeded_identities_are_reused_without_any_question(
    make_service, refusing_resolver, examples, seed_card, scalar
):
    resolver = refusing_resolver
    first = seed_card("4396", IssuerCode.MAX)
    second = seed_card("2023", IssuerCode.MAX)

    report = make_service(resolver).import_file(examples["max_monthly"])

    assert (report.status, report.rows_imported) == (ImportStatus.SUCCESS, 158)
    assert resolver.questions == []
    assert scalar("SELECT count(*) FROM cards") == 2
    assert (
        scalar(
            "SELECT count(*) FROM transactions WHERE card_id IN (:a, :b)",
            a=first.card_id,
            b=second.card_id,
        )
        == 158
    )


def test_one_unknown_card_blocks_the_whole_file_and_creates_nothing(
    make_service, refusing_resolver, examples, seed_card, scalar
):
    resolver = refusing_resolver
    seed_card("4396", IssuerCode.MAX)

    report = make_service(resolver).import_file(examples["max_monthly"])

    assert report.status is ImportStatus.NEEDS_INPUT
    assert len(report.unresolved_cards) == 1
    assert scalar("SELECT count(*) FROM cards") == 1
    assert scalar("SELECT count(*) FROM transactions") == 0


def test_two_stored_cards_with_the_same_last4_are_never_resolved_by_picking_the_first(
    make_service, refusing_resolver, examples, seed_card, scalar
):
    refusing = refusing_resolver
    seed_card("4396", IssuerCode.MAX, owner_name="First Owner")
    chosen = seed_card("4396", IssuerCode.MAX, owner_name="Second Owner")
    seed_card("2023", IssuerCode.MAX, owner_name="First Owner")

    blocked = make_service(refusing).import_file(examples["max_monthly"])

    assert blocked.status is ImportStatus.NEEDS_INPUT
    (question,) = refusing.questions
    assert question.issue is IdentityIssue.AMBIGUOUS_CARDS
    assert len(question.candidates) == 2

    class PickSecond(IdentityResolver):
        def complete(self, question):
            if len(question.candidates) < 2:
                raise IdentityUnresolvedError([question.hint.card.key])
            return UseCard(question.candidates[1].card_id)

    report = make_service(PickSecond()).import_file(examples["max_monthly"])

    assert report.status is ImportStatus.SUCCESS
    assert scalar("SELECT count(*) FROM transactions WHERE card_id = :c", c=chosen.card_id) > 0


def test_a_stored_card_owned_by_someone_else_than_the_file_names_is_a_conflict(
    make_service, refusing_resolver, examples, seed_card, scalar
):
    resolver = refusing_resolver
    seed_card("9462", IssuerCode.ISRACARD, owner_name="Somebody Else Entirely")

    report = make_service(resolver).import_file(examples["isracard"])

    assert report.status is ImportStatus.NEEDS_INPUT
    assert resolver.questions[0].issue is IdentityIssue.CONFLICTING_HINTS
    assert scalar("SELECT count(*) FROM transactions") == 0


def test_the_file_owner_name_is_used_when_the_answer_creates_the_owner(service, examples, scalar):
    service.import_file(examples["isracard"])

    again = service.import_file(examples["isracard"])

    assert again.status is ImportStatus.SUCCESS
    assert scalar("SELECT count(*) FROM owners") == 1
    assert scalar("SELECT count(*) FROM cards") == 1
