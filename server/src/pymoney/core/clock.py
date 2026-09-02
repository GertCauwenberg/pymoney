"""Clock abstraction (decision #10: post dates are plain dates, audit stamps
are UTC instants).

All audit stamps in the domain come from a :class:`Clock` rather than from
``datetime.now()`` directly.  That keeps the domain deterministic under test
and, later, lets the server be the single source of truth for time even when
the command was composed on a client with a skewed clock.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "ManualClock", "SystemClock", "utc_now"]


def utc_now() -> datetime:
    """Timezone-aware "now" in UTC."""
    return datetime.now(timezone.utc)


@runtime_checkable
class Clock(Protocol):
    """Source of audit timestamps."""

    def now(self) -> datetime:
        """Current instant, timezone-aware, UTC."""
        ...

    def today(self) -> date:
        """Current post date (UTC calendar day)."""
        ...


class SystemClock:
    """Production clock."""

    def now(self) -> datetime:
        return utc_now()

    def today(self) -> date:
        return utc_now().date()


class ManualClock:
    """Test clock with an explicit, advanceable instant."""

    def __init__(self, start: datetime | None = None) -> None:
        if start is None:
            start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        if start.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware datetime")
        self._now = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def advance(self, **kwargs: float) -> datetime:
        """Move the clock forward; kwargs are passed to :class:`timedelta`."""
        delta = timedelta(**kwargs)
        if delta < timedelta(0):
            raise ValueError("ManualClock only moves forward")
        self._now += delta
        return self._now

    def set(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware datetime")
        self._now = value.astimezone(timezone.utc)
        return self._now
