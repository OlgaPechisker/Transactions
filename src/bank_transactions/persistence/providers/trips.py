"""Trips and their membership. Membership is always read through `trip_membership` of
`visibility.py` (directly or through `transaction_view_select`), so the automatic-member rule
lives in one place."""

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import ScalarSelect, Select, exists, func, insert, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from bank_transactions.domain.enums import TripMembership
from bank_transactions.domain.ids import PublicId, TransactionId, TripId, UserId
from bank_transactions.domain.queries import SortSpec
from bank_transactions.persistence.commands import NewTrip
from bank_transactions.persistence.db import translates_database_errors
from bank_transactions.persistence.interfaces import TripRepository
from bank_transactions.persistence.orm.models import TransactionRow, TripExclusionRow, TripRow
from bank_transactions.persistence.providers.transaction_query import (
    summarize,
    summary_statement,
)
from bank_transactions.persistence.public_ids import new_public_id
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    DayBreakdownRecord,
    GroupRecord,
    TransactionViewRecord,
    TripBreakdownRecord,
    TripMembersRecord,
    TripRecord,
    TripSummaryRecord,
)
from bank_transactions.persistence.visibility import (
    to_view_record,
    transaction_view_select,
    trip_membership,
    visible_unknown_currency,
)

_t = TransactionRow.__table__
_trips = TripRow.__table__
_exclusions = TripExclusionRow.__table__

_RECORD_COLUMNS = (
    _trips.c.id,
    _trips.c.public_id,
    _trips.c.name,
    _trips.c.currency,
    _trips.c.start_date,
    _trips.c.end_date,
)
_SORT_COLUMNS: dict[str, ColumnElement[Any]] = {
    "startDate": _trips.c.start_date,
    "name": func.lower(_trips.c.name),
}
_EMPTY_SUMMARY = BillingSummaryRecord(0, 0)
_DAY_FORMAT = "YYYY-MM-DD"


def _record(row: Any) -> TripRecord:
    return TripRecord(
        trip_id=TripId(row.id),
        public_id=PublicId(row.public_id),
        name=row.name,
        currency=row.currency,
        start_date=row.start_date,
        end_date=row.end_date,
    )


class SqlTripRepository(TripRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    @translates_database_errors
    def list_with_stats(self, user_id: UserId, sort: SortSpec) -> tuple[TripSummaryRecord, ...]:
        ordering: list[ColumnElement[Any]] = []
        for field, descending in sort.fields:
            column = _SORT_COLUMNS[field]
            ordering.append(column.desc() if descending else column.asc())
        ordering.append(_trips.c.id.asc())
        trips = self._session.execute(
            select(*_RECORD_COLUMNS).where(_trips.c.user_id == user_id).order_by(*ordering)
        ).all()

        membership = trip_membership(user_id)
        facts = (
            select(
                membership.c.trip_public_id.label("group_key"),
                _t.c.billing_currency,
                _t.c.billing_amount,
                visible_unknown_currency().label("unknown_currency_visible"),
            )
            .select_from(membership.join(_t, _t.c.id == membership.c.transaction_id))
            .subquery("facts")
        )
        summaries = summarize(self._session.execute(summary_statement(facts, True)).all(), True)
        counts: dict[str, dict[str, int]] = defaultdict(dict)
        for row in self._session.execute(
            select(membership.c.trip_public_id, membership.c.membership, func.count()).group_by(
                membership.c.trip_public_id, membership.c.membership
            )
        ):
            counts[row[0]][row[1]] = row[2]

        return tuple(
            TripSummaryRecord(
                trip=record,
                summary=summaries.get(record.public_id, _EMPTY_SUMMARY),
                tagged_count=counts[record.public_id].get(TripMembership.TAGGED, 0),
                auto_count=counts[record.public_id].get(TripMembership.AUTO, 0),
            )
            for record in map(_record, trips)
        )

    @translates_database_errors
    def list(self, user_id: UserId) -> tuple[TripRecord, ...]:
        rows = self._session.execute(
            select(*_RECORD_COLUMNS)
            .where(_trips.c.user_id == user_id)
            .order_by(_trips.c.start_date.desc(), _trips.c.id.desc())
        ).all()
        return tuple(map(_record, rows))

    @translates_database_errors
    def get(self, user_id: UserId, public_id: PublicId) -> TripRecord | None:
        row = self._session.execute(
            select(*_RECORD_COLUMNS).where(
                _trips.c.user_id == user_id, _trips.c.public_id == public_id
            )
        ).one_or_none()
        return None if row is None else _record(row)

    @translates_database_errors
    def get_id(self, user_id: UserId, public_id: PublicId) -> TripId | None:
        trip_id = self._session.execute(
            select(_trips.c.id).where(_trips.c.user_id == user_id, _trips.c.public_id == public_id)
        ).scalar_one_or_none()
        return None if trip_id is None else TripId(trip_id)

    @translates_database_errors
    def exists_overlap(self, user_id: UserId, start_date: date, end_date: date) -> bool:
        return bool(
            self._session.execute(
                select(
                    exists().where(
                        _trips.c.user_id == user_id,
                        _trips.c.start_date <= end_date,
                        _trips.c.end_date >= start_date,
                    )
                )
            ).scalar_one()
        )

    @translates_database_errors
    def create(self, trip: NewTrip) -> TripRecord:
        row = self._session.execute(
            insert(TripRow)
            .values(
                public_id=new_public_id("trp"),
                user_id=trip.user_id,
                name=trip.name,
                currency=trip.currency,
                start_date=trip.start_date,
                end_date=trip.end_date,
            )
            .returning(*_RECORD_COLUMNS)
        ).one()
        return _record(row)

    @translates_database_errors
    def members(self, user_id: UserId, trip_id: TripId) -> TripMembersRecord:
        view = transaction_view_select(user_id).subquery("members")
        in_trip = view.c.trip_public_id == self._public_id(user_id, trip_id)

        def rows(*conditions: ColumnElement[bool]) -> tuple[TransactionViewRecord, ...]:
            statement = (
                select(view)
                .where(*conditions)
                .order_by(view.c.transaction_date.asc(), view.c.transaction_id.asc())
            )
            return tuple(to_view_record(row) for row in self._session.execute(statement))

        return TripMembersRecord(
            tagged=rows(in_trip, view.c.trip_membership == TripMembership.TAGGED.value),
            auto=rows(in_trip, view.c.trip_membership == TripMembership.AUTO.value),
            excluded=rows(
                view.c.transaction_id.in_(
                    select(_exclusions.c.transaction_id).where(_exclusions.c.trip_id == trip_id)
                )
            ),
        )

    @translates_database_errors
    def breakdowns(self, user_id: UserId, trip_id: TripId) -> TripBreakdownRecord:
        dates = self._session.execute(
            select(_trips.c.start_date, _trips.c.end_date).where(
                _trips.c.user_id == user_id, _trips.c.id == trip_id
            )
        ).one_or_none()
        if dates is None:
            return TripBreakdownRecord(_EMPTY_SUMMARY, _EMPTY_SUMMARY)

        view = transaction_view_select(user_id).subquery("members")
        in_trip = view.c.trip_public_id == self._public_id(user_id, trip_id)

        def summaries(
            *conditions: ColumnElement[bool], key: ColumnElement[Any] | None = None
        ) -> dict[str | None, BillingSummaryRecord]:
            columns: list[Any] = [view]
            if key is not None:
                columns.append(key.label("group_key"))
            filtered = select(*columns).where(in_trip, *conditions).subquery("filtered")
            rows = self._session.execute(summary_statement(filtered, key is not None)).all()
            return summarize(rows, key is not None)

        outside = or_(
            view.c.transaction_date < dates.start_date, view.c.transaction_date > dates.end_date
        )
        by_category = summaries(key=view.c.effective_category)
        by_day = summaries(key=func.to_char(view.c.transaction_date, _DAY_FORMAT))
        return TripBreakdownRecord(
            overall=summaries().get(None, _EMPTY_SUMMARY),
            outside_range=summaries(outside).get(None, _EMPTY_SUMMARY),
            by_category=tuple(GroupRecord(key, summary) for key, summary in by_category.items()),
            by_day=tuple(
                DayBreakdownRecord(date.fromisoformat(str(key)), summary)
                for key, summary in by_day.items()
            ),
        )

    @translates_database_errors
    def candidates(
        self,
        user_id: UserId,
        trip_id: TripId,
        window: tuple[date, date] | None,
        query: str | None,
        limit: int,
    ) -> tuple[TransactionViewRecord, ...]:
        view = transaction_view_select(user_id).subquery("candidates")
        members = trip_membership(user_id)
        conditions: list[ColumnElement[bool]] = [
            view.c.transaction_id.not_in(
                select(members.c.transaction_id).where(members.c.trip_id == trip_id)
            )
        ]
        if window is not None:
            conditions.append(view.c.transaction_date.between(*window))
        if query is not None:
            conditions.append(
                or_(
                    view.c.merchant.icontains(query, autoescape=True),
                    func.to_char(view.c.transaction_date, _DAY_FORMAT).startswith(
                        query, autoescape=True
                    ),
                    view.c.effective_category.icontains(query, autoescape=True),
                )
            )
        statement: Select[Any] = (
            select(view)
            .where(*conditions)
            .order_by(view.c.transaction_date.desc(), view.c.transaction_id.asc())
            .limit(limit)
        )
        return tuple(to_view_record(row) for row in self._session.execute(statement))

    @translates_database_errors
    def is_auto_member(
        self, user_id: UserId, trip_id: TripId, transaction_id: TransactionId
    ) -> bool:
        membership = trip_membership(user_id)
        return bool(
            self._session.execute(
                select(
                    exists().where(
                        membership.c.transaction_id == transaction_id,
                        membership.c.trip_id == trip_id,
                        membership.c.membership == TripMembership.AUTO.value,
                    )
                )
            ).scalar_one()
        )

    @staticmethod
    def _public_id(user_id: UserId, trip_id: TripId) -> ScalarSelect[str]:
        return (
            select(_trips.c.public_id)
            .where(_trips.c.user_id == user_id, _trips.c.id == trip_id)
            .scalar_subquery()
        )
