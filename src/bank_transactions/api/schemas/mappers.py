"""Service view -> DTO mappers. Stubs: implemented by U2 (shared) and the owning units."""

from collections.abc import Callable

from bank_transactions.api.schemas.common import (
    BillingSummaryDto,
    CardDto,
    CardRefDto,
    GroupDto,
    PageMeta,
    TransactionDetailDto,
    TransactionListItemDto,
)
from bank_transactions.domain.queries import Page
from bank_transactions.services.card_service import CardView
from bank_transactions.services.read_models import (
    BillingSummaryRecord,
    GroupRecord,
    TransactionViewRecord,
    VisibleCardRecord,
)

CardPresent = Callable[[VisibleCardRecord], CardView]


def to_card_dto(card: CardView) -> CardDto:
    raise NotImplementedError


def to_card_ref_dto(card: CardView) -> CardRefDto:
    raise NotImplementedError


def to_page_meta(page: Page[object]) -> PageMeta:
    raise NotImplementedError


def to_billing_summary(summary: BillingSummaryRecord) -> BillingSummaryDto:
    raise NotImplementedError


def to_group(group: GroupRecord, label: str) -> GroupDto:
    raise NotImplementedError


def to_transaction_list_item(
    record: TransactionViewRecord, present_card: CardPresent
) -> TransactionListItemDto:
    raise NotImplementedError


def to_transaction_detail(
    record: TransactionViewRecord, present_card: CardPresent
) -> TransactionDetailDto:
    raise NotImplementedError
