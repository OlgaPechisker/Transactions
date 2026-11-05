"""Cards a user sees through a claim."""

from typing import Any

from sqlalchemy import Row, Select, select, update
from sqlalchemy.orm import Session

from bank_transactions.domain.ids import CardId, IdentifierToken, PublicId, UserId
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import VisibleCardRepository
from bank_transactions.persistence.orm.models import CardRow, ClaimRow, OwnerRow
from bank_transactions.persistence.records import VisibleCardRecord


def _visible_cards(user_id: UserId) -> Select[*tuple[Any, ...]]:
    return (
        select(
            CardRow.id,
            CardRow.public_id,
            CardRow.issuer_code,
            CardRow.last4_enc,
            CardRow.display_name,
            OwnerRow.display_name.label("owner_display_name"),
        )
        .join(ClaimRow, (ClaimRow.id == CardRow.claim_id) & (ClaimRow.user_id == user_id))
        .join(OwnerRow, OwnerRow.id == CardRow.owner_id)
    )


def _record(row: Row[Any]) -> VisibleCardRecord:
    return VisibleCardRecord(
        card_id=CardId(row.id),
        public_id=PublicId(row.public_id),
        issuer_code=row.issuer_code,
        last4_enc=IdentifierToken(bytes(row.last4_enc)),
        nickname=row.display_name,
        owner_display_name=row.owner_display_name,
    )


class SqlVisibleCardRepository(VisibleCardRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def list_visible(self, user_id: UserId) -> tuple[VisibleCardRecord, ...]:
        rows = self._session.execute(_visible_cards(user_id).order_by(CardRow.id)).all()
        return tuple(_record(row) for row in rows)

    @translates_database_errors
    def get_visible(self, user_id: UserId, public_id: PublicId) -> VisibleCardRecord | None:
        row = self._session.execute(
            _visible_cards(user_id).where(CardRow.public_id == public_id)
        ).one_or_none()
        return None if row is None else _record(row)

    @translates_database_errors
    def set_display_name(
        self, user_id: UserId, public_id: PublicId, display_name: str | None
    ) -> VisibleCardRecord | None:
        claimed = select(ClaimRow.id).where(ClaimRow.user_id == user_id)
        updated = self._session.execute(
            update(CardRow)
            .where(CardRow.public_id == public_id, CardRow.claim_id.in_(claimed))
            .values(display_name=display_name)
            .returning(CardRow.id)
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        return None if updated is None else self.get_visible(user_id, public_id)
