import pytest

from bank_transactions.domain.enums import IssueSeverity, RowIssueCode
from bank_transactions.domain.issues import RowIssue, SourceLocation


def test_location_names_a_sheet_position_not_a_sheet_name():
    location = SourceLocation(sheet_position=2, row=17)

    assert (location.sheet_position, location.row) == (2, 17)
    assert "sheet_name" not in repr(location)


@pytest.mark.parametrize("sheet_position,row", [(0, 1), (1, 0), (-1, 5)])
def test_locations_are_one_based(sheet_position, row):
    with pytest.raises(ValueError):
        SourceLocation(sheet_position=sheet_position, row=row)


def test_pending_row_issue_is_informational_and_located():
    issue = RowIssue(
        location=SourceLocation(1, 9),
        code=RowIssueCode.PENDING_TRANSACTION,
        severity=IssueSeverity.INFO,
        message="Pending row skipped",
    )

    assert issue.severity is IssueSeverity.INFO
    assert issue.location == SourceLocation(1, 9)


def test_file_level_issue_may_have_no_location():
    issue = RowIssue(None, RowIssueCode.TOTALS_MISMATCH, IssueSeverity.WARNING, "Totals differ")

    assert issue.location is None


def test_blank_message_is_rejected():
    with pytest.raises(ValueError):
        RowIssue(None, RowIssueCode.MALFORMED_ROW, IssueSeverity.ERROR, "  ")
