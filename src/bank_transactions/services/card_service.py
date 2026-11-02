"""A user's cards. `CardPresenter` is the single place that decrypts a card for display."""

from dataclasses import dataclass

from bank_transactions.domain.card_labels import card_label
from bank_transactions.domain.enums import IdentifierType, IssuerCode
from bank_transactions.domain.errors import NotFoundError, ValidationFailedError
from bank_transactions.domain.ids import PublicId
from bank_transactions.persistence.interfaces import UnitOfWorkFactory
from bank_transactions.persistence.records import VisibleCardRecord
from bank_transactions.ports.identifier_protector import IdentifierProtector
from bank_transactions.services.interfaces import AuthContext

MAX_NICKNAME_LENGTH = 120


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
    def __init__(self, uow_factory: UnitOfWorkFactory, presenter: CardPresenter) -> None:
        self._uow_factory = uow_factory
        self._presenter = presenter

    def list(self, context: AuthContext) -> tuple[CardView, ...]:
        with self._uow_factory.create() as uow:
            records = uow.visible_cards.list_visible(context.user_id)
        return tuple(self._presenter.present(r, context.user_display_name) for r in records)

    def set_nickname(
        self, context: AuthContext, card_id: PublicId, nickname: str | None
    ) -> CardView:
        """Trims; blank becomes None; more than 120 characters is a ValidationFailedError."""
        value = (nickname or "").strip() or None
        if value is not None and len(value) > MAX_NICKNAME_LENGTH:
            raise ValidationFailedError.single(
                "nickname",
                "TOO_LONG",
                f"The nickname is limited to {MAX_NICKNAME_LENGTH} characters.",
            )
        with self._uow_factory.create() as uow:
            record = uow.visible_cards.set_display_name(context.user_id, card_id, value)
            if record is None:
                raise NotFoundError("The card was not found")
            uow.commit()
        return self._presenter.present(record, context.user_display_name)
