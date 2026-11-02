"""A unit of work over one SQLAlchemy session, and its factory."""

from types import TracebackType

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import (
    AccountRepository,
    AnalysisRepository,
    CardRepository,
    CategoryRepository,
    ClaimRepository,
    CurrencyValidationRepository,
    ExclusionRepository,
    ImportHistoryRepository,
    ImportRunRepository,
    InstallmentPlanRepository,
    Isolation,
    LoginThrottleRepository,
    MappingRepository,
    OverrideRepository,
    OwnerRepository,
    ReviewCounterRepository,
    RuleRepository,
    SessionRepository,
    TagRepository,
    TransactionQueryRepository,
    TransactionRepository,
    TripRepository,
    UnitOfWork,
    UnitOfWorkFactory,
    UserRepository,
    VisibleCardRepository,
)
from bank_transactions.persistence.providers.analysis import SqlAnalysisRepository
from bank_transactions.persistence.providers.cards_visible import SqlVisibleCardRepository
from bank_transactions.persistence.providers.categories import SqlCategoryRepository
from bank_transactions.persistence.providers.claims import SqlClaimRepository
from bank_transactions.persistence.providers.currency_validation import (
    SqlCurrencyValidationRepository,
)
from bank_transactions.persistence.providers.import_history import SqlImportHistoryRepository
from bank_transactions.persistence.providers.login_throttle import SqlLoginThrottleRepository
from bank_transactions.persistence.providers.mappings import SqlMappingRepository
from bank_transactions.persistence.providers.overrides import SqlOverrideRepository
from bank_transactions.persistence.providers.review_counter import SqlReviewCounterRepository
from bank_transactions.persistence.providers.rules import SqlRuleRepository
from bank_transactions.persistence.providers.sessions import SqlSessionRepository
from bank_transactions.persistence.providers.transaction_query import (
    SqlTransactionQueryRepository,
)
from bank_transactions.persistence.providers.trip_exclusions import SqlExclusionRepository
from bank_transactions.persistence.providers.trip_tags import SqlTagRepository
from bank_transactions.persistence.providers.trips import SqlTripRepository
from bank_transactions.persistence.providers.users import SqlUserRepository
from bank_transactions.persistence.repositories import (
    SqlAccountRepository,
    SqlCardRepository,
    SqlImportRunRepository,
    SqlInstallmentPlanRepository,
    SqlOwnerRepository,
    SqlTransactionRepository,
)


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session: Session) -> None:
        self._session = session
        self._import_runs = SqlImportRunRepository(session)
        self._owners = SqlOwnerRepository(session)
        self._accounts = SqlAccountRepository(session)
        self._cards = SqlCardRepository(session)
        self._installment_plans = SqlInstallmentPlanRepository(session)
        self._transactions = SqlTransactionRepository(session)
        self._users = SqlUserRepository(session)
        self._sessions = SqlSessionRepository(session)
        self._login_throttle = SqlLoginThrottleRepository(session)
        self._claims = SqlClaimRepository(session)
        self._visible_cards = SqlVisibleCardRepository(session)
        self._import_history = SqlImportHistoryRepository(session)
        self._transaction_query = SqlTransactionQueryRepository(session)
        self._categories = SqlCategoryRepository(session)
        self._overrides = SqlOverrideRepository(session)
        self._mappings = SqlMappingRepository(session)
        self._rules = SqlRuleRepository(session)
        self._trips = SqlTripRepository(session)
        self._tags = SqlTagRepository(session)
        self._exclusions = SqlExclusionRepository(session)
        self._analysis = SqlAnalysisRepository(session)
        self._currency_validation = SqlCurrencyValidationRepository(session)
        self._review_counter = SqlReviewCounterRepository(session)

    @property
    def import_runs(self) -> ImportRunRepository:
        return self._import_runs

    @property
    def owners(self) -> OwnerRepository:
        return self._owners

    @property
    def accounts(self) -> AccountRepository:
        return self._accounts

    @property
    def cards(self) -> CardRepository:
        return self._cards

    @property
    def installment_plans(self) -> InstallmentPlanRepository:
        return self._installment_plans

    @property
    def transactions(self) -> TransactionRepository:
        return self._transactions

    @property
    def users(self) -> UserRepository:
        return self._users

    @property
    def sessions(self) -> SessionRepository:
        return self._sessions

    @property
    def login_throttle(self) -> LoginThrottleRepository:
        return self._login_throttle

    @property
    def claims(self) -> ClaimRepository:
        return self._claims

    @property
    def visible_cards(self) -> VisibleCardRepository:
        return self._visible_cards

    @property
    def import_history(self) -> ImportHistoryRepository:
        return self._import_history

    @property
    def transaction_query(self) -> TransactionQueryRepository:
        return self._transaction_query

    @property
    def categories(self) -> CategoryRepository:
        return self._categories

    @property
    def overrides(self) -> OverrideRepository:
        return self._overrides

    @property
    def mappings(self) -> MappingRepository:
        return self._mappings

    @property
    def rules(self) -> RuleRepository:
        return self._rules

    @property
    def trips(self) -> TripRepository:
        return self._trips

    @property
    def tags(self) -> TagRepository:
        return self._tags

    @property
    def exclusions(self) -> ExclusionRepository:
        return self._exclusions

    @property
    def analysis(self) -> AnalysisRepository:
        return self._analysis

    @property
    def currency_validation(self) -> CurrencyValidationRepository:
        return self._currency_validation

    @property
    def review_counter(self) -> ReviewCounterRepository:
        return self._review_counter

    @translates_database_errors
    def commit(self) -> None:
        self._session.commit()

    @translates_database_errors
    def rollback(self) -> None:
        self._session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            super().__exit__(exc_type, exc, traceback)
        finally:
            self._session.close()


class SqlAlchemyUnitOfWorkFactory(UnitOfWorkFactory):
    def __init__(self, engine: Engine) -> None:
        self._sessions = {
            Isolation.READ_COMMITTED: sessionmaker(engine, expire_on_commit=False),
            Isolation.REPEATABLE_READ: sessionmaker(
                engine.execution_options(isolation_level="REPEATABLE READ"),
                expire_on_commit=False,
            ),
        }

    def create(self, isolation: Isolation = Isolation.READ_COMMITTED) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self._sessions[isolation]())
