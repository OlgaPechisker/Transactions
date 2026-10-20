"""Composition root: the only module that wires concrete adapters (Excel, PostgreSQL, AES-SIV,
registered issuer parsers) into the library. Importing it pulls those libraries in; the import
service and the CLI do not."""

from collections.abc import Callable
from pathlib import Path

from bank_transactions.cli import ImportRunner
from bank_transactions.config import Settings
from bank_transactions.domain.enums import IssuerCode
from bank_transactions.identity.cli_resolver import CliIdentityResolver
from bank_transactions.identity.crypto import IdentifierCipher
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.noninteractive import NonInteractiveResolver
from bank_transactions.parsing.default_registry import build_default_registry
from bank_transactions.parsing.workbook import ExcelWorkbookReader
from bank_transactions.persistence.db import create_database_engine, upgrade_database
from bank_transactions.persistence.uow import SqlAlchemyUnitOfWorkFactory
from bank_transactions.ports.identifier_protector import IdentifierProtector
from bank_transactions.ports.parser_selector import ParserSelector
from bank_transactions.ports.workbook_reader import WorkbookReader
from bank_transactions.services.import_service import StatementImportService


def build_workbook_reader() -> WorkbookReader:
    return ExcelWorkbookReader()


def build_parser_selector(settings: Settings) -> ParserSelector:
    options: dict[str, object] = {
        "two_digit_years_back": settings.two_digit_years_back,
        "fee_window_days": settings.fee_window_days,
        "minimum_confidence": settings.minimum_confidence,
    }
    if settings.fee_keywords is not None:
        options["fee_keywords"] = settings.fee_keywords
    return build_default_registry(**options)  # type: ignore[arg-type]


def build_identifier_protector(settings: Settings) -> IdentifierProtector:
    return IdentifierCipher(settings.require_cipher_key())


def build_identity_resolver(
    *, interactive: bool, read: Callable[[str], str], write: Callable[[str], None]
) -> IdentityResolver:
    if interactive:
        return CliIdentityResolver(read, write)
    return NonInteractiveResolver()


def build_import_service(
    settings: Settings,
    *,
    interactive: bool = True,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> StatementImportService:
    dsn = settings.require_dsn()
    protector = build_identifier_protector(settings)
    return StatementImportService(
        build_workbook_reader(),
        build_parser_selector(settings),
        protector,
        build_identity_resolver(interactive=interactive, read=read, write=write),
        SqlAlchemyUnitOfWorkFactory(create_database_engine(dsn)),
    )


class DefaultComposition:
    """What the CLI asks for, backed by the real adapters."""

    def detect_issuer(self, settings: Settings, path: Path) -> IssuerCode:
        workbook = build_workbook_reader().open(path)
        return build_parser_selector(settings).select(workbook).issuer_code

    def initialize_database(self, settings: Settings) -> None:
        upgrade_database(settings.require_dsn())

    def import_runner(
        self,
        settings: Settings,
        *,
        interactive: bool,
        read: Callable[[str], str],
        write: Callable[[str], None],
    ) -> ImportRunner:
        return build_import_service(settings, interactive=interactive, read=read, write=write)
