"""The command line as a user sees it: arguments and environment in, text and exit code out.
A fake composition stands in for the real adapters, so no database or workbook is touched."""

import base64
import io
from pathlib import Path

import pytest

from bank_transactions.cli import main
from bank_transactions.config import Settings
from bank_transactions.domain.enums import ImportStatus, IssuerCode, IssueSeverity, RowIssueCode
from bank_transactions.domain.errors import ParseError, UnknownFormatError
from bank_transactions.domain.issues import RowIssue, SourceLocation
from bank_transactions.services.report import ImportReport

KEY = base64.b64encode(bytes(range(32))).decode()
DSN = "postgresql+psycopg://user:s3cret@127.0.0.1:5432/bank"


def report(status=ImportStatus.SUCCESS, **fields):
    values = {
        "run_id": 7,
        "status": status,
        "issuer_code": IssuerCode.MAX,
        "rows_total": 10,
        "rows_imported": 8,
        "rows_duplicate": 1,
        "rows_skipped": 1,
        "rows_needing_review": 2,
    }
    return ImportReport(**{**values, **fields})


class FakeRunner:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def import_file(self, path, *, dry_run=False):
        self.calls.append((path, dry_run))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeComposition:
    def __init__(self, outcome=None):
        self.runner = FakeRunner(outcome or report())
        self.detected = None
        self.detect_failure = None
        self.initialized = []
        self.init_failure = None
        self.runner_requests = []

    def detect_issuer(self, settings, path):
        if self.detect_failure:
            raise self.detect_failure
        self.detected = (settings, path)
        return IssuerCode.CAL

    def initialize_database(self, settings):
        if self.init_failure:
            raise self.init_failure
        self.initialized.append(settings)

    def import_runner(self, settings, *, interactive, read, write):
        self.runner_requests.append((settings, interactive))
        return self.runner


@pytest.fixture
def composition():
    return FakeComposition()


@pytest.fixture
def run(composition):
    def invoke(*argv, env=None, read=None):
        out, err = io.StringIO(), io.StringIO()
        code = main(
            list(argv),
            environ=env or {},
            stdout=out,
            stderr=err,
            read=read or (lambda prompt: ""),
            composition=composition,
        )
        return code, out.getvalue(), err.getvalue()

    return invoke


FULL_ENV = {"BT_DSN": DSN, "BT_CIPHER_KEY": KEY}


# --- detect -----------------------------------------------------------------------------


def test_detect_names_the_issuer_and_needs_neither_database_nor_key(run, composition):
    code, out, err = run("detect", "statement.xlsx")

    assert (code, err) == (0, "")
    assert "CAL" in out
    assert composition.detected[1] == Path("statement.xlsx")
    assert composition.runner_requests == []


def test_detect_passes_the_parser_settings_from_the_environment(run, composition):
    run(
        "detect", "statement.xlsx", env={"BT_TWO_DIGIT_YEARS_BACK": "30", "BT_FEE_WINDOW_DAYS": "5"}
    )

    settings, _ = composition.detected
    assert (settings.two_digit_years_back, settings.fee_window_days) == (30, 5)


def test_detect_of_an_unrecognised_workbook_is_a_failure_without_the_path(run, composition):
    composition.detect_failure = UnknownFormatError("no parser recognised the workbook")

    code, out, err = run("detect", "private-name-4396.xlsx")

    assert code == 1
    assert "no parser recognised the workbook" in err
    assert "private-name" not in out + err


# --- init-db ----------------------------------------------------------------------------


def test_init_db_needs_the_dsn_but_not_the_cipher_key(run, composition):
    code, out, _ = run("init-db", env={"BT_DSN": DSN})

    assert code == 0
    assert composition.initialized[0].dsn == DSN
    assert "s3cret" not in out


def test_init_db_without_a_dsn_is_a_usage_error_naming_the_variable(run, composition):
    code, _, err = run("init-db")

    assert code == 2
    assert "BT_DSN" in err
    assert composition.initialized == []


def test_the_dsn_option_overrides_the_environment(run, composition):
    run("init-db", "--dsn", "postgresql+psycopg://other@127.0.0.1/db2", env={"BT_DSN": DSN})

    assert composition.initialized[0].dsn.endswith("/db2")


def test_a_failed_migration_is_reported_without_the_connection_string(run, composition):
    composition.init_failure = RuntimeError(f"cannot connect to {DSN}")

    code, out, err = run("init-db", env={"BT_DSN": DSN})

    assert code == 1
    assert "s3cret" not in out + err and "Traceback" not in out + err


# --- import: configuration -----------------------------------------------------------


def test_import_requires_the_dsn(run, composition):
    code, _, err = run("import", "s.xlsx", env={"BT_CIPHER_KEY": KEY})

    assert (code, composition.runner_requests) == (2, [])
    assert "BT_DSN" in err


def test_import_requires_the_cipher_key(run, composition):
    code, _, err = run("import", "s.xlsx", env={"BT_DSN": DSN})

    assert (code, composition.runner_requests) == (2, [])
    assert "BT_CIPHER_KEY" in err


def test_an_invalid_cipher_key_is_rejected_without_echoing_it(run, composition):
    code, out, err = run("import", "s.xlsx", env={"BT_DSN": DSN, "BT_CIPHER_KEY": "not-base64!!"})

    assert code == 2
    assert "not-base64" not in out + err
    assert composition.runner_requests == []


def test_an_invalid_numeric_setting_is_a_configuration_error(run):
    code, _, err = run("detect", "s.xlsx", env={"BT_FEE_WINDOW_DAYS": "soon"})

    assert code == 2 and "BT_FEE_WINDOW_DAYS" in err


# --- import: behaviour ----------------------------------------------------------------


def test_a_successful_import_prints_the_counts_and_exits_zero(run, composition):
    code, out, err = run("import", "s.xlsx", env=FULL_ENV)

    assert (code, err) == (0, "")
    for text in ("SUCCESS", "MAX", "10 rows", "8 imported", "1 duplicate", "1 skipped", "2 need"):
        assert text in out
    assert composition.runner.calls == [(Path("s.xlsx"), False)]
    settings, interactive = composition.runner_requests[0]
    assert interactive is True
    assert isinstance(settings, Settings) and settings.cipher_key == KEY


def test_a_dry_run_is_announced_and_passed_to_the_service(run, composition):
    composition.runner.outcome = report(dry_run=True)

    code, out, _ = run("import", "s.xlsx", "--dry-run", env=FULL_ENV)

    assert code == 0
    assert composition.runner.calls == [(Path("s.xlsx"), True)]
    assert "DRY RUN" in out and "nothing was committed" in out


def test_non_interactive_mode_is_requested_from_the_composition(run, composition):
    run("import", "s.xlsx", "--non-interactive", env=FULL_ENV)

    assert composition.runner_requests[0][1] is False


def test_needing_input_lists_the_cards_and_exits_three(run, composition):
    composition.runner.outcome = report(
        ImportStatus.NEEDS_INPUT, unresolved_cards=("MAX card 2 of 3",), rows_total=0
    )

    code, out, _ = run("import", "s.xlsx", "--non-interactive", env=FULL_ENV)

    assert code == 3
    assert "NEEDS_INPUT" in out and "MAX card 2 of 3" in out
    assert "interactive" in out.lower()


def test_a_partial_import_lists_the_problem_rows_and_exits_four(run, composition):
    issue = RowIssue(
        SourceLocation(2, 17), RowIssueCode.MALFORMED_ROW, IssueSeverity.ERROR, "amount is missing"
    )
    composition.runner.outcome = report(ImportStatus.PARTIAL, issues=(issue,))

    code, out, _ = run("import", "s.xlsx", env=FULL_ENV)

    assert code == 4
    assert "PARTIAL" in out and "sheet 2 row 17" in out and "amount is missing" in out


def test_warnings_are_shown(run, composition):
    composition.runner.outcome = report(warnings=("Declared and calculated totals differ.",))

    _, out, _ = run("import", "s.xlsx", env=FULL_ENV)

    assert "Declared and calculated totals differ." in out


def test_a_domain_failure_prints_its_safe_message_and_exits_one(run, composition):
    composition.runner.outcome = ParseError("the header row is missing")

    code, _, err = run("import", "private-4396.xlsx", env=FULL_ENV)

    assert code == 1
    assert "the header row is missing" in err
    assert "private-4396" not in err


def test_an_unexpected_error_is_neutral_without_traceback_dsn_key_or_path(run, composition):
    composition.runner.outcome = RuntimeError(f"{DSN} {KEY} C:\\Users\\dana\\statements\\x.xlsx")

    code, out, err = run("import", "C:\\Users\\dana\\statements\\x.xlsx", env=FULL_ENV)

    assert code == 1
    shown = out + err
    for secret in ("s3cret", KEY, "dana", "Traceback", "RuntimeError"):
        assert secret not in shown
    assert "unexpected" in err.lower()


def test_an_interrupt_exits_130_and_never_claims_success(run, composition):
    composition.runner.outcome = KeyboardInterrupt()

    code, out, err = run("import", "s.xlsx", env=FULL_ENV)

    assert code == 130
    assert "SUCCESS" not in out + err and "interrupted" in (out + err).lower()


# --- usage ------------------------------------------------------------------------------


def test_a_missing_command_is_a_usage_error(run):
    code, _, err = run()

    assert code == 2 and "usage" in err.lower()


def test_an_unknown_option_is_a_usage_error_and_runs_nothing(run, composition):
    code, _, err = run("import", "s.xlsx", "--force", env=FULL_ENV)

    assert (code, composition.runner.calls) == (2, [])
    assert "usage" in err.lower()


def test_help_goes_to_standard_output_and_exits_zero(run):
    code, out, err = run("--help")

    assert (code, err) == (0, "")
    assert "detect" in out and "init-db" in out and "import" in out
