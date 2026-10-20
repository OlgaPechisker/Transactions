"""A unit of work over one SQLAlchemy session, and its factory."""

from types import TracebackType

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import (
    AccountRepository,
    CardRepository,
    ImportRunRepository,
    InstallmentPlanRepository,
    OwnerRepository,
    TransactionRepository,
    UnitOfWork,
    UnitOfWorkFactory,
)
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
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def create(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self._sessions())
