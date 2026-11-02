"""Card-key claims: the one-time, cross-user registry of which user owns an issuer + last-four."""

from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.errors import DataAlreadyOwnedError, PersistenceError
from bank_transactions.domain.ids import ClaimId, IdentifierToken, UserId
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import ClaimRepository
from bank_transactions.persistence.orm.models import ClaimRow
from bank_transactions.persistence.records import ClaimRecord


def _record(row: ClaimRow) -> ClaimRecord:
    return ClaimRecord(
        ClaimId(row.id), UserId(row.user_id), row.issuer_code, IdentifierToken(bytes(row.last4_enc))
    )


def _by_key(issuer_code: IssuerCode, last4_enc: IdentifierToken) -> Select[Any]:
    return select(ClaimRow).where(
        ClaimRow.issuer_code == issuer_code, ClaimRow.last4_enc == bytes(last4_enc)
    )


class SqlClaimRepository(ClaimRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def find(self, issuer_code: IssuerCode, last4_enc: IdentifierToken) -> ClaimRecord | None:
        row = self._session.scalars(_by_key(issuer_code, last4_enc)).one_or_none()
        return None if row is None else _record(row)

    @translates_database_errors
    def create_or_get(
        self, user_id: UserId, issuer_code: IssuerCode, last4_enc: IdentifierToken
    ) -> ClaimRecord:
        """Claim the key for the user. A key already claimed by someone else is a conflict that
        does not reveal the other user."""
        inserted = self._session.execute(
            postgresql_insert(ClaimRow)
            .values(user_id=user_id, issuer_code=issuer_code, last4_enc=bytes(last4_enc))
            .on_conflict_do_nothing(index_elements=["issuer_code", "last4_enc"])
            .returning(ClaimRow.id)
        ).scalar_one_or_none()
        if inserted is not None:
            return ClaimRecord(ClaimId(inserted), user_id, issuer_code, last4_enc)
        existing = self.lock(issuer_code, last4_enc)
        if existing is None:
            raise PersistenceError("the card claim could not be created")
        if existing.user_id != user_id:
            raise DataAlreadyOwnedError("this card is already linked to another account")
        return existing

    @translates_database_errors
    def lock(self, issuer_code: IssuerCode, last4_enc: IdentifierToken) -> ClaimRecord | None:
        row = self._session.scalars(_by_key(issuer_code, last4_enc).with_for_update()).one_or_none()
        return None if row is None else _record(row)
