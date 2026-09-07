"""Recurrence rules: a small, exact subset of RFC 5545 (decision: new).

Enough to describe rent, a salary, a subscription and an annual insurance
premium, and nothing more:

* ``DAILY`` / ``WEEKLY`` / ``MONTHLY`` / ``YEARLY``, every ``interval`` units;
* ``WEEKLY`` repeats on the start date's weekday;
* ``MONTHLY`` repeats on the start date's *day of the month* (clamped to the
  month's length, so the 31st becomes the 30th or the 28th and never drifts),
  or -- with ``monthly_by_weekday`` -- on its *nth weekday* (a "third Friday",
  last one of the month if the count runs out);
* it ends ``after`` a fixed number of occurrences, or ``until`` a date
  (inclusive), or never.

Everything is computed from ``start`` and an occurrence index, so the *k*-th
date is a pure function -- no accumulated error, and a schedule's cursor can
always be recomputed from how many occurrences it has consumed.

No floats, no clock: a :class:`Recurrence` is data. "Is this due?" belongs to
the :class:`~pymoney.core.schedule.Schedule` that owns the cursor and a date.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date as _Date
from datetime import timedelta
from enum import StrEnum
from typing import Any

from .errors import ValidationError

__all__ = ["Frequency", "Recurrence", "add_months"]

_MAX_SCAN = 10_000
"""A guard: no real schedule needs this many occurrences to answer a query,
and an unbounded generator must never run away."""


class Frequency(StrEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    YEARLY = "YEARLY"


def add_months(anchor: _Date, months: int) -> _Date:
    """``anchor`` shifted by ``months``, its day clamped to the target month.

    ``add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)`` -- and a *later*
    step lands back on the 31st where the month allows, because every
    occurrence is measured from the original day, never from the clamped one.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return _Date(year, month, day)


def _nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> _Date:
    """The ``nth`` (1-based) ``weekday`` of a month; the last one if ``nth`` is
    past the end or is 5 and the month only has four."""
    first = _Date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    day = 1 + offset + (nth - 1) * 7
    last_of_kind = (
        1 + offset + ((calendar.monthrange(year, month)[1] - 1 - offset) // 7) * 7
    )
    return _Date(year, month, min(day, last_of_kind))


@dataclass(frozen=True, slots=True, kw_only=True)
class Recurrence:
    """When a schedule fires. Immutable; the k-th occurrence is a function of
    ``start`` and ``k``."""

    frequency: Frequency
    start: _Date
    """The first occurrence."""
    interval: int = 1
    """Every ``interval`` units (2 = fortnightly for ``WEEKLY``)."""
    monthly_by_weekday: bool = False
    """``MONTHLY`` only: repeat on the nth weekday of the month (the "third
    Friday") rather than on a day number."""
    count: int | None = None
    """End after this many occurrences (including the first). ``None`` = open."""
    until: _Date | None = None
    """End on or before this date (inclusive). ``None`` = open."""

    def __post_init__(self) -> None:
        if self.interval < 1:
            raise ValidationError("a recurrence interval must be at least 1")
        if self.count is not None and self.count < 1:
            raise ValidationError("a recurrence count must be at least 1")
        if self.until is not None and self.until < self.start:
            raise ValidationError(
                f"the recurrence ends ({self.until}) before it starts ({self.start})"
            )
        if self.monthly_by_weekday and self.frequency is not Frequency.MONTHLY:
            raise ValidationError(
                "monthly_by_weekday only applies to a MONTHLY recurrence"
            )

    # -- the series -------------------------------------------------------
    def nth(self, k: int) -> _Date:
        """The date of occurrence ``k`` (0-based), ignoring ``count``/``until``."""
        if k < 0:
            raise ValidationError("occurrence index must not be negative")
        step = k * self.interval
        if self.frequency is Frequency.DAILY:
            return self.start + timedelta(days=step)
        if self.frequency is Frequency.WEEKLY:
            return self.start + timedelta(weeks=step)
        if self.frequency is Frequency.YEARLY:
            return add_months(self.start, step * 12)
        # MONTHLY
        if not self.monthly_by_weekday:
            return add_months(self.start, step)
        target = add_months(self.start.replace(day=1), step)
        nth = (self.start.day - 1) // 7 + 1  # 1..5; the helper clamps to the last
        return _nth_weekday_of_month(target.year, target.month, self.start.weekday(), nth)

    def bounded_count(self) -> int | None:
        """Total occurrences when the rule is count- or until-bounded."""
        if self.count is not None and self.until is None:
            return self.count
        if self.until is None:
            return None
        total = 0
        for k in range(_MAX_SCAN):
            if self.nth(k) > self.until:
                break
            total += 1
            if self.count is not None and total >= self.count:
                break
        return total

    def occurrences(self, *, start_index: int = 0) -> Iterator[_Date]:
        """Occurrence dates from ``start_index`` onward, honouring the end."""
        limit = self.bounded_count()
        k = start_index
        while limit is None or k < limit:
            if k - start_index >= _MAX_SCAN:  # pragma: no cover - runaway guard
                raise ValidationError("recurrence produced too many occurrences")
            moment = self.nth(k)
            if self.until is not None and moment > self.until:
                return
            yield moment
            k += 1

    def occurrence_on_or_after(self, day: _Date, *, start_index: int = 0) -> int | None:
        """Index of the first occurrence not before ``day``; None if the series
        ends first."""
        limit = self.bounded_count()
        for k in range(start_index, start_index + _MAX_SCAN):
            if limit is not None and k >= limit:
                return None
            moment = self.nth(k)
            if self.until is not None and moment > self.until:
                return None
            if moment >= day:
                return k
        return None  # pragma: no cover - runaway guard

    # -- persistence -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency": self.frequency.value,
            "start": self.start.isoformat(),
            "interval": self.interval,
            "monthly_by_weekday": self.monthly_by_weekday,
            "count": self.count,
            "until": None if self.until is None else self.until.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recurrence:
        return cls(
            frequency=Frequency(data["frequency"]),
            start=_Date.fromisoformat(data["start"]),
            interval=int(data.get("interval", 1)),
            monthly_by_weekday=bool(data.get("monthly_by_weekday", False)),
            count=None if data.get("count") is None else int(data["count"]),
            until=(
                None if data.get("until") is None else _Date.fromisoformat(data["until"])
            ),
        )

    def replace(self, **changes: Any) -> Recurrence:
        return replace(self, **changes)

    # -- display --------------------------------------------------------
    def describe(self) -> str:
        unit = {
            Frequency.DAILY: "day",
            Frequency.WEEKLY: "week",
            Frequency.MONTHLY: "month",
            Frequency.YEARLY: "year",
        }[self.frequency]
        every = unit if self.interval == 1 else f"{self.interval} {unit}s"
        text = f"every {every}"
        if self.frequency is Frequency.WEEKLY:
            text += f" on {calendar.day_name[self.start.weekday()]}"
        elif self.frequency is Frequency.MONTHLY and self.monthly_by_weekday:
            ordinal = ["first", "second", "third", "fourth", "fifth"][
                min((self.start.day - 1) // 7, 4)
            ]
            text += f" on the {ordinal} {calendar.day_name[self.start.weekday()]}"
        elif self.frequency is Frequency.MONTHLY:
            text += f" on day {self.start.day}"
        if self.count is not None:
            text += f", {self.count} time{'s' if self.count != 1 else ''}"
        if self.until is not None:
            text += f", until {self.until.isoformat()}"
        return text
