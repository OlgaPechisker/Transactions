from datetime import UTC, datetime, timedelta

from bank_transactions.ports.clock import Clock


class FakeClock(Clock):
    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2025, 1, 15, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
