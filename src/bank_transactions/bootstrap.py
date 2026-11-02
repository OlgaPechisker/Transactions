"""Composition root: the only module that wires concrete adapters (Excel, PostgreSQL, AES-SIV,
registered issuer parsers) into the library. Importing it pulls those libraries in; the import
service and the CLI do not."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from bank_transactions.cli import ImportRunner
from bank_transactions.config import Settings
from bank_transactions.domain.enums import IssuerCode
from bank_transactions.identity.argon2_hasher import Argon2PasswordHasher
from bank_transactions.identity.cli_resolver import CliIdentityResolver
from bank_transactions.identity.crypto import IdentifierCipher
from bank_transactions.identity.interfaces import IdentityResolver
from bank_transactions.identity.noninteractive import NonInteractiveResolver
from bank_transactions.identity.pycountry_catalogue import PycountryCurrencyCatalogue
from bank_transactions.identity.tokens import HmacTokenService
from bank_transactions.parsing.default_registry import build_default_registry
from bank_transactions.parsing.workbook import ExcelWorkbookReader
from bank_transactions.persistence.db import create_database_engine, upgrade_database
from bank_transactions.persistence.uow import SqlAlchemyUnitOfWorkFactory
from bank_transactions.ports.clock import Clock
from bank_transactions.ports.identifier_protector import IdentifierProtector
from bank_transactions.ports.parser_selector import ParserSelector
from bank_transactions.ports.workbook_reader import WorkbookReader
from bank_transactions.services.analysis_service import AnalysisService
from bank_transactions.services.card_service import CardPresenter, CardService
from bank_transactions.services.category_service import CategoryService
from bank_transactions.services.composition import ApiComposition
from bank_transactions.services.currency_validation_service import CurrencyValidationService
from bank_transactions.services.import_api_service import ImportApiService
from bank_transactions.services.import_service import StatementImportService
from bank_transactions.services.profile_service import ProfileService
from bank_transactions.services.review_counter import UowReviewCounter
from bank_transactions.services.session_service import SessionAuthenticator, SessionService
from bank_transactions.services.transaction_service import TransactionService
from bank_transactions.services.trip_service import TripService


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)


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


def build_api_composition(settings: Settings) -> ApiComposition:
    """Wires the services the REST API consumes. No connection is opened here."""
    uow_factory = SqlAlchemyUnitOfWorkFactory(create_database_engine(settings.require_dsn()))
    protector = build_identifier_protector(settings)
    tokens = HmacTokenService(settings.require_cipher_key())
    hasher = Argon2PasswordHasher()
    catalogue = PycountryCurrencyCatalogue()
    clock = SystemClock()
    presenter = CardPresenter(protector)
    review_counter = UowReviewCounter(uow_factory)
    return ApiComposition(
        authenticator=SessionAuthenticator(uow_factory, tokens, clock),
        card_presenter=presenter,
        sessions=SessionService(uow_factory, hasher, tokens, clock),
        profile=ProfileService(uow_factory, review_counter),
        cards=CardService(uow_factory, presenter),
        imports=ImportApiService(
            uow_factory,
            build_workbook_reader(),
            build_parser_selector(settings),
            protector,
            tokens,
            clock,
        ),
        transactions=TransactionService(uow_factory),
        categories=CategoryService(uow_factory),
        trips=TripService(uow_factory, catalogue, clock),
        analysis=AnalysisService(uow_factory, clock),
        currency_validation=CurrencyValidationService(uow_factory, catalogue, clock),
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
