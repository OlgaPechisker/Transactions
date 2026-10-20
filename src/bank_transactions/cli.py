"""Thin command line over the import library: detect, init-db and import.

Exit codes: 0 success, 1 failure, 2 usage or configuration, 3 identity input needed,
4 imported with row errors, 130 interrupted. Output never contains the DSN, the cipher key, file
paths, tracebacks or the text of unexpected errors; only our own sanitised messages are shown.
"""

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO, NoReturn, Protocol

from bank_transactions.config import ConfigurationError, Settings
from bank_transactions.domain.enums import ImportStatus, IssuerCode
from bank_transactions.domain.errors import BankTransactionsError
from bank_transactions.services.report import ImportReport

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_NEEDS_INPUT = 3
EXIT_PARTIAL = 4
EXIT_INTERRUPTED = 130

_NEUTRAL_FAILURE = (
    "error: the command failed unexpectedly; details are withheld to protect private data. "
    "For an import, see its audit record."
)
_INTERRUPTED = "Interrupted: the command was stopped and nothing was reported as completed."


class ImportRunner(Protocol):
    def import_file(self, path: Path, *, dry_run: bool = False) -> ImportReport: ...


class Composition(Protocol):
    """Everything the CLI needs from the concrete adapters; the real one is in bootstrap."""

    def detect_issuer(self, settings: Settings, path: Path) -> IssuerCode: ...

    def initialize_database(self, settings: Settings) -> None: ...

    def import_runner(
        self,
        settings: Settings,
        *,
        interactive: bool,
        read: Callable[[str], str],
        write: Callable[[str], None],
    ) -> ImportRunner: ...


class _StopError(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class _ArgumentParser(argparse.ArgumentParser):
    """Writes to the streams bound by `_bound_parser_class` and raises instead of exiting."""

    _out: IO[str]
    _err: IO[str]

    def _print_message(self, message: str, file: object = None) -> None:
        self._out.write(message)

    def error(self, message: str) -> NoReturn:
        self._err.write(self.format_usage())
        self._err.write(f"{self.prog}: error: {message}\n")
        raise _StopError(EXIT_USAGE)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            self._err.write(message)
        raise _StopError(status)


def _bound_parser_class(out: IO[str], err: IO[str]) -> type[_ArgumentParser]:
    class _BoundParser(_ArgumentParser):
        _out = out
        _err = err

    return _BoundParser


def _build_parser(out: IO[str], err: IO[str]) -> argparse.ArgumentParser:
    parser_class = _bound_parser_class(out, err)
    parser = parser_class(
        prog="bank-transactions",
        description="Import Israeli credit-card statements (Excel) into PostgreSQL.",
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="command",
        parser_class=parser_class,
    )
    detect = commands.add_parser("detect", help="name the issuer format of a workbook")
    detect.add_argument("file", type=Path)
    init_db = commands.add_parser("init-db", help="create or upgrade the database schema")
    init_db.add_argument("--dsn", help="database URL (overrides BT_DSN)")
    importer = commands.add_parser("import", help="import one statement workbook")
    importer.add_argument("file", type=Path)
    importer.add_argument("--dsn", help="database URL (overrides BT_DSN)")
    importer.add_argument(
        "--dry-run", action="store_true", help="run every step, then roll the data back"
    )
    importer.add_argument(
        "--non-interactive",
        action="store_true",
        help="never prompt; exit with code 3 when an identity must be chosen",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    read: Callable[[str], str] = input,
    composition: Composition | None = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    try:
        args = _build_parser(out, err).parse_args(argv)
    except _StopError as stop:
        return stop.status
    try:
        settings = Settings.from_env(
            os.environ if environ is None else environ, getattr(args, "dsn", None)
        )
        return _run(args, settings, composition or _default_composition(), out, read)
    except ConfigurationError as error:
        print(f"error: {error}", file=err)
        return EXIT_USAGE
    except BankTransactionsError as error:
        print(f"error: {error}", file=err)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        print(_INTERRUPTED, file=err)
        return EXIT_INTERRUPTED
    except Exception:
        print(_NEUTRAL_FAILURE, file=err)
        return EXIT_FAILURE


def _default_composition() -> Composition:
    from bank_transactions.bootstrap import DefaultComposition

    return DefaultComposition()


def _run(
    args: argparse.Namespace,
    settings: Settings,
    composition: Composition,
    out: IO[str],
    read: Callable[[str], str],
) -> int:
    if args.command == "detect":
        issuer = composition.detect_issuer(settings, args.file)
        print(f"Detected format: {issuer.value}", file=out)
        return EXIT_OK
    if args.command == "init-db":
        settings.require_dsn()
        composition.initialize_database(settings)
        print("The database schema is up to date.", file=out)
        return EXIT_OK
    return _import(args, settings, composition, out, read)


def _import(
    args: argparse.Namespace,
    settings: Settings,
    composition: Composition,
    out: IO[str],
    read: Callable[[str], str],
) -> int:
    settings.require_dsn()
    settings.require_cipher_key()
    runner = composition.import_runner(
        settings,
        interactive=not args.non_interactive,
        read=read,
        write=lambda text: print(text, file=out),
    )
    report = runner.import_file(args.file, dry_run=args.dry_run)
    _print_report(report, out)
    return _EXIT_BY_STATUS.get(report.status, EXIT_FAILURE)


_EXIT_BY_STATUS = {
    ImportStatus.SUCCESS: EXIT_OK,
    ImportStatus.PARTIAL: EXIT_PARTIAL,
    ImportStatus.NEEDS_INPUT: EXIT_NEEDS_INPUT,
}


def _print_report(report: ImportReport, out: IO[str]) -> None:
    def say(text: str = "") -> None:
        print(text, file=out)

    say(f"Import {report.status.value} (run {report.run_id})")
    if report.issuer_code is not None:
        say(f"Format: {report.issuer_code.value}")
    say(
        f"Rows: {report.rows_total} rows, {report.rows_imported} imported, "
        f"{report.rows_duplicate} duplicate, {report.rows_skipped} skipped, "
        f"{report.rows_needing_review} need review"
    )
    if report.dry_run:
        say("DRY RUN: nothing was committed; the counts show what would be imported.")
    for warning in report.warnings:
        say(f"Warning: {warning}")
    for issue in report.issues:
        place = (
            f"sheet {issue.location.sheet_position} row {issue.location.row}: "
            if issue.location
            else ""
        )
        say(f"{issue.severity.value} {issue.code.value}: {place}{issue.message}")
    if report.unresolved_cards:
        say("Cards that need an identity choice:")
        for label in report.unresolved_cards:
            say(f"  - {label}")
        say("Run the import again without --non-interactive to choose or create them.")
