"""The signed-in user's profile. Stub: implemented by U4."""

from dataclasses import dataclass

from bank_transactions.domain.ids import PublicId
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.services.interfaces import AuthContext, ReviewCounter


@dataclass(frozen=True)
class MeView:
    public_id: PublicId
    name: str
    email: str
    csrf_token: str
    pending_currency_validations: int


class ProfileService:
    def __init__(self, uow_factory: UnitOfWorkFactory, review_counter: ReviewCounter) -> None:
        self._uow_factory = uow_factory
        self._review_counter = review_counter

    def me(self, context: AuthContext) -> MeView:
        raise NotImplementedError
