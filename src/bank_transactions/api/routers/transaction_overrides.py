"""`PATCH /transactions/{id}`: set or clear the custom-category override."""

from fastapi import APIRouter

from bank_transactions.api.dependencies import Composition, CsrfUser, TransactionIdPath
from bank_transactions.api.group_labels import card_presenter
from bank_transactions.api.problems import problem_responses
from bank_transactions.api.schemas.common import TransactionDetailDto
from bank_transactions.api.schemas.mappers import to_transaction_detail
from bank_transactions.api.schemas.transactions import UpdateTransactionRequest

router = APIRouter(tags=["Transactions"])


@router.patch(
    "/transactions/{transactionId}",
    operation_id="updateTransaction",
    responses=problem_responses(404, 422),
)
def update_transaction(
    transaction_id: TransactionIdPath,
    body: UpdateTransactionRequest,
    user: CsrfUser,
    comp: Composition,
) -> TransactionDetailDto:
    record = comp.categories.set_override(user, transaction_id, body.custom_category)
    return to_transaction_detail(record, card_presenter(comp, user.user_display_name))
