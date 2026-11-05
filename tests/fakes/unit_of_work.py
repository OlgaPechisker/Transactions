from typing import Any

from bank_transactions.persistence.interfaces import (
    Isolation,
    UnitOfWork,
    UnitOfWorkFactory,
)


class RepositoryNotInjectedError(AssertionError):
    """A test used a repository it did not provide a fake for."""


def _slot(name: str) -> property:
    def get(self: "RepositorySlots") -> Any:
        try:
            return self.injected[name]
        except KeyError:
            raise RepositoryNotInjectedError(
                f"UnitOfWork.{name} was used but the test injected no fake for it"
            ) from None

    def put(self: "RepositorySlots", value: Any) -> None:
        self.injected[name] = value

    return property(get, put)


class RepositorySlots:
    """One slot per repository added for the API. Reading a slot raises until a test assigns a
    fake: `uow.users = FakeUsers()`. Mix into any `UnitOfWork` fake that predates the API."""

    @property
    def injected(self) -> dict[str, Any]:
        return self.__dict__.setdefault("_injected", {})

    users = _slot("users")
    sessions = _slot("sessions")
    login_throttle = _slot("login_throttle")
    claims = _slot("claims")
    visible_cards = _slot("visible_cards")
    import_history = _slot("import_history")
    transaction_query = _slot("transaction_query")
    categories = _slot("categories")
    overrides = _slot("overrides")
    mappings = _slot("mappings")
    rules = _slot("rules")
    trips = _slot("trips")
    tags = _slot("tags")
    exclusions = _slot("exclusions")
    analysis = _slot("analysis")
    currency_validation = _slot("currency_validation")
    review_counter = _slot("review_counter")


class InMemoryUnitOfWork(RepositorySlots, UnitOfWork):
    """Every repository, including the import ones, is a slot a test injects."""

    import_runs = _slot("import_runs")
    owners = _slot("owners")
    accounts = _slot("accounts")
    cards = _slot("cards")
    installment_plans = _slot("installment_plans")
    transactions = _slot("transactions")

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        if not self.committed:
            self.rolled_back = True


class InMemoryUnitOfWorkFactory(UnitOfWorkFactory):
    """Hands out units of work that share the repositories injected into `template`."""

    def __init__(self) -> None:
        self.template = InMemoryUnitOfWork()
        self.created: list[tuple[InMemoryUnitOfWork, Isolation]] = []

    def create(self, isolation: Isolation = Isolation.READ_COMMITTED) -> UnitOfWork:
        uow = InMemoryUnitOfWork()
        uow.injected.update(self.template.injected)
        self.created.append((uow, isolation))
        return uow
