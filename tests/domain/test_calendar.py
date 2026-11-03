from datetime import date
from decimal import Decimal

import pytest

from bank_transactions.domain.calendar import (
    DateRange,
    bucket_key,
    bucket_start,
    count_buckets,
    monthly_average,
    periods,
    previous_range,
    year_over_year_range,
)
from bank_transactions.domain.queries import Bucket

TODAY = date(2025, 3, 15)


def keys(days: DateRange, bucket: Bucket, today: date = TODAY) -> list[str]:
    return [period.key for period in periods(days, bucket, today)]


def completeness(days: DateRange, bucket: Bucket, today: date = TODAY) -> list[bool]:
    return [period.complete for period in periods(days, bucket, today)]


def test_a_range_cannot_end_before_it_starts():
    with pytest.raises(ValueError):
        DateRange(date(2025, 2, 2), date(2025, 2, 1))


class TestBuckets:
    def test_weeks_start_on_monday(self):
        sunday, monday = date(2025, 3, 9), date(2025, 3, 10)

        assert bucket_start(sunday, Bucket.WEEK) == date(2025, 3, 3)
        assert bucket_start(monday, Bucket.WEEK) == monday
        assert bucket_start(date(2025, 3, 16), Bucket.WEEK) == monday

    def test_keys_name_the_first_day_month_or_year(self):
        first = date(2025, 3, 10)

        assert bucket_key(first, Bucket.DAY) == "2025-03-10"
        assert bucket_key(first, Bucket.WEEK) == "2025-03-10"
        assert bucket_key(first.replace(day=1), Bucket.MONTH) == "2025-03"
        assert bucket_key(first.replace(month=1, day=1), Bucket.YEAR) == "2025"

    def test_every_bucket_of_the_range_is_listed_including_the_empty_ones(self):
        days = DateRange(date(2025, 1, 31), date(2025, 4, 1))

        assert keys(days, Bucket.MONTH) == ["2025-01", "2025-02", "2025-03", "2025-04"]
        assert keys(DateRange(date(2024, 12, 31), date(2026, 1, 1)), Bucket.YEAR) == [
            "2024",
            "2025",
            "2026",
        ]
        assert keys(DateRange(date(2025, 2, 27), date(2025, 3, 2)), Bucket.DAY) == [
            "2025-02-27",
            "2025-02-28",
            "2025-03-01",
            "2025-03-02",
        ]

    def test_weeks_across_a_year_boundary(self):
        days = DateRange(date(2024, 12, 30), date(2025, 1, 12))

        assert keys(days, Bucket.WEEK) == ["2024-12-30", "2025-01-06"]

    def test_the_first_and_last_bucket_are_clipped_to_the_range(self):
        days = DateRange(date(2025, 1, 15), date(2025, 2, 10))

        january, february = periods(days, Bucket.MONTH, TODAY)

        assert (january.start, january.end) == (date(2025, 1, 15), date(2025, 1, 31))
        assert (february.start, february.end) == (date(2025, 2, 1), date(2025, 2, 10))

    def test_a_single_day_range_has_one_bucket_of_every_kind(self):
        day = DateRange(date(2025, 2, 28), date(2025, 2, 28))

        assert [count_buckets(day, bucket) for bucket in Bucket] == [1, 1, 1, 1]
        assert [len(periods(day, bucket, TODAY)) for bucket in Bucket] == [1, 1, 1, 1]

    @pytest.mark.parametrize("bucket", list(Bucket))
    def test_the_count_matches_the_listed_buckets(self, bucket):
        days = DateRange(date(2023, 12, 20), date(2025, 2, 3))

        assert count_buckets(days, bucket) == len(periods(days, bucket, TODAY))

    def test_the_last_day_of_the_calendar_still_has_a_bucket(self):
        days = DateRange(date(9999, 12, 25), date(9999, 12, 31))

        assert keys(days, Bucket.YEAR, TODAY) == ["9999"]
        assert keys(days, Bucket.MONTH, TODAY) == ["9999-12"]


class TestCompleteness:
    def test_a_month_is_complete_only_when_the_range_covers_all_of_it(self):
        days = DateRange(date(2025, 1, 2), date(2025, 2, 28))

        assert completeness(days, Bucket.MONTH) == [False, True]

    def test_a_week_cut_by_the_range_is_partial(self):
        days = DateRange(date(2025, 2, 5), date(2025, 2, 23))

        assert keys(days, Bucket.WEEK) == ["2025-02-03", "2025-02-10", "2025-02-17"]
        assert completeness(days, Bucket.WEEK) == [False, True, True]

    def test_the_bucket_holding_today_is_partial_even_inside_the_range(self):
        days = DateRange(date(2025, 3, 1), date(2025, 3, 31))

        assert completeness(days, Bucket.MONTH, today=date(2025, 3, 15)) == [False]
        assert completeness(days, Bucket.MONTH, today=date(2025, 3, 31)) == [False]
        assert completeness(days, Bucket.MONTH, today=date(2025, 4, 1)) == [True]

    def test_today_itself_is_never_a_complete_day(self):
        days = DateRange(date(2025, 3, 14), date(2025, 3, 16))

        assert completeness(days, Bucket.DAY, today=date(2025, 3, 15)) == [True, False, False]

    def test_buckets_in_the_future_are_partial(self):
        days = DateRange(date(2025, 12, 1), date(2025, 12, 31))

        assert completeness(days, Bucket.MONTH) == [False]


class TestComparisonRanges:
    def test_previous_is_the_same_number_of_days_right_before(self):
        days = DateRange(date(2025, 3, 1), date(2025, 3, 31))

        assert previous_range(days) == DateRange(date(2025, 1, 29), date(2025, 2, 28))

    def test_previous_of_a_single_day_is_the_day_before(self):
        day = date(2025, 3, 1)

        assert previous_range(DateRange(day, day)) == DateRange(
            date(2025, 2, 28), date(2025, 2, 28)
        )

    def test_previous_before_the_first_calendar_day_is_rejected(self):
        with pytest.raises(ValueError):
            previous_range(DateRange(date(1, 1, 1), date(1, 1, 31)))

    def test_year_over_year_is_the_same_dates_one_year_earlier(self):
        days = DateRange(date(2025, 3, 1), date(2025, 3, 31))

        assert year_over_year_range(days) == DateRange(date(2024, 3, 1), date(2024, 3, 31))

    def test_year_over_year_maps_february_29_to_february_28(self):
        days = DateRange(date(2024, 2, 29), date(2024, 3, 10))

        assert year_over_year_range(days) == DateRange(date(2023, 2, 28), date(2023, 3, 10))

    def test_year_over_year_of_a_leap_range_end_keeps_the_range_valid(self):
        days = DateRange(date(2024, 2, 28), date(2024, 2, 29))

        assert year_over_year_range(days) == DateRange(date(2023, 2, 28), date(2023, 2, 28))

    def test_year_over_year_before_the_first_calendar_year_is_rejected(self):
        with pytest.raises(ValueError):
            year_over_year_range(DateRange(date(1, 5, 1), date(1, 5, 2)))


class TestMonthlyAverage:
    def test_it_averages_the_active_complete_months_only(self):
        days = DateRange(date(2025, 1, 1), date(2025, 3, 31))
        nets = {
            date(2025, 1, 1): Decimal("100"),
            date(2025, 2, 1): Decimal("50"),
            date(2025, 3, 1): Decimal("999"),
        }

        average = monthly_average(nets, days, TODAY)

        assert average.value == Decimal("75")
        assert average.complete_months == 2
        assert average.partial_months == ("2025-03",)

    def test_a_complete_month_without_transactions_does_not_count(self):
        days = DateRange(date(2025, 1, 1), date(2025, 2, 28))

        average = monthly_average({date(2025, 2, 1): Decimal("-30")}, days, TODAY)

        assert (average.value, average.complete_months) == (Decimal("-30"), 1)

    def test_a_partial_month_does_not_count_even_with_transactions(self):
        days = DateRange(date(2025, 1, 10), date(2025, 2, 28))
        nets = {date(2025, 1, 1): Decimal("999"), date(2025, 2, 1): Decimal("20")}

        average = monthly_average(nets, days, TODAY)

        assert (average.value, average.complete_months) == (Decimal("20"), 1)
        assert average.partial_months == ("2025-01",)

    def test_there_is_no_average_without_an_active_complete_month(self):
        days = DateRange(date(2025, 3, 1), date(2025, 3, 20))

        average = monthly_average({date(2025, 3, 1): Decimal("10")}, days, TODAY)

        assert (average.value, average.complete_months) == (None, 0)
        assert average.partial_months == ("2025-03",)
