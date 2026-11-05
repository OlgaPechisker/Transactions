from bank_transactions.domain.ids import UserId
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.services.interfaces import ReviewCounter


class UowReviewCounter(ReviewCounter):
    """Adapts the repository to the service-level port, so callers need no unit of work."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def count_visible_unknown_currency(self, user_id: UserId) -> int:
        with self._uow_factory.create() as uow:
            return uow.review_counter.count_visible_unknown_currency(user_id)
