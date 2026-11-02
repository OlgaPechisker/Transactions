"""A user's cards. `CardPresenter` is the single place that decrypts a card for display."""

from dataclasses import dataclass

from bank_transactions.domain.card_labels import card_label
from bank_transactions.domain.enums import IdentifierType, IssuerCode
from bank_transactions.domain.ids import PublicId
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import VisibleCardRecord
from bank_transactions.ports.identifier_protector import IdentifierProtector
from bank_transactions.services.interfaces import AuthContext


@dataclass(frozen=True)
class CardView:
    public_id: PublicId
    label: str
    issuer: IssuerCode
    last4: str
    nickname: str | None
    owner_name: str


class CardPresenter:
    """Turns a visible card into what the API shows; the placeholder owner falls back to the
    signed-in user's name."""

    def __init__(self, protector: IdentifierProtector) -> None:
        self._protector = protector

    def present(self, record: VisibleCardRecord, user_name: str) -> CardView:
        last4 = self._protector.decrypt(IdentifierType.CARD_LAST4, record.last4_enc)
        owner_name = record.owner_display_name or user_name
        label = card_label(record.owner_display_name, user_name, record.issuer_code, last4)
        return CardView(
            record.public_id, label, record.issuer_code, last4, record.nickname, owner_name
        )


class CardService:
    """Stub: implemented by U4."""

    def __init__(self, uow_factory: UnitOfWorkFactory, presenter: CardPresenter) -> None:
        self._uow_factory = uow_factory
        self._presenter = presenter

    def list(self, context: AuthContext) -> tuple[CardView, ...]:
        raise NotImplementedError

    def set_nickname(
        self, context: AuthContext, card_id: PublicId, nickname: str | None
    ) -> CardView:
        """Trims; blank becomes None; more than 120 characters is a ValidationFailedError."""
        raise NotImplementedError
