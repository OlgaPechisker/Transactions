"""`PATCH /transactions/{id}`: set or clear the custom-category override."""

from fastapi import APIRouter

from bank_transactions.api.dependencies import Composition, CsrfUser, TransactionIdPath
from bank_transactions.api.schemas.common import TransactionDetailDto
from bank_transactions.api.schemas.transactions import UpdateTransactionRequest

router = APIRouter(tags=["Transactions"])


@router.patch("/transactions/{transactionId}", operation_id="updateTransaction")
def update_transaction(
    transaction_id: TransactionIdPath,
    body: UpdateTransactionRequest,
    user: CsrfUser,
    comp: Composition,
) -> TransactionDetailDto:
    comp.categories.set_override(user, transaction_id, body.custom_category)
    raise NotImplementedError
