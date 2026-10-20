import dataclasses

import pytest

from bank_transactions.domain import issues as domain_issues
from bank_transactions.domain.enums import ImportStatus, IssuerCode
from bank_transactions.domain.ids import ImportRunId
from bank_transactions.parsing.interfaces import StatementParser
from bank_transactions.ports.identifier_protector import IdentifierProtector
from bank_transactions.ports.parser_selector import ParserSelector
from bank_transactions.ports.workbook_reader import WorkbookReader
from bank_transactions.services import report


def make_report(**overrides):
    values = {
        "run_id": ImportRunId(1),
        "status": ImportStatus.SUCCESS,
        "issuer_code": IssuerCode.MAX,
        "rows_total": 10,
        "rows_imported": 7,
        "rows_duplicate": 1,
        "rows_skipped": 2,
        "rows_needing_review": 1,
    }
    return report.ImportReport(**{**values, **overrides})


def test_report_re_exports_the_domain_issue_types():
    assert report.RowIssue is domain_issues.RowIssue
    assert report.SourceLocation is domain_issues.SourceLocation
    assert report.RowIssueCode.PENDING_TRANSACTION == "PENDING_TRANSACTION"
    assert report.IssueSeverity.ERROR == "ERROR"


def test_report_defaults_are_empty_and_not_a_dry_run():
    result = make_report()

    assert result.issues == ()
    assert result.warnings == ()
    assert result.unresolved_cards == ()
    assert result.dry_run is False


def test_report_freezes_its_collections():
    result = make_report(warnings=["totals differ"], unresolved_cards=["MAX card 1"])

    assert result.warnings == ("totals differ",)
    assert result.unresolved_cards == ("MAX card 1",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.rows_imported = 0  # type: ignore[misc]


def test_report_status_must_be_terminal():
    with pytest.raises(ValueError):
        make_report(status=ImportStatus.RUNNING)


def test_report_counts_cannot_be_negative():
    with pytest.raises(ValueError):
        make_report(rows_duplicate=-1)


def test_needs_input_report_can_have_no_issuer_and_unresolved_cards():
    result = make_report(
        status=ImportStatus.NEEDS_INPUT,
        issuer_code=None,
        rows_total=0,
        rows_imported=0,
        rows_duplicate=0,
        rows_skipped=0,
        rows_needing_review=0,
        unresolved_cards=("CAL card 1 of 1",),
    )

    assert result.needs_input is True
    assert make_report().needs_input is False


def test_ports_and_parser_contract_are_narrow_abstractions():
    assert WorkbookReader.__abstractmethods__ == {"open"}
    assert ParserSelector.__abstractmethods__ == {"select"}
    assert IdentifierProtector.__abstractmethods__ == {"encrypt", "decrypt"}
    assert StatementParser.__abstractmethods__ == {"sniff", "read_context", "read_transactions"}
