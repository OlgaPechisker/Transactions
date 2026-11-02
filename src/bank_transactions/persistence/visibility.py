"""Shared SQL building blocks of every API read. Signatures are frozen by U0. Every query is built
from `visible_transactions`, so a user can never reach another user's rows.

`visible_unknown_currency()` and `api_original_currency()` take no arguments: they refer to the
`transactions` table, so apply them to a select built from `visible_transactions` (or
`transaction_view_select`) *before* it is turned into a subquery.
"""

from typing import Any

import sqlalchemy as sa
from sqlalchemy import Row, Select, Subquery, and_, case, exists, func, literal_column, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import FromClause

from bank_transactions.domain.enums import CategorySource, ReviewReason, TripMembership
from bank_transactions.domain.ids import CardId, IdentifierToken, PublicId, TransactionId, UserId
from bank_transactions.persistence.orm.models import (
    CardRow,
    CategoryMappingRow,
    CategoryOverrideRow,
    CategoryRuleRow,
    CategoryRuleTermRow,
    ClaimRow,
    CustomCategoryRow,
    ForeignDetailRow,
    InstallmentPlanRow,
    OwnerRow,
    ReviewReasonRow,
    TransactionRow,
    TripExclusionRow,
    TripRow,
    TripTagRow,
)
from bank_transactions.persistence.records import (
    TransactionViewRecord,
    TripMembershipRef,
    VisibleCardRecord,
)

__all__ = [
    "api_original_currency",
    "effective_category",
    "to_view_record",
    "transaction_view_select",
    "trip_membership",
    "visible_transactions",
    "visible_unknown_currency",
]

_t = TransactionRow.__table__
_cards = CardRow.__table__
_claims = ClaimRow.__table__
_owners = OwnerRow.__table__
_plans = InstallmentPlanRow.__table__
_reasons = ReviewReasonRow.__table__
_foreign = ForeignDetailRow.__table__
_categories = CustomCategoryRow.__table__
_overrides = CategoryOverrideRow.__table__
_mappings = CategoryMappingRow.__table__
_rules = CategoryRuleRow.__table__
_terms = CategoryRuleTermRow.__table__
_trips = TripRow.__table__
_tags = TripTagRow.__table__
_exclusions = TripExclusionRow.__table__


def _visible_join(user_id: UserId) -> FromClause:
    return _t.join(_cards, _cards.c.id == _t.c.card_id).join(
        _claims, and_(_claims.c.id == _cards.c.claim_id, _claims.c.user_id == user_id)
    )


def visible_transactions(user_id: UserId) -> Select[Any]:
    """The transactions columns, restricted to the user's claimed cards. `cards` and
    `card_ownership_claims` are in the FROM clause, so callers may filter on them too."""
    return select(_t).select_from(_visible_join(user_id))


def _is_foreign() -> ColumnElement[bool]:
    return (
        select(1)
        .select_from(_foreign)
        .where(_foreign.c.transaction_id == _t.c.id)
        .correlate(_t)
        .exists()
    )


def visible_unknown_currency() -> ColumnElement[bool]:
    """The transaction is pending a currency: it has an UNKNOWN_CURRENCY review reason, foreign
    details, and no original currency. A domestic row with the same reason is never visible."""
    has_reason = exists().where(
        _reasons.c.transaction_id == _t.c.id, _reasons.c.reason == ReviewReason.UNKNOWN_CURRENCY
    )
    return and_(has_reason, _is_foreign(), _t.c.original_currency.is_(None))


def api_original_currency() -> ColumnElement[str | None]:
    """The original currency as the API reports it (validated value, else the source one).
    It is foreign-only: NULL for domestic rows (§B.9 OQ-3), so a domestic transaction never
    reports an original currency, amount or country."""
    return case((_is_foreign(), _t.c.original_currency), else_=None)


def effective_category(user_id: UserId) -> Subquery:
    """Columns: transaction_id, bank, custom, effective, source. Override beats the newest
    matching rule, then the mapping, then the bank category."""
    override = _categories.alias("override_category")
    mapped = _categories.alias("mapped_category")
    matching_term = (
        select(1)
        .select_from(_terms)
        .where(
            _terms.c.rule_id == _rules.c.id,
            func.strpos(_t.c.merchant_name_norm, _terms.c.term_norm) > 0,
        )
        .correlate(_t, _rules)
        .exists()
    )
    rule = (
        select(_categories.c.name)
        .select_from(_rules.join(_categories, _categories.c.id == _rules.c.custom_category_id))
        .where(_rules.c.user_id == user_id, matching_term)
        .order_by(_rules.c.id.desc())
        .limit(1)
        .correlate(_t)
        .lateral("newest_rule")
    )
    source_tables = (
        _visible_join(user_id)
        .outerjoin(
            _overrides,
            and_(_overrides.c.transaction_id == _t.c.id, _overrides.c.user_id == user_id),
        )
        .outerjoin(override, override.c.id == _overrides.c.custom_category_id)
        .outerjoin(rule, sa.true())
        .outerjoin(
            _mappings,
            and_(
                _mappings.c.user_id == user_id,
                _mappings.c.bank_category_raw == _t.c.bank_category_raw,
            ),
        )
        .outerjoin(mapped, mapped.c.id == _mappings.c.custom_category_id)
    )
    custom = func.coalesce(override.c.name, rule.c.name, mapped.c.name)
    source = case(
        (override.c.name.is_not(None), literal_column("'OVERRIDE'")),
        (rule.c.name.is_not(None), literal_column("'RULE'")),
        (mapped.c.name.is_not(None), literal_column("'MAPPING'")),
        else_=literal_column("'BANK'"),
    )
    return (
        select(
            _t.c.id.label("transaction_id"),
            _t.c.bank_category_raw.label("bank"),
            custom.label("custom"),
            func.coalesce(custom, _t.c.bank_category_raw).label("effective"),
            source.label("source"),
        )
        .select_from(source_tables)
        .subquery("effective_category")
    )


def trip_membership(user_id: UserId) -> Subquery:
    """Columns: transaction_id, trip_id, trip_public_id, trip_name, membership. One trip per
    transaction; a tag wins over automatic membership; exclusions are removed. A transaction is
    an automatic member of a trip when its API-visible original currency is the trip's currency
    and its date falls in the trip's range, unless it is excluded or tagged to any trip."""
    excluded = exists().where(
        _exclusions.c.trip_id == _trips.c.id, _exclusions.c.transaction_id == _t.c.id
    )

    def labelled(membership: TripMembership) -> list[Any]:
        return [
            _t.c.id.label("transaction_id"),
            _trips.c.id.label("trip_id"),
            _trips.c.public_id.label("trip_public_id"),
            _trips.c.name.label("trip_name"),
            literal_column(f"'{membership.value}'").label("membership"),
        ]

    tagged = (
        select(*labelled(TripMembership.TAGGED))
        .select_from(
            _visible_join(user_id)
            .join(_tags, _tags.c.transaction_id == _t.c.id)
            .join(_trips, and_(_trips.c.id == _tags.c.trip_id, _trips.c.user_id == user_id))
        )
        .where(~excluded)
    )
    auto = (
        select(*labelled(TripMembership.AUTO))
        .select_from(
            _visible_join(user_id).join(
                _trips,
                and_(
                    _trips.c.user_id == user_id,
                    _trips.c.currency == api_original_currency(),
                    _t.c.transaction_date.between(_trips.c.start_date, _trips.c.end_date),
                ),
            )
        )
        .where(~excluded, ~exists().where(_tags.c.transaction_id == _t.c.id))
        .ext(distinct_on(_t.c.id))
        .order_by(_t.c.id, _trips.c.id)
    )
    return tagged.union_all(auto).subquery("trip_membership")


def transaction_view_select(user_id: UserId) -> Select[Any]:
    """`visible_transactions` joined with category, trip, card and review data: one row per
    `TransactionViewRecord`. Amounts of a domestic row never report an original side."""
    category = effective_category(user_id)
    trip = trip_membership(user_id)
    tables = (
        _visible_join(user_id)
        .join(_owners, _owners.c.id == _cards.c.owner_id)
        .outerjoin(_foreign, _foreign.c.transaction_id == _t.c.id)
        .outerjoin(_plans, _plans.c.id == _t.c.installment_plan_id)
        .outerjoin(category, category.c.transaction_id == _t.c.id)
        .outerjoin(trip, trip.c.transaction_id == _t.c.id)
    )
    other_reasons = (
        select(func.array_agg(sa.cast(_reasons.c.reason, sa.Text)))
        .where(
            _reasons.c.transaction_id == _t.c.id,
            _reasons.c.reason != ReviewReason.UNKNOWN_CURRENCY,
        )
        .scalar_subquery()
    )
    foreign = _foreign.c.transaction_id.is_not(None)
    return select(
        _t.c.id.label("transaction_id"),
        _t.c.public_id.label("public_id"),
        _t.c.transaction_date.label("transaction_date"),
        _t.c.charge_date.label("charge_date"),
        _t.c.merchant_name_raw.label("merchant"),
        _t.c.merchant_city.label("city"),
        _t.c.merchant_country.label("country"),
        category.c.bank.label("bank_category"),
        category.c.custom.label("custom_category"),
        category.c.effective.label("effective_category"),
        category.c.source.label("category_source"),
        _cards.c.id.label("card_id"),
        _cards.c.public_id.label("card_public_id"),
        _cards.c.issuer_code.label("card_issuer_code"),
        _cards.c.last4_enc.label("card_last4_enc"),
        _cards.c.display_name.label("card_nickname"),
        _owners.c.display_name.label("owner_display_name"),
        _t.c.transaction_type.label("transaction_type"),
        _t.c.installment_number.label("installment_number"),
        _plans.c.total_installments.label("installment_total"),
        case((foreign, _t.c.original_amount), else_=None).label("original_amount"),
        api_original_currency().label("original_currency"),
        _t.c.billing_amount.label("billing_amount"),
        _t.c.billing_currency.label("billing_currency"),
        _foreign.c.effective_fx_rate.label("effective_rate"),
        _foreign.c.commission_amount.label("commission"),
        _foreign.c.stated_fx_rate.label("stated_fx_rate"),
        trip.c.trip_public_id.label("trip_public_id"),
        trip.c.trip_name.label("trip_name"),
        trip.c.membership.label("trip_membership"),
        _t.c.notes.label("notes"),
        other_reasons.label("other_review_reasons"),
        visible_unknown_currency().label("unknown_currency_visible"),
        _t.c.created_at.label("imported_at"),
        _plans.c.total_amount.label("plan_total_amount"),
        _plans.c.currency.label("plan_currency"),
    ).select_from(tables)


def to_view_record(row: Row[Any]) -> TransactionViewRecord:
    reasons = {ReviewReason(reason) for reason in row.other_review_reasons or ()}
    if row.unknown_currency_visible:
        reasons.add(ReviewReason.UNKNOWN_CURRENCY)
    trip = (
        None
        if row.trip_public_id is None
        else TripMembershipRef(
            PublicId(row.trip_public_id), row.trip_name, TripMembership(row.trip_membership)
        )
    )
    return TransactionViewRecord(
        transaction_id=TransactionId(row.transaction_id),
        public_id=PublicId(row.public_id),
        transaction_date=row.transaction_date,
        charge_date=row.charge_date,
        merchant=row.merchant,
        city=row.city,
        country=row.country,
        bank_category=row.bank_category,
        custom_category=row.custom_category,
        effective_category=row.effective_category,
        category_source=CategorySource(row.category_source),
        card=VisibleCardRecord(
            card_id=CardId(row.card_id),
            public_id=PublicId(row.card_public_id),
            issuer_code=row.card_issuer_code,
            last4_enc=IdentifierToken(bytes(row.card_last4_enc)),
            nickname=row.card_nickname,
            owner_display_name=row.owner_display_name,
        ),
        transaction_type=row.transaction_type,
        installment_number=row.installment_number,
        installment_total=row.installment_total,
        original_amount=row.original_amount,
        original_currency=row.original_currency,
        billing_amount=row.billing_amount,
        billing_currency=row.billing_currency,
        effective_rate=row.effective_rate,
        commission=row.commission,
        trip=trip,
        notes=row.notes,
        visible_review_reasons=tuple(sorted(reasons)),
        stated_fx_rate=row.stated_fx_rate,
        imported_at=row.imported_at,
        plan_total_amount=row.plan_total_amount,
        plan_currency=row.plan_currency,
    )
