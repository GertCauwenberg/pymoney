"""Budgets: what an account is *allowed* to move, month by month.

A :class:`Budget` is a book-scoped entity -- one per account -- that a user
sets to track spending (or an income target) against a plan.  The plan *is*
its months: ``YYYY-MM`` -> a figure.  A month with no entry is unbudgeted, and
that is the whole story -- there is no default that quietly applies to months
nobody planned.

That absence is deliberate.  A budget answers "what may I spend in 2026?", not
"what may I spend for ever?", and a stored default would keep answering the
second question in 2031, beside per-month figures that were never revisited.
Looking back must show what was planned then.  The screen still offers a
"same every month" input, but it fills the months on screen with explicit
figures and stores exactly those.

Amounts are stored the way a user types them: a **positive allowance** in the
account's natural reporting orientation (expenses positive, income positive).
The budget report lines that up with the period table's already-signed actuals,
so "remaining = budget minus spent" is a straight subtraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID

from .amount import Amount
from .commodity import Commodity
from .entity import Entity
from .errors import ValidationError

__all__ = ["Budget", "is_month"]

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def is_month(value: str) -> bool:
    """True for a ``YYYY-MM`` string with a real month number."""
    return bool(_MONTH.match(value))


@dataclass(frozen=True, slots=True, kw_only=True)
class Budget(Entity):
    """An account's plan: the figure for each month that has one."""

    account_id: UUID
    currency: Commodity
    months: dict[str, int] = field(default_factory=dict)
    """``YYYY-MM`` -> minor units.  A month that is not here is unbudgeted."""

    def __post_init__(self) -> None:
        for key in self.months:
            if not is_month(key):
                raise ValidationError(f"budget month must be YYYY-MM, got {key!r}")
        # Freeze the mapping into a plain, ordered dict so two budgets compare equal
        # regardless of insertion order.
        object.__setattr__(
            self, "months", {k: int(v) for k, v in sorted(self.months.items())}
        )

    def for_month(self, ym: str) -> int | None:
        """Minor units allowed in month ``ym``, or ``None`` if unplanned."""
        return self.months.get(ym)

    def amount_for(self, ym: str) -> Amount | None:
        minor = self.for_month(ym)
        return None if minor is None else Amount(self.currency, minor)

    def __str__(self) -> str:
        count = len(self.months)
        if not count:
            return "nothing planned"
        return f"{count} month{'s' if count != 1 else ''} planned"
