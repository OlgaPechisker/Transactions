"""Read side of the transaction list: filters, sorting, summaries, groups and filter options.

Every query starts from `transaction_view_select` / `visible_transactions` of `visibility.py`, so
another user's rows are unreachable. Group keys are computed in an inner select and grouped by
column in the outer one, because PostgreSQL rejects a GROUP BY expression whose bind parameters
differ from the selected one."""

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, Select, String, Subquery, case, cast, false, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from bank_transactions.domain.enums import ReviewReason, TransactionType
from bank_transactions.domain.ids import PublicId, TransactionId, UserId
from bank_transactions.domain.queries import (
    NONE,
    UNKNOWN,
    Dimension,
    Page,
    PageRequest,
    PendingValidationFilter,
    SortSpec,
    TransactionFilter,
)
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import TransactionQueryRepository
from bank_transactions.persistence.orm.models import TransactionRow
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    BillingTotalRecord,
    CodeCountRecord,
    FilterOptionsRecord,
    GroupRecord,
    NamedCountRecord,
    TransactionRef,
    TransactionViewRecord,
)
from bank_transactions.persistence.visibility import (
    api_original_currency,
    effective_category,
    to_view_record,
    transaction_view_select,
    visible_transactions,
    visible_unknown_currency,
)

__all__ = [
    "SqlTransactionQueryRepository",
    "contains_any",
    "filter_conditions",
    "group_key",
    "pending_filter_conditions",
    "summarize",
    "summary_statement",
]

# A sort field "group:<dimension>" orders by that dimension's group key, exactly like the groups
# themselves are ordered. The service puts it first when the list is grouped. The service keeps
# its own copy of the prefix (layering forbids importing this module): keep the two in step.
GROUP_SORT_PREFIX = "group:"

_t = TransactionRow.__table__

_PLAIN_SORT_COLUMNS = {
    "transactionDate": "transaction_date",
    "chargeDate": "charge_date",
    "merchant": "merchant",
    "country": "country",
    "city": "city",
    "category": "effective_category",
    "bankCategory": "bank_category",
    "originalAmount": "original_amount",
    "billingAmount": "billing_amount",
    "commission": "commission",
    "trip": "trip_name",
    "notes": "notes",
}


def contains_any(column: ColumnElement[Any], terms: Sequence[str]) -> ColumnElement[bool]:
    def pattern(term: str) -> str:
        return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    return or_(*(column.ilike(pattern(term), escape="\\") for term in terms))


def _in_or_none(
    column: ColumnElement[Any], values: Sequence[str], none_token: str
) -> ColumnElement[bool]:
    """`column IN values`, where `none_token` stands for "no value" (SQL NULL)."""
    clauses: list[ColumnElement[bool]] = []
    concrete = [value for value in values if value != none_token]
    if concrete:
        clauses.append(column.in_(concrete))
    if none_token in values:
        clauses.append(column.is_(None))
    return or_(*clauses)


def pending_filter_conditions(filter: PendingValidationFilter) -> list[ColumnElement[bool]]:
    """The narrowing of the validation queue and of a filter batch, on the transaction table.
    One definition, so the pending list and `lock_pending_matching` cannot drift apart."""
    where: list[ColumnElement[bool]] = []
    if filter.countries:
        where.append(_t.c.merchant_country.in_(filter.countries))
    if filter.date_from is not None:
        where.append(_t.c.transaction_date >= filter.date_from)
    if filter.date_to is not None:
        where.append(_t.c.transaction_date <= filter.date_to)
    if filter.merchants:
        where.append(contains_any(_t.c.merchant_name_raw, filter.merchants))
    return where


def filter_conditions(v: Subquery, filter: TransactionFilter) -> list[ColumnElement[bool]]:
    c: Any = v.c
    where: list[ColumnElement[bool]] = []
    if filter.date_from is not None:
        where.append(c.transaction_date >= filter.date_from)
    if filter.date_to is not None:
        where.append(c.transaction_date <= filter.date_to)
    if filter.categories:
        where.append(c.effective_category.in_(filter.categories))
    if filter.bank_categories:
        where.append(c.bank_category.in_(filter.bank_categories))
    if filter.custom_categories:
        where.append(_in_or_none(c.custom_category, filter.custom_categories, NONE))
    if filter.category_sources:
        where.append(c.category_source.in_([source.value for source in filter.category_sources]))
    if filter.card_ids:
        where.append(c.card_public_id.in_(filter.card_ids))
    if filter.currencies:
        codes = [code for code in filter.currencies if code != UNKNOWN]
        options: list[ColumnElement[bool]] = []
        if codes:
            options.append(c.original_currency.in_(codes))
        if UNKNOWN in filter.currencies:
            options.append(c.unknown_currency_visible)
        where.append(or_(*options))
    if filter.billing_currency is not None:
        where.append(c.billing_currency == filter.billing_currency)
    if filter.min_amount is not None:
        where.append(c.billing_amount >= filter.min_amount)
    if filter.max_amount is not None:
        where.append(c.billing_amount <= filter.max_amount)
    if filter.merchants:
        where.append(contains_any(c.merchant, filter.merchants))
    if filter.types:
        where.append(c.transaction_type.in_(filter.types))
    if filter.countries:
        where.append(c.country.in_(filter.countries))
    if filter.trip_ids:
        where.append(_in_or_none(c.trip_public_id, filter.trip_ids, NONE))
    if filter.needs_review is not None:
        pending = c.unknown_currency_visible
        where.append(pending if filter.needs_review else ~pending)
    if filter.review_reasons:
        # UNKNOWN_CURRENCY is the only reason the API exposes; any other one matches nothing.
        matches = ReviewReason.UNKNOWN_CURRENCY in filter.review_reasons
        where.append(c.unknown_currency_visible if matches else false())
    return where


def group_key(c: Any, dimension: Dimension) -> ColumnElement[Any]:
    """The key of the row's group, NULL when the row has no value in that dimension. A key can
    be sent back as the matching filter value."""
    match dimension:
        case Dimension.CATEGORY:
            return c.effective_category  # type: ignore[no-any-return]
        case Dimension.CUSTOM_CATEGORY:
            return c.custom_category  # type: ignore[no-any-return]
        case Dimension.DAY:
            return func.to_char(c.transaction_date, "YYYY-MM-DD")
        case Dimension.WEEK:
            return func.to_char(func.date_trunc("week", c.transaction_date), "YYYY-MM-DD")
        case Dimension.MONTH:
            return func.to_char(c.transaction_date, "YYYY-MM")
        case Dimension.YEAR:
            return func.to_char(c.transaction_date, "YYYY")
        case Dimension.CARD:
            return c.card_public_id  # type: ignore[no-any-return]
        case Dimension.CURRENCY:
            # Pending rows are "UNKNOWN" (the currency filter value); domestic rows have no key.
            return case(
                (c.unknown_currency_visible, UNKNOWN), else_=cast(c.original_currency, String)
            )
        case Dimension.MERCHANT:
            return c.merchant  # type: ignore[no-any-return]
        case Dimension.TYPE:
            return cast(c.transaction_type, String)
        case Dimension.TRIP:
            return c.trip_public_id  # type: ignore[no-any-return]
        case Dimension.COUNTRY:
            return c.country  # type: ignore[no-any-return]


def _sort_expression(c: Any, field: str) -> ColumnElement[Any]:
    if field.startswith(GROUP_SORT_PREFIX):
        return group_key(c, Dimension(field.removeprefix(GROUP_SORT_PREFIX)))
    if field in _PLAIN_SORT_COLUMNS:
        return getattr(c, _PLAIN_SORT_COLUMNS[field])  # type: ignore[no-any-return]
    match field:
        case "card":
            return cast(c.card_issuer_code, String)
        case "type":
            return cast(c.transaction_type, String)
        case "originalCurrency":
            return cast(c.original_currency, String)
        case "effectiveRate":
            # The API reports the rate as a positive number.
            return func.abs(c.effective_rate)
        case "reviewCount":
            return cast(c.unknown_currency_visible, Integer)
    raise ValueError(f"unsupported sort field: {field}")


def _order_by(c: Any, sort: SortSpec) -> list[ColumnElement[Any]]:
    """Nulls sort last in both directions; the transaction id breaks ties so pages are stable."""
    ordering: list[ColumnElement[Any]] = []
    for field, descending in sort.fields:
        expressions = [_sort_expression(c, field)]
        if field == "card":
            expressions.append(c.card_id)
        ordering.extend(
            expression.desc().nulls_last() if descending else expression.asc().nulls_last()
            for expression in expressions
        )
    ordering.append(c.transaction_id.asc())
    return ordering


def summary_statement(filtered: Subquery, keyed: bool) -> Select[Any]:
    c: Any = filtered.c
    columns = [c.group_key, c.billing_currency] if keyed else [c.billing_currency]
    return (
        select(
            *columns,
            func.count().label("rows"),
            func.sum(c.billing_amount).label("net"),
            func.count().filter(c.unknown_currency_visible).label("unknown"),
        )
        .group_by(*columns)
        .order_by(*(column.asc().nulls_last() for column in columns))
    )


def summarize(rows: Sequence[Any], keyed: bool) -> dict[str | None, BillingSummaryRecord]:
    """One summary per group key, in the order of the rows (a single None key when not keyed).
    The rows are the result of `summary_statement`."""
    totals: dict[str | None, list[BillingTotalRecord]] = defaultdict(list)
    counts: dict[str | None, int] = defaultdict(int)
    unknown_billing: dict[str | None, int] = defaultdict(int)
    unknown_currency: dict[str | None, int] = defaultdict(int)
    for row in rows:
        key = row.group_key if keyed else None
        counts[key] += row.rows
        unknown_currency[key] += row.unknown
        if row.billing_currency is None:
            unknown_billing[key] += row.rows
        else:
            totals[key].append(BillingTotalRecord(row.billing_currency, row.rows, Decimal(row.net)))
    return {
        key: BillingSummaryRecord(
            count=count,
            unknown_billing_currency_count=unknown_billing[key],
            totals=tuple(totals[key]),
            unknown_currency_count=unknown_currency[key],
        )
        for key, count in counts.items()
    }


class SqlTransactionQueryRepository(TransactionQueryRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _view(user_id: UserId) -> Subquery:
        return transaction_view_select(user_id).subquery("v")

    @translates_database_errors
    def page(
        self, user_id: UserId, filter: TransactionFilter, sort: SortSpec, page: PageRequest
    ) -> Page[TransactionViewRecord]:
        v = self._view(user_id)
        where = filter_conditions(v, filter)
        total = self._session.execute(
            select(func.count()).select_from(v).where(*where)
        ).scalar_one()
        rows = self._session.execute(
            select(v)
            .where(*where)
            .order_by(*_order_by(v.c, sort))
            .limit(page.size)
            .offset(page.offset)
        ).all()
        return Page(tuple(to_view_record(row) for row in rows), page.page, page.size, total)

    @translates_database_errors
    def summary(self, user_id: UserId, filter: TransactionFilter) -> BillingSummaryRecord:
        v = self._view(user_id)
        filtered = select(v).where(*filter_conditions(v, filter)).subquery("filtered")
        rows = self._session.execute(summary_statement(filtered, keyed=False)).all()
        return summarize(rows, keyed=False).get(None, BillingSummaryRecord(0, 0))

    @translates_database_errors
    def groups(
        self, user_id: UserId, filter: TransactionFilter, dimension: Dimension
    ) -> tuple[GroupRecord, ...]:
        v = self._view(user_id)
        filtered = (
            select(v, group_key(v.c, dimension).label("group_key"))
            .where(*filter_conditions(v, filter))
            .subquery("filtered")
        )
        rows = self._session.execute(summary_statement(filtered, keyed=True)).all()
        return tuple(GroupRecord(key, summary) for key, summary in summarize(rows, True).items())

    @translates_database_errors
    def get(self, user_id: UserId, public_id: PublicId) -> TransactionViewRecord | None:
        row = self._session.execute(
            transaction_view_select(user_id).where(_t.c.public_id == public_id)
        ).one_or_none()
        return None if row is None else to_view_record(row)

    @translates_database_errors
    def visible_id(self, user_id: UserId, public_id: PublicId) -> TransactionId | None:
        found = self._session.execute(
            visible_transactions(user_id)
            .with_only_columns(_t.c.id)
            .where(_t.c.public_id == public_id)
        ).scalar_one_or_none()
        return None if found is None else TransactionId(found)

    @translates_database_errors
    def lock_visible(
        self, user_id: UserId, public_ids: Sequence[PublicId]
    ) -> tuple[TransactionRef, ...]:
        if not public_ids:
            return ()
        return self._locked(
            visible_transactions(user_id)
            .with_only_columns(_t.c.id, _t.c.public_id)
            .where(_t.c.public_id.in_(list(public_ids)))
        )

    @translates_database_errors
    def lock_pending_matching(
        self, user_id: UserId, filter: PendingValidationFilter
    ) -> tuple[TransactionRef, ...]:
        statement = (
            visible_transactions(user_id)
            .with_only_columns(_t.c.id, _t.c.public_id)
            .where(visible_unknown_currency(), *pending_filter_conditions(filter))
        )
        return self._locked(statement)

    def _locked(self, statement: Select[Any]) -> tuple[TransactionRef, ...]:
        rows = self._session.execute(statement.order_by(_t.c.id).with_for_update(of=_t)).all()
        return tuple(TransactionRef(TransactionId(row.id), PublicId(row.public_id)) for row in rows)

    @translates_database_errors
    def filter_options(self, user_id: UserId) -> FilterOptionsRecord:
        custom = effective_category(user_id)
        custom_counts = self._session.execute(
            select(custom.c.custom, func.count())
            .where(custom.c.custom.is_not(None))
            .group_by(custom.c.custom)
            .order_by(custom.c.custom)
        ).all()
        date_min, date_max, unknown_currency, unknown_billing = self._session.execute(
            visible_transactions(user_id).with_only_columns(
                func.min(_t.c.transaction_date),
                func.max(_t.c.transaction_date),
                func.count().filter(visible_unknown_currency()),
                func.count().filter(_t.c.billing_currency.is_(None)),
            )
        ).one()
        present_types = {kind for kind, _ in self._counts(user_id, _t.c.transaction_type)}
        return FilterOptionsRecord(
            bank_categories=self._named(user_id),
            custom_categories=tuple(NamedCountRecord(name, count) for name, count in custom_counts),
            cards=(),  # the service fills them in from the visible-card repository
            original_currencies=self._coded(user_id, api_original_currency()),
            billing_currencies=self._coded(user_id, _t.c.billing_currency),
            countries=self._coded(user_id, _t.c.merchant_country),
            types=tuple(kind for kind in TransactionType if kind in present_types),
            unknown_currency_count=unknown_currency,
            unknown_billing_currency_count=unknown_billing,
            date_min=date_min,
            date_max=date_max,
        )

    def _counts(self, user_id: UserId, expression: ColumnElement[Any]) -> list[tuple[Any, int]]:
        """(value, rows) of the visible rows with a value, ordered by value."""
        inner = (
            visible_transactions(user_id).with_only_columns(expression.label("value")).subquery()
        )
        statement = (
            select(inner.c.value, func.count())
            .where(inner.c.value.is_not(None))
            .group_by(inner.c.value)
            .order_by(inner.c.value)
        )
        return [(row[0], row[1]) for row in self._session.execute(statement)]

    def _coded(
        self, user_id: UserId, expression: ColumnElement[Any]
    ) -> tuple[CodeCountRecord, ...]:
        return tuple(
            CodeCountRecord(code, count) for code, count in self._counts(user_id, expression)
        )

    def _named(self, user_id: UserId) -> tuple[NamedCountRecord, ...]:
        counts = self._counts(user_id, _t.c.bank_category_raw)
        return tuple(NamedCountRecord(name, count) for name, count in counts)

    @translates_database_errors
    def bank_category_counts(self, user_id: UserId) -> tuple[NamedCountRecord, ...]:
        return self._named(user_id)

    @translates_database_errors
    def has_bank_category(self, user_id: UserId, bank_category: str) -> bool:
        return bool(
            self._session.execute(
                select(
                    visible_transactions(user_id)
                    .where(_t.c.bank_category_raw == bank_category)
                    .exists()
                )
            ).scalar_one()
        )
