"""The import flow end to end against in-memory fakes: observable data, counts and audit state."""

import hashlib
from datetime import timedelta
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import (
    ImportStatus,
    IssuerCode,
    IssueSeverity,
    ReviewReason,
    RowIssueCode,
    TransactionType,
)
from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.issues import RowIssue, SourceLocation
from bank_transactions.domain.models import (
    CardHintKey,
    ParsedFxDetail,
    ParsedInstallmentPlan,
    ParsedInstallmentRef,
    StatementTotals,
)
from bank_transactions.domain.money import ILS, USD, Money, effective_fx_rate
from bank_transactions.persistence.commands import NewImportRun


def ils(value: str) -> Money:
    return Money(Decimal(value), ILS)


def issue(code, severity, row=2, message="a problem"):
    return RowIssue(SourceLocation(1, row), code, severity, message)


@pytest.fixture
def known_card(world):
    return world.seed_card()


def test_a_statement_is_imported_into_the_known_card_and_recorded(world, known_card, make_tx):
    world.statement(world.context(), world.result([make_tx(), make_tx(merchant_name_norm="mega")]))

    report = world.service().import_file(world.statement_file())

    assert report.status is ImportStatus.SUCCESS
    assert (report.rows_total, report.rows_imported, report.rows_duplicate) == (2, 2, 0)
    assert report.rows_skipped == 0
    assert report.dry_run is False
    stored = [a.transaction for a in world.stored]
    assert [t.card_id for t in stored] == [known_card.card_id] * 2
    assert {t.import_run_id for t in stored} == {report.run_id}
    (run,) = world.runs
    assert run.status is ImportStatus.SUCCESS
    assert run.outcome.rows_imported == 2
    assert run.outcome.issuer_code is report.issuer_code


def test_the_run_is_committed_as_running_before_the_workbook_is_opened(world, known_card):
    seen = []
    world.reader.on_open = lambda: seen.append([run.status for run in world.runs])

    world.service().import_file(world.statement_file())

    assert seen == [[ImportStatus.RUNNING]]


def test_the_audit_row_carries_the_file_hash_and_only_hash_based_labels(world, known_card):
    content = b"statement bytes"
    path = world.statement_file("Dana Levi 4396 דוח.xlsx", content)

    world.service().import_file(path)

    (run,) = world.runs
    assert run.new.file_sha256 == hashlib.sha256(content).hexdigest()
    assert run.new.file_path == f"sha256/{run.new.file_sha256}"
    assert run.new.file_name == f"{run.new.file_sha256[:12]}.xlsx"
    for label in (run.new.file_path, run.new.file_name):
        assert "Dana" not in label and "4396" not in label and str(world.tmp_path) not in label


def test_an_unusual_file_suffix_is_not_copied_into_the_audit_label(world, known_card):
    path = world.statement_file("statement.x l s!")

    world.service().import_file(path)

    (run,) = world.runs
    assert run.new.file_name == run.new.file_sha256[:12]


def test_rows_seen_but_not_imported_are_reported_as_skipped(world, known_card, make_tx):
    world.statement(
        world.context(), world.result([make_tx(), make_tx(merchant_name_norm="b")], rows_seen=7)
    )

    report = world.service().import_file(world.statement_file())

    assert (report.rows_total, report.rows_skipped, report.rows_imported) == (7, 5, 2)
    (run,) = world.runs
    assert (run.outcome.rows_total, run.outcome.rows_skipped) == (7, 5)


def test_reimporting_the_same_referenced_rows_adds_nothing(world, known_card, make_tx):
    world.statement(
        world.context(),
        world.result([make_tx(issuer_reference="V-1"), make_tx(issuer_reference="V-2")]),
    )
    service = world.service()
    service.import_file(world.statement_file())

    second = service.import_file(world.statement_file())

    assert (second.rows_imported, second.rows_duplicate, second.rows_skipped) == (0, 2, 0)
    assert second.status is ImportStatus.SUCCESS
    assert len(world.stored) == 2


def test_identical_repeated_reference_less_rows_are_kept_up_to_the_stored_count(
    world, known_card, make_tx
):
    service = world.service()
    path = world.statement_file()

    world.statement(world.context(), world.result([make_tx() for _ in range(3)]))
    first = service.import_file(path)
    world.statement(world.context(), world.result([make_tx() for _ in range(3)]))
    same = service.import_file(path)
    world.statement(world.context(), world.result([make_tx() for _ in range(4)]))
    one_more = service.import_file(path)

    assert (first.rows_imported, first.rows_duplicate) == (3, 0)
    assert (same.rows_imported, same.rows_duplicate) == (0, 3)
    assert (one_more.rows_imported, one_more.rows_duplicate) == (1, 3)
    assert len(world.stored) == 4


def test_a_reference_repeated_inside_one_file_is_imported_once(world, known_card, make_tx):
    world.statement(
        world.context(),
        world.result([make_tx(issuer_reference="V-1"), make_tx(issuer_reference="V-1")]),
    )

    report = world.service().import_file(world.statement_file())

    assert (report.rows_imported, report.rows_duplicate) == (1, 1)
    assert len(world.stored) == 1


def test_installments_sharing_a_reference_are_distinct_by_installment_number(
    world, known_card, make_tx
):
    first = make_tx(issuer_reference="V-1", installment=ParsedInstallmentRef(1, 3))
    second = make_tx(issuer_reference="V-1", installment=ParsedInstallmentRef(2, 3))
    world.statement(world.context(), world.result([first, second]))

    report = world.service().import_file(world.statement_file())

    assert (report.rows_imported, report.rows_duplicate) == (2, 0)
    assert [a.transaction.installment_number for a in world.stored] == [1, 2]


def test_rows_without_installment_numbers_share_a_reference_key(world, known_card, make_tx):
    world.statement(
        world.context(),
        world.result([make_tx(issuer_reference="V-1")]),
    )
    service = world.service()
    service.import_file(world.statement_file())
    world.statement(
        world.context(),
        world.result(
            [
                make_tx(issuer_reference="V-1"),
                make_tx(issuer_reference="V-1", installment=ParsedInstallmentRef(1, 3)),
            ]
        ),
    )

    report = service.import_file(world.statement_file())

    assert (report.rows_imported, report.rows_duplicate) == (1, 1)


def test_installment_plans_are_stored_once_and_linked_to_their_rows(
    world, known_card, make_tx, card_key
):
    plan = ParsedInstallmentPlan(
        card_key, "tv store", make_tx().transaction_date, ils("1680.00"), 5
    )
    row = make_tx(
        merchant_name_norm="tv store",
        transaction_type=TransactionType.INSTALLMENTS,
        installment=ParsedInstallmentRef(4, 5, plan_total=ils("1680.00")),
        installment_plan=plan,
        billing=ils("336.00"),
        original=ils("336.00"),
    )
    world.statement(world.context(), world.result([row], plans=[plan]))
    service = world.service()

    service.import_file(world.statement_file())
    service.import_file(world.statement_file())

    assert len(world.db.domain.plans) == 1
    (stored,) = world.stored
    assert stored.transaction.installment_plan_id == 1
    assert stored.transaction.installment_number == 4
    assert world.db.domain.plans[1].total_amount == Decimal("1680.00")


def test_foreign_details_and_review_reasons_are_stored_and_counted(world, known_card, make_tx):
    billing, original = ils("365.00"), Money(Decimal("100.00"), USD)
    foreign = make_tx(
        billing=billing,
        original=original,
        fx=ParsedFxDetail(
            Decimal("1.50"), None, effective_fx_rate(billing.amount, original.amount)
        ),
    )
    flagged = make_tx(
        merchant_name_norm="mystery",
        billing=Money(Decimal("5.00"), None),
        original=None,
        review_reasons=frozenset({ReviewReason.UNKNOWN_CURRENCY}),
    )
    world.statement(world.context(), world.result([foreign, flagged]))

    report = world.service().import_file(world.statement_file())

    assert report.rows_needing_review == 1
    first, second = world.stored
    assert first.foreign_detail.commission_amount == Decimal("1.50")
    assert first.foreign_detail.effective_fx_rate == Decimal("3.650000")
    assert second.review_reasons == {ReviewReason.UNKNOWN_CURRENCY}


def test_a_dry_run_reports_proposed_counts_and_commits_no_domain_data(world, make_tx):
    world.statement(world.context(), world.result([make_tx(), make_tx(merchant_name_norm="b")]))
    world.resolver.decide = lambda question: _create_everything()

    report = world.service().import_file(world.statement_file(), dry_run=True)

    assert report.dry_run is True
    assert (report.status, report.rows_imported) == (ImportStatus.SUCCESS, 2)
    assert world.stored == []
    assert world.db.domain.owners == {} and world.db.domain.cards == {}
    (run,) = world.runs
    assert run.status is ImportStatus.SUCCESS
    assert run.outcome.rows_imported == 0
    assert run.error_summary.startswith("DRY RUN: nothing was committed")
    assert "2" in run.error_summary


def test_a_real_import_after_a_dry_run_still_imports_everything(world, make_tx):
    world.statement(world.context(), world.result([make_tx(), make_tx(merchant_name_norm="b")]))
    world.resolver.decide = lambda question: _create_everything()
    service = world.service()
    service.import_file(world.statement_file(), dry_run=True)

    report = service.import_file(world.statement_file())

    assert (report.rows_imported, report.rows_duplicate, report.dry_run) == (2, 0, False)
    assert len(world.stored) == 2
    assert len(world.db.domain.cards) == 1


def test_a_dry_run_that_needs_input_still_asks_and_discards_nothing_it_never_made(world, make_tx):
    world.statement(world.context(), world.result([make_tx()]))

    report = world.service().import_file(world.statement_file(), dry_run=True)

    assert report.status is ImportStatus.NEEDS_INPUT
    assert report.dry_run is True
    assert world.db.domain.cards == {}


def test_an_error_issue_makes_the_run_partial_and_keeps_every_issue(world, known_card, make_tx):
    problems = [
        issue(RowIssueCode.MALFORMED_ROW, IssueSeverity.ERROR, 5, "row is malformed"),
        issue(RowIssueCode.PENDING_TRANSACTION, IssueSeverity.INFO, 6, "pending"),
    ]
    world.statement(world.context(), world.result([make_tx()], issues=problems, rows_seen=3))

    report = world.service().import_file(world.statement_file())

    assert report.status is ImportStatus.PARTIAL
    assert report.issues == tuple(problems)
    (run,) = world.runs
    assert run.status is ImportStatus.PARTIAL
    assert "MALFORMED_ROW" in run.error_summary
    assert "row is malformed" not in run.error_summary


def test_a_pending_info_issue_alone_does_not_make_the_run_partial(world, known_card, make_tx):
    pending = issue(RowIssueCode.PENDING_TRANSACTION, IssueSeverity.INFO, 6, "pending")
    world.statement(world.context(), world.result([make_tx()], issues=[pending], rows_seen=2))

    report = world.service().import_file(world.statement_file())

    assert report.status is ImportStatus.SUCCESS
    assert report.rows_skipped == 1
    assert report.issues == (pending,)


def test_a_totals_mismatch_is_a_warning_not_a_failure_or_a_transaction_reason(
    world, known_card, make_tx
):
    declared = StatementTotals.of(ils("100.00"))
    world.statement(
        world.context(declared=declared),
        world.result([make_tx()], calculated=StatementTotals.of(ils("90.00"))),
    )

    report = world.service().import_file(world.statement_file())

    assert report.status is ImportStatus.SUCCESS
    assert report.rows_needing_review == 0
    mismatch = [i for i in report.issues if i.code is RowIssueCode.TOTALS_MISMATCH]
    assert len(mismatch) == 1 and mismatch[0].severity is IssueSeverity.WARNING
    assert any("ILS" in warning for warning in report.warnings)
    (run,) = world.runs
    assert "totals" in run.error_summary.lower()
    assert world.stored[0].review_reasons == frozenset()


def test_matching_totals_raise_no_warning(world, known_card, make_tx):
    totals = StatementTotals.of(ils("100.00"))
    world.statement(world.context(declared=totals), world.result([make_tx()], calculated=totals))

    report = world.service().import_file(world.statement_file())

    assert report.warnings == ()
    assert report.issues == ()


def test_missing_declared_totals_mean_the_check_is_unavailable_not_a_mismatch(
    world, known_card, make_tx
):
    world.statement(
        world.context(), world.result([make_tx()], calculated=StatementTotals.of(ils("100.00")))
    )

    report = world.service().import_file(world.statement_file())

    assert report.issues == ()
    assert any("not available" in warning for warning in report.warnings)
    assert report.status is ImportStatus.SUCCESS


def test_totals_are_checked_against_the_file_even_when_every_row_is_a_duplicate(
    world, known_card, make_tx
):
    totals = StatementTotals.of(ils("100.00"))
    world.statement(
        world.context(declared=totals),
        world.result([make_tx(issuer_reference="V-1")], calculated=totals),
    )
    service = world.service()
    service.import_file(world.statement_file())

    report = service.import_file(world.statement_file())

    assert report.rows_imported == 0
    assert report.warnings == ()


def test_stuck_runs_older_than_an_hour_are_failed_before_the_new_run_starts(world, known_card):
    with world.factory.create() as uow:
        stale = uow.import_runs.start_run(
            NewImportRun("sha256/a", "a.xlsx", "a" * 64, world.clock.now - timedelta(hours=2))
        )
        recent = uow.import_runs.start_run(
            NewImportRun("sha256/b", "b.xlsx", "b" * 64, world.clock.now - timedelta(minutes=30))
        )
        uow.commit()

    report = world.service().import_file(world.statement_file())

    assert world.db.audit.runs[stale].status is ImportStatus.FAILED
    assert "interrupted" in world.db.audit.runs[stale].error_summary
    assert world.db.audit.runs[recent].status is ImportStatus.RUNNING
    assert any("1" in warning and "interrupted" in warning for warning in report.warnings)


def test_audit_timestamps_come_from_the_injected_clock(world, known_card, make_tx):
    world.statement(world.context(), world.result([make_tx()]))
    world.parser.on_read_transactions = lambda: world.clock.advance(seconds=5)

    world.service().import_file(world.statement_file())

    (run,) = world.runs
    assert run.new.started_at == world.clock.now - timedelta(seconds=5)
    assert run.outcome.finished_at == world.clock.now


def test_a_missing_file_fails_before_any_run_exists(world, known_card):
    with pytest.raises(ParseError):
        world.service().import_file(world.tmp_path / "missing.xlsx")

    assert world.runs == []
    assert world.reader.opened == []


def test_a_parser_reporting_fewer_rows_than_transactions_is_a_contract_failure(
    world, known_card, make_tx
):
    world.statement(world.context(), world.result([make_tx(), make_tx()], rows_seen=1))

    with pytest.raises(ParseError):
        world.service().import_file(world.statement_file())

    assert world.stored == []
    (run,) = world.runs
    assert run.status is ImportStatus.FAILED


def test_a_transaction_for_a_card_the_context_never_named_is_a_contract_failure(
    world, known_card, make_tx
):
    stranger = make_tx(card_key=CardHintKey(IssuerCode.MAX, "0000"))
    world.statement(world.context(), world.result([stranger]))

    with pytest.raises(ParseError):
        world.service().import_file(world.statement_file())

    assert world.stored == []
    assert world.runs[0].status is ImportStatus.FAILED


def _create_everything():
    from bank_transactions.identity.models import CreateAccount, CreateCard, CreateOwner

    return CreateCard(CreateOwner("Dana Levi"), CreateAccount())
