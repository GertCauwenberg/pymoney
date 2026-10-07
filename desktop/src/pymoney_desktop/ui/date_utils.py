"""Some utilities used in other modules"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate

__all__ = [
    "PERIOD_PRESETS",
    "period_for",
]


# The same presets as the web app (packages/core/src/periods.ts), so a period
# means the same thing wherever it is chosen.
PERIOD_PRESETS: tuple[tuple[str, str], ...] = (
    ("this-month", "This month"),
    ("last-month", "Last month"),
    ("this-quarter", "This quarter"),
    ("last-quarter", "Last quarter"),
    ("year-to-date", "Year to date"),
    ("last-year", "Last year"),
    ("last-12-months", "Last 12 months"),
)


def _month_end(year: int, month: int) -> date:
    first_next = date(year + (month // 12), month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def period_for(preset: str, today: date) -> tuple[date, date]:
    y, m = today.year, today.month
    quarter_start = ((m - 1) // 3) * 3 + 1
    if preset == "this-month":
        return date(y, m, 1), today
    if preset == "last-month":
        y2, m2 = (y - 1, 12) if m == 1 else (y, m - 1)
        return date(y2, m2, 1), _month_end(y2, m2)
    if preset == "this-quarter":
        return date(y, quarter_start, 1), today
    if preset == "last-quarter":
        start_month = quarter_start - 3
        y2 = y if start_month >= 1 else y - 1
        start_month = start_month if start_month >= 1 else start_month + 12
        return date(y2, start_month, 1), _month_end(y2, start_month + 2)
    if preset == "year-to-date":
        return date(y, 1, 1), today
    if preset == "last-year":
        return date(y - 1, 1, 1), date(y - 1, 12, 31)
    if preset == "last-12-months":
        try:
            start = date(y - 1, m, today.day)
        except ValueError:  # 29 February
            start = date(y - 1, m, 28)
        return start, today
    raise ValueError(f"unknown period preset {preset!r}")


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _pydate(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())
