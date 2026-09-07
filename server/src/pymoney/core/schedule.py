"""Scheduled / recurring transactions (decision: new).

Rent, a salary, a subscription: a transaction that should be posted again and
again on a rule. A :class:`Schedule` is

* a **template** -- the legs, payee, memo and number of the transaction to
  post, with the *date left out* (:class:`ScheduleTemplate`);
* a **recurrence** -- the rule (:class:`~pymoney.core.recurrence.Recurrence`);
* a **cursor** -- ``next_due``, the date of the next occurrence that has not
  yet been posted or skipped, and ``occurrences_done``, how many have been.

Nothing posts on its own. A schedule becomes *due* when ``next_due`` is on or
before a date the caller supplies; a client that sees due schedules offers to
post them, one idempotent command per occurrence (see
:class:`~pymoney.core.commands.PostScheduledOccurrence`). ``auto_post`` only
says the client may do that without a per-occurrence confirmation -- it never
means the server does it behind anyone's back, which would need concurrency
this design deliberately avoids.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date as _Date
from fractions import Fraction
from typing import Any
from uuid import UUID

from .entity import Entity
from .errors import ValidationError
from .ids import new_id
from .posting import LegSpec
from .recurrence import Recurrence
from .rounding import DEFAULT_ROUNDING, Numeric, RoundingMode, to_fraction

__all__ = ["Schedule", "ScheduleTemplate", "TemplateLeg"]


def _maybe_fraction(value: Numeric | None) -> Fraction | None:
    return None if value is None else to_fraction(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class TemplateLeg:
    """One leg of a schedule's template: an account and, optionally, the
    amount to post. Exactly one leg may leave every amount unset (the residual
    that balances the entry), as in an ordinary posting."""

    account_id: UUID
    value: Fraction | None = None
    quantity: Fraction | None = None
    price: Fraction | None = None
    memo: str = ""
    payee_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _maybe_fraction(self.value))
        object.__setattr__(self, "quantity", _maybe_fraction(self.quantity))
        object.__setattr__(self, "price", _maybe_fraction(self.price))
        if self.price is not None and self.price <= 0:
            raise ValidationError(
                f"a template leg's price must be positive, got {self.price}"
            )

    @property
    def is_residual(self) -> bool:
        return self.value is None and self.quantity is None

    def to_legspec(self) -> LegSpec:
        """A fresh :class:`LegSpec` -- new leg id every time, so each posted
        occurrence is its own transaction with its own splits."""
        return LegSpec(
            account_id=self.account_id,
            value=self.value,
            quantity=self.quantity,
            price=self.price,
            memo=self.memo,
            payee_id=self.payee_id,
            id=new_id(),
        )

    def to_dict(self) -> dict[str, Any]:
        def r(x: Fraction | None) -> str | None:
            return None if x is None else f"{x.numerator}/{x.denominator}"

        return {
            "account_id": str(self.account_id),
            "value": r(self.value),
            "quantity": r(self.quantity),
            "price": r(self.price),
            "memo": self.memo,
            "payee_id": None if self.payee_id is None else str(self.payee_id),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemplateLeg:
        return cls(
            account_id=UUID(data["account_id"]),
            value=data.get("value"),
            quantity=data.get("quantity"),
            price=data.get("price"),
            memo=data.get("memo", ""),
            payee_id=None if data.get("payee_id") is None else UUID(data["payee_id"]),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduleTemplate:
    """The transaction a schedule posts, minus its date."""

    legs: tuple[TemplateLeg, ...]
    currency_id: UUID | None = None
    memo: str = ""
    payee_id: UUID | None = None
    number: str = ""
    rounding: RoundingMode = DEFAULT_ROUNDING
    imbalance_account_id: UUID | None = None
    estimate: bool = False
    """The amounts are a guess (a variable bill, a salary before tax changes).
    Stored for display -- ``~€900`` -- and as a nudge to review on confirm;
    posting still uses them verbatim unless the occurrence overrides them."""

    def __post_init__(self) -> None:
        if len(self.legs) < 1:
            raise ValidationError("a schedule template needs at least one leg")
        if sum(1 for leg in self.legs if leg.is_residual) > 1:
            raise ValidationError(
                "at most one template leg may be left to balance the entry"
            )

    def legspecs(self) -> tuple[LegSpec, ...]:
        return tuple(leg.to_legspec() for leg in self.legs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "legs": [leg.to_dict() for leg in self.legs],
            "currency_id": None if self.currency_id is None else str(self.currency_id),
            "memo": self.memo,
            "payee_id": None if self.payee_id is None else str(self.payee_id),
            "number": self.number,
            "rounding": self.rounding.value,
            "imbalance_account_id": (
                None
                if self.imbalance_account_id is None
                else str(self.imbalance_account_id)
            ),
            "estimate": self.estimate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleTemplate:
        return cls(
            legs=tuple(TemplateLeg.from_dict(leg) for leg in data["legs"]),
            currency_id=(
                None if data.get("currency_id") is None else UUID(data["currency_id"])
            ),
            memo=data.get("memo", ""),
            payee_id=None if data.get("payee_id") is None else UUID(data["payee_id"]),
            number=data.get("number", ""),
            rounding=RoundingMode(data.get("rounding", DEFAULT_ROUNDING.value)),
            imbalance_account_id=(
                None
                if data.get("imbalance_account_id") is None
                else UUID(data["imbalance_account_id"])
            ),
            estimate=bool(data.get("estimate", False)),
        )

    def replace(self, **changes: Any) -> ScheduleTemplate:
        return replace(self, **changes)


@dataclass(frozen=True, slots=True, kw_only=True)
class Schedule(Entity):
    """A recurring transaction: a template, a rule, and a cursor over the rule."""

    name: str = ""
    template: ScheduleTemplate | None = None
    recurrence: Recurrence | None = None
    occurrences_done: int = 0
    """How many occurrences have been posted *or* skipped. The one cursor into
    the recurrence; everything else about "where we are" is derived from it."""
    next_due: _Date | None = None
    """Derived: the date of the next occurrence (None once finished). Stored as
    a column only so "what is due?" is an indexed range scan; recomputed in
    :meth:`__post_init__`, so a stale value can never be believed."""
    last_occurrence: _Date | None = None
    """The date of the most recent occurrence that was posted or skipped."""
    auto_post: bool = False
    """A client may post due occurrences of this schedule without asking; it
    still does so under the user's session, idempotently, on sight."""
    paused: bool = False
    """Stop offering this schedule as due, without losing it."""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("a schedule needs a name")
        if self.template is None or self.recurrence is None:
            raise ValidationError("a schedule needs a template and a recurrence")
        if self.occurrences_done < 0:
            raise ValidationError("occurrences_done cannot be negative")
        object.__setattr__(self, "next_due", self.occurrence_due())

    # -- the series ------------------------------------------------------
    @property
    def rule(self) -> Recurrence:
        assert self.recurrence is not None  # guaranteed by __post_init__
        return self.recurrence

    @property
    def tmpl(self) -> ScheduleTemplate:
        assert self.template is not None
        return self.template

    @property
    def total_occurrences(self) -> int | None:
        return self.rule.bounded_count()

    @property
    def is_finished(self) -> bool:
        """No more occurrences will ever come due."""
        total = self.total_occurrences
        if total is not None and self.occurrences_done >= total:
            return True
        moment = self.rule.nth(self.occurrences_done)
        return self.rule.until is not None and moment > self.rule.until

    def is_due(self, on: _Date) -> bool:
        due = None if self.paused else self.occurrence_due()
        return due is not None and due <= on

    def pending(self, on: _Date) -> tuple[_Date, ...]:
        """Every occurrence from the cursor up to and including ``on``."""
        if self.paused or self.is_finished:
            return ()
        out: list[_Date] = []
        total = self.total_occurrences
        for k in range(self.occurrences_done, (total if total is not None else 1 << 30)):
            moment = self.rule.nth(k)
            if (self.rule.until is not None and moment > self.rule.until) or moment > on:
                break
            out.append(moment)
        return tuple(out)

    def upcoming(self, count: int, *, after: _Date | None = None) -> tuple[_Date, ...]:
        """The next ``count`` occurrence dates (for a preview)."""
        out: list[_Date] = []
        k = self.occurrences_done
        total = self.total_occurrences
        while len(out) < count:
            if total is not None and k >= total:
                break
            moment = self.rule.nth(k)
            if self.rule.until is not None and moment > self.rule.until:
                break
            if after is None or moment > after:
                out.append(moment)
            k += 1
        return tuple(out)

    # -- advancing ------------------------------------------------------
    def occurrence_due(self) -> _Date | None:
        """The date this schedule would post next, or None if finished."""
        if self.is_finished:
            return None
        return self.rule.nth(self.occurrences_done)

    def consumed(self, *, on: _Date) -> Schedule:
        """A copy advanced past the occurrence dated ``on`` (which must be the
        one currently due)."""
        due = self.occurrence_due()
        if due is None:
            raise ValidationError(f"schedule {self.name!r} has no occurrence left")
        if on != due:
            raise ValidationError(
                f"the next occurrence of {self.name!r} is {due}, not {on}"
            )
        return replace(
            self, occurrences_done=self.occurrences_done + 1, last_occurrence=on
        )

    def replace(self, **changes: Any) -> Schedule:
        return replace(self, **changes)

    def __repr__(self) -> str:
        rule = "?" if self.rule is None else self.rule.describe()
        return f"Schedule({self.name!r}, {rule}, next {self.next_due})"
