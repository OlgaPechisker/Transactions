"""Calendar rules of the spending analysis: buckets, completeness, comparison ranges and the
monthly average. Pure functions over dates; "today" is always passed in (UTC).

Weeks start on Monday. A bucket is *complete* when all its calendar days lie inside the range
and it has ended before today."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from bank_transactions.domain.queries import Bucket

__all__ = [
    "DateRange",
    "MonthlyAverage",
    "Period",
    "bucket_end",
    "bucket_key",
    "bucket_start",
    "count_buckets",
    "monthly_average",
    "periods",
    "previous_range",
    "year_over_year_range",
]

_LAST_MONTH = 12
_FEBRUARY = 2
_LEAP_DAY = 29


@dataclass(frozen=True)
class DateRange:
    """An inclusive range of days."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("the range cannot end before it starts")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class Period:
    """One bucket of a range. `start`/`end` are the days of the bucket the range covers."""

    key: str
    start: date
    end: date
    complete: bool


@dataclass(frozen=True)
class MonthlyAverage:
    """`complete_months` counts the active complete calendar months (the divisor of `value`,
    None when there are none); `partial_months` lists the incomplete months of the range."""

    value: Decimal | None
    complete_months: int
    partial_months: tuple[str, ...]


def bucket_start(day: date, bucket: Bucket) -> date:
    """The first calendar day of the bucket holding `day`."""
    match bucket:
        case Bucket.DAY:
            return day
        case Bucket.WEEK:
            return day - timedelta(days=day.weekday())
        case Bucket.MONTH:
            return day.replace(day=1)
        case Bucket.YEAR:
            return day.replace(month=1, day=1)


def bucket_end(day: date, bucket: Bucket) -> date:
    """The last calendar day of the bucket holding `day`; the bucket that runs past the last
    representable day ends there."""
    try:
        return _next_start(bucket_start(day, bucket), bucket) - timedelta(days=1)
    except (OverflowError, ValueError):
        return date.max


def _next_start(first: date, bucket: Bucket) -> date:
    match bucket:
        case Bucket.DAY:
            return first + timedelta(days=1)
        case Bucket.WEEK:
            return first + timedelta(days=7)
        case Bucket.MONTH:
            if first.month == _LAST_MONTH:
                return first.replace(year=first.year + 1, month=1)
            return first.replace(month=first.month + 1)
        case Bucket.YEAR:
            return first.replace(year=first.year + 1)


def bucket_key(first: date, bucket: Bucket) -> str:
    """Day and week buckets are keyed by their first day, months by `YYYY-MM`, years by `YYYY`."""
    match bucket:
        case Bucket.DAY | Bucket.WEEK:
            return first.isoformat()
        case Bucket.MONTH:
            return f"{first.year:04d}-{first.month:02d}"
        case Bucket.YEAR:
            return f"{first.year:04d}"


def count_buckets(days: DateRange, bucket: Bucket) -> int:
    """How many buckets the range touches, without listing them."""
    first, last = days.start, days.end
    match bucket:
        case Bucket.DAY:
            return days.days
        case Bucket.WEEK:
            return (bucket_start(last, bucket) - bucket_start(first, bucket)).days // 7 + 1
        case Bucket.MONTH:
            return (last.year - first.year) * 12 + last.month - first.month + 1
        case Bucket.YEAR:
            return last.year - first.year + 1


def periods(days: DateRange, bucket: Bucket, today: date) -> tuple[Period, ...]:
    """Every bucket the range touches, in order, including the ones without transactions."""
    result: list[Period] = []
    first = bucket_start(days.start, bucket)
    while first <= days.end:
        last = bucket_end(first, bucket)
        result.append(
            Period(
                bucket_key(first, bucket),
                max(first, days.start),
                min(last, days.end),
                complete=first >= days.start and last <= days.end and last < today,
            )
        )
        if last >= days.end:
            break
        first = last + timedelta(days=1)
    return tuple(result)


def previous_range(days: DateRange) -> DateRange:
    """The same number of days immediately before the range. Raises ValueError before 0001-01-01."""
    try:
        end = days.start - timedelta(days=1)
        return DateRange(end - timedelta(days=days.days - 1), end)
    except OverflowError:
        raise ValueError("the previous range starts before the first representable day") from None


def year_over_year_range(days: DateRange) -> DateRange:
    """The range one year earlier; February 29 becomes February 28 in a non-leap year."""
    return DateRange(_a_year_earlier(days.start), _a_year_earlier(days.end))


def _a_year_earlier(day: date) -> date:
    year = day.year - 1
    if year < 1:
        raise ValueError("the year-over-year range starts before the first representable day")
    if day.month == _FEBRUARY and day.day == _LEAP_DAY:
        return date(year, _FEBRUARY, 28)  # the year before a leap year is never a leap year
    return day.replace(year=year)


def monthly_average(
    month_nets: Mapping[date, Decimal], days: DateRange, today: date
) -> MonthlyAverage:
    """The mean net of the active complete calendar months of the range. `month_nets` holds the
    net of every month with transactions, keyed by its first day. Months that are incomplete or
    have no transactions do not count; there is no average without a qualifying month."""
    complete = 0
    total = Decimal(0)
    partial: list[str] = []
    for period in periods(days, Bucket.MONTH, today):
        first = period.start.replace(day=1)
        if not period.complete:
            partial.append(period.key)
        elif first in month_nets:
            complete += 1
            total += month_nets[first]
    value = total / complete if complete else None
    return MonthlyAverage(value, complete, tuple(partial))
