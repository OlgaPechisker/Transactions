"""Card-key claims. Stub: implemented by U5."""

from sqlalchemy.orm import Session

from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.ids import IdentifierToken, UserId
from bank_transactions.persistence.interfaces import ClaimRepository
from bank_transactions.persistence.records import ClaimRecord


class SqlClaimRepository(ClaimRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, issuer_code: IssuerCode, last4_enc: IdentifierToken) -> ClaimRecord | None:
        raise NotImplementedError

    def create_or_get(
        self, user_id: UserId, issuer_code: IssuerCode, last4_enc: IdentifierToken
    ) -> ClaimRecord:
        raise NotImplementedError

    def lock(self, issuer_code: IssuerCode, last4_enc: IdentifierToken) -> ClaimRecord | None:
        raise NotImplementedError
