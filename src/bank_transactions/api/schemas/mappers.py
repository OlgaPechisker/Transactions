"""Service view -> DTO mappers shared by several resources, per `docs/api-contract/openapi.yaml`.

Amounts are two-decimal strings, rates six-decimal strings (always positive); nullable members
are always present."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from bank_transactions.api.schemas.common import (
    BillingSummaryDto,
    BillingTotalDto,
    CardDto,
    CardRefDto,
    CategoryDto,
    FxDto,
    GroupDto,
    InstallmentDto,
    InstallmentPlanDto,
    MoneyDto,
    PageMeta,
    PersonDto,
    TransactionDetailDto,
    TransactionListItemDto,
    TripRefDto,
)
from bank_transactions.domain.enums import TransactionType
from bank_transactions.domain.money import round_amount, round_rate
from bank_transactions.domain.queries import Page
from bank_transactions.services.card_service import CardView
from bank_transactions.services.read_models import (
    BillingSummaryRecord,
    GroupRecord,
    TransactionViewRecord,
    TripMembershipRef,
    VisibleCardRecord,
)

CardPresent = Callable[[VisibleCardRecord], CardView]

_PERCENT_PLACES = Decimal("0.01")


def format_amount(amount: Decimal) -> str:
    rounded = round_amount(amount)
    return format(abs(rounded) if rounded == 0 else rounded, "f")


def format_rate(rate: Decimal) -> str:
    return format(round_rate(abs(rate)), "f")


def format_timestamp(moment: datetime) -> str:
    utc = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_money(amount: Decimal, currency: str | None) -> MoneyDto:
    return MoneyDto(amount=format_amount(amount), currency=currency)


def to_card_dto(card: CardView) -> CardDto:
    return CardDto(
        id=card.public_id,
        label=card.label,
        issuer=card.issuer,
        last4=card.last4,
        nickname=card.nickname,
        owner=PersonDto(name=card.owner_name),
    )


def to_card_ref_dto(card: CardView) -> CardRefDto:
    return CardRefDto(
        id=card.public_id,
        label=card.label,
        issuer=card.issuer,
        last4=card.last4,
        nickname=card.nickname,
        owner_name=card.owner_name,
    )


def to_page_meta(page: Page[object]) -> PageMeta:
    return PageMeta(
        page=page.page,
        page_size=page.size,
        total_items=page.total,
        total_pages=page.total_pages,
    )


def to_billing_summary(summary: BillingSummaryRecord) -> BillingSummaryDto:
    return BillingSummaryDto(
        count=summary.count,
        unknown_billing_currency_count=summary.unknown_billing_currency_count,
        billing_totals=[
            BillingTotalDto(count=total.count, net=to_money(total.net, total.currency))
            for total in summary.totals
        ],
    )


def to_group(group: GroupRecord, label: str) -> GroupDto:
    summary = to_billing_summary(group.summary)
    return GroupDto(**summary.model_dump(), key=group.key, label=label)


def _trip_ref(trip: TripMembershipRef) -> TripRefDto:
    return TripRefDto(id=trip.public_id, name=trip.name, membership=trip.membership.value)


def to_transaction_list_item(
    record: TransactionViewRecord, present_card: CardPresent
) -> TransactionListItemDto:
    has_installment = record.installment_number is not None and record.installment_total is not None
    return TransactionListItemDto(
        id=record.public_id,
        transaction_date=record.transaction_date.isoformat(),
        charge_date=None if record.charge_date is None else record.charge_date.isoformat(),
        merchant=record.merchant,
        city=record.city,
        country=record.country,
        category=CategoryDto(
            bank=record.bank_category,
            custom=record.custom_category,
            effective=record.effective_category,
            source=record.category_source.value,
        ),
        card=to_card_ref_dto(present_card(record.card)),
        type=record.transaction_type.value,
        installment=InstallmentDto(
            number=record.installment_number or 0, total=record.installment_total or 0
        )
        if has_installment
        else None,
        original=None
        if record.original_amount is None
        else to_money(record.original_amount, record.original_currency),
        billing=to_money(record.billing_amount, record.billing_currency),
        effective_rate=None
        if record.effective_rate is None
        else format_rate(record.effective_rate),
        commission=None
        if record.commission is None
        else to_money(record.commission, record.billing_currency),
        trip=None if record.trip is None else _trip_ref(record.trip),
        notes=record.notes,
        review_reasons=[reason.value for reason in record.visible_review_reasons],
    )


def _fx(record: TransactionViewRecord) -> FxDto | None:
    if record.effective_rate is None:
        return None
    effective = abs(record.effective_rate)
    stated = None if record.stated_fx_rate is None else abs(record.stated_fx_rate)
    deviation = (
        None
        if not stated
        else format(((effective - stated) / stated * 100).quantize(_PERCENT_PLACES), "f")
    )
    return FxDto(
        stated_rate=None if stated is None else format_rate(stated),
        effective_rate=format_rate(effective),
        deviation_percent=deviation,
        commission=to_money(record.commission or Decimal(0), record.billing_currency),
    )


def _installment_plan(record: TransactionViewRecord) -> InstallmentPlanDto | None:
    """The plan summary of an INSTALLMENTS row; None for other types or without number/total.

    - number, total: the row's installment number and total.
    - perPayment: round_amount(billing_amount), in the billing currency (sign as stored).
    - planTotal: plan_total_amount when stored, in plan_currency; otherwise
      perPayment × total, in the billing currency.
    - remainingPayments: max(total − number, 0).
    - remainingAmount: perPayment × remainingPayments, in the billing currency.
    """
    number, total = record.installment_number, record.installment_total
    if record.transaction_type is not TransactionType.INSTALLMENTS or not number or not total:
        return None
    per_payment = round_amount(record.billing_amount)
    plan_total = (
        per_payment * total if record.plan_total_amount is None else record.plan_total_amount
    )
    remaining = max(total - number, 0)
    currency = record.billing_currency if record.plan_total_amount is None else record.plan_currency
    return InstallmentPlanDto(
        number=number,
        total=total,
        plan_total=to_money(plan_total, currency),
        per_payment=to_money(per_payment, record.billing_currency),
        remaining_payments=remaining,
        remaining_amount=to_money(per_payment * remaining, record.billing_currency),
    )


def to_transaction_detail(
    record: TransactionViewRecord, present_card: CardPresent
) -> TransactionDetailDto:
    if record.imported_at is None:
        raise ValueError("a transaction detail needs the import time")
    return TransactionDetailDto(
        **to_transaction_list_item(record, present_card).model_dump(),
        fx=_fx(record),
        installment_plan=_installment_plan(record),
        imported_at=format_timestamp(record.imported_at),
    )
