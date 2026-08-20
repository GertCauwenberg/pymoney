"""Amounts (decision #3): "booked" and "rational" are two different things.

:class:`Amount`
    A **booked** amount: an exact integer number of minor units of one
    commodity.  This is the only thing that ever lands in a split, a balance or
    a bank statement.  Closed under ``+``, ``-`` and integer scaling, so
    balances can never drift.

:class:`RationalAmount`
    An **exact rational** quantity of one commodity, held in major units as a
    :class:`~fractions.Fraction`.  This is what intermediate computations
    produce -- apply a 10/3 exchange rate, value a holding, pro-rate a fee --
    and it must be explicitly *booked* (rounded) before it can be stored.

Keeping them as separate types means the type checker catches the place where
you forgot to round, instead of a float creeping into the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING

from .commodity import Commodity
from .errors import CommodityMismatchError, ValidationError
from .rounding import (
    DEFAULT_ROUNDING,
    Numeric,
    RoundingMode,
    round_fraction,
    to_fraction,
)

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

__all__ = ["Amount", "Balances", "RationalAmount"]


def _check_same(a: Commodity, b: Commodity, op: str) -> None:
    if a.id != b.id:
        raise CommodityMismatchError(
            f"cannot {op} {a.code} and {b.code}: different commodities"
        )


def _format_fraction(value: Fraction, precision: int, *, group: str = "") -> str:
    """Render an exact rational with ``precision`` decimals, half-even."""
    scaled = round_fraction(value * 10**precision, RoundingMode.HALF_EVEN)
    sign = "-" if scaled < 0 else ""
    digits = str(abs(scaled)).rjust(precision + 1, "0")
    whole, frac = (
        (digits[:-precision], digits[-precision:]) if precision else (digits, "")
    )
    if group:
        blocks = []
        while len(whole) > 3:
            blocks.append(whole[-3:])
            whole = whole[:-3]
        blocks.append(whole)
        whole = group.join(reversed(blocks))
    return f"{sign}{whole}.{frac}" if precision else f"{sign}{whole}"


@dataclass(frozen=True, slots=True)
class Amount:
    """A booked amount: ``minor`` indivisible units of ``commodity``."""

    commodity: Commodity
    minor: int

    def __post_init__(self) -> None:
        if not isinstance(self.minor, int) or isinstance(self.minor, bool):
            raise ValidationError(
                f"booked amounts are integer minor units, got {self.minor!r}"
            )

    # -- construction ------------------------------------------------------
    @classmethod
    def zero(cls, commodity: Commodity) -> Amount:
        return cls(commodity, 0)

    @classmethod
    def from_major(
        cls,
        commodity: Commodity,
        value: Numeric,
        mode: RoundingMode = DEFAULT_ROUNDING,
    ) -> Amount:
        """Book a major-unit number (``"12.34"``, ``Fraction(10, 3)``, ...)."""
        exact = to_fraction(value) * commodity.minor_unit_factor
        return cls(commodity, round_fraction(exact, mode))

    @classmethod
    def exact_from_major(cls, commodity: Commodity, value: Numeric) -> Amount:
        """Book a major-unit number, refusing to round.

        Use this on input paths where silently losing a fraction of a cent
        would be a bug (importers, API payloads claiming to be exact).
        """
        exact = to_fraction(value) * commodity.minor_unit_factor
        if exact.denominator != 1:
            raise ValidationError(
                f"{value} is not representable in {commodity.code} "
                f"(1/{commodity.minor_unit_factor} units)"
            )
        return cls(commodity, exact.numerator)

    # -- conversions -------------------------------------------------------
    @property
    def as_fraction(self) -> Fraction:
        """Value in major units, exactly."""
        return Fraction(self.minor, self.commodity.minor_unit_factor)

    @property
    def rational(self) -> RationalAmount:
        return RationalAmount(self.commodity, self.as_fraction)

    # -- arithmetic --------------------------------------------------------
    def __add__(self, other: Amount) -> Amount:
        _check_same(self.commodity, other.commodity, "add")
        return Amount(self.commodity, self.minor + other.minor)

    def __sub__(self, other: Amount) -> Amount:
        _check_same(self.commodity, other.commodity, "subtract")
        return Amount(self.commodity, self.minor - other.minor)

    def __neg__(self) -> Amount:
        return Amount(self.commodity, -self.minor)

    def __abs__(self) -> Amount:
        return Amount(self.commodity, abs(self.minor))

    def __mul__(self, factor: int) -> Amount:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError(
                "a booked amount may only be scaled by an int; use .rational "
                "for exact rational arithmetic"
            )
        return Amount(self.commodity, self.minor * factor)

    __rmul__ = __mul__

    def scaled(self, factor: Numeric, mode: RoundingMode = DEFAULT_ROUNDING) -> Amount:
        """Multiply by an exact rational and re-book the result."""
        return Amount(
            self.commodity, round_fraction(self.minor * to_fraction(factor), mode)
        )

    # -- predicates --------------------------------------------------------
    @property
    def is_zero(self) -> bool:
        return self.minor == 0

    @property
    def sign(self) -> int:
        return (self.minor > 0) - (self.minor < 0)

    def __bool__(self) -> bool:
        return self.minor != 0

    def _cmp_key(self, other: Amount, op: str) -> tuple[int, int]:
        _check_same(self.commodity, other.commodity, op)
        return self.minor, other.minor

    def __lt__(self, other: Amount) -> bool:
        a, b = self._cmp_key(other, "compare")
        return a < b

    def __le__(self, other: Amount) -> bool:
        a, b = self._cmp_key(other, "compare")
        return a <= b

    def __gt__(self, other: Amount) -> bool:
        a, b = self._cmp_key(other, "compare")
        return a > b

    def __ge__(self, other: Amount) -> bool:
        a, b = self._cmp_key(other, "compare")
        return a >= b

    def __eq__(self, other: object) -> bool:
        """Equality is by commodity *id* and minor units.

        Deliberately not the dataclass default: an ``Amount`` may be holding a
        slightly stale copy of the commodity (a renamed symbol, a changed
        display precision) and should still compare equal.
        """
        if not isinstance(other, Amount):
            return NotImplemented
        return self.minor == other.minor and self.commodity.id == other.commodity.id

    def __hash__(self) -> int:
        return hash((self.commodity.id, self.minor))

    # -- display -----------------------------------------------------------
    def format(
        self,
        *,
        symbol: bool = True,
        precision: int | None = None,
        group: str = "",
    ) -> str:
        precision = self.commodity.precision if precision is None else precision
        text = _format_fraction(self.as_fraction, precision, group=group)
        return f"{text} {self.commodity.display_symbol}" if symbol else text

    def __str__(self) -> str:
        return self.format()

    def __repr__(self) -> str:
        return f"Amount({self.commodity.code} {self.format(symbol=False)})"


@dataclass(frozen=True, slots=True)
class RationalAmount:
    """An exact, not-yet-booked quantity of a commodity (major units)."""

    commodity: Commodity
    value: Fraction

    def __post_init__(self) -> None:
        if not isinstance(self.value, Fraction):
            object.__setattr__(self, "value", to_fraction(self.value))

    @classmethod
    def zero(cls, commodity: Commodity) -> RationalAmount:
        return cls(commodity, Fraction(0))

    def book(self, mode: RoundingMode = DEFAULT_ROUNDING) -> Amount:
        """Round to the commodity's minor unit."""
        return Amount.from_major(self.commodity, self.value, mode)

    @property
    def is_bookable(self) -> bool:
        """True if booking loses nothing."""
        return (self.value * self.commodity.minor_unit_factor).denominator == 1

    def __add__(self, other: RationalAmount) -> RationalAmount:
        _check_same(self.commodity, other.commodity, "add")
        return RationalAmount(self.commodity, self.value + other.value)

    def __sub__(self, other: RationalAmount) -> RationalAmount:
        _check_same(self.commodity, other.commodity, "subtract")
        return RationalAmount(self.commodity, self.value - other.value)

    def __neg__(self) -> RationalAmount:
        return RationalAmount(self.commodity, -self.value)

    def __mul__(self, factor: Numeric) -> RationalAmount:
        return RationalAmount(self.commodity, self.value * to_fraction(factor))

    __rmul__ = __mul__

    def __truediv__(self, divisor: Numeric) -> RationalAmount:
        d = to_fraction(divisor)
        if d == 0:
            raise ZeroDivisionError("division by zero")
        return RationalAmount(self.commodity, self.value / d)

    @property
    def is_zero(self) -> bool:
        return self.value == 0

    @property
    def sign(self) -> int:
        return (self.value > 0) - (self.value < 0)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RationalAmount):
            return NotImplemented
        return self.value == other.value and self.commodity.id == other.commodity.id

    def __hash__(self) -> int:
        return hash((self.commodity.id, self.value))

    def format(self, *, symbol: bool = True, precision: int | None = None) -> str:
        precision = self.commodity.precision if precision is None else precision
        text = _format_fraction(self.value, precision)
        exact = "" if self.is_bookable else "~"
        out = f"{exact}{text}"
        return f"{out} {self.commodity.display_symbol}" if symbol else out

    def __str__(self) -> str:
        return self.format()

    def __repr__(self) -> str:
        return f"RationalAmount({self.commodity.code} {self.value})"


class Balances:
    """A multi-commodity balance: commodity id -> :class:`Amount`.

    An account subtree can hold several commodities at once (a brokerage parent
    over USD cash plus three securities).  Summing those into one number needs
    a valuation policy, so until that policy is supplied we carry the vector.
    """

    __slots__ = ("_by_commodity",)

    def __init__(self, amounts: Iterable[Amount] = ()) -> None:
        self._by_commodity: dict[object, Amount] = {}
        for amount in amounts:
            self.add(amount)

    def add(self, amount: Amount) -> Balances:
        """Accumulate ``amount``; zero positions are not retained.

        A commodity that nets to zero carries no information for a balance
        report, so it is dropped.  Use :meth:`of` to ask about a commodity that
        may or may not be present.
        """
        key = amount.commodity.id
        current = self._by_commodity.get(key)
        total = amount if current is None else current + amount
        if total.is_zero:
            self._by_commodity.pop(key, None)
        else:
            self._by_commodity[key] = total
        return self

    def amounts(self) -> tuple[Amount, ...]:
        return tuple(sorted(self._by_commodity.values(), key=lambda a: a.commodity.code))

    def of(self, commodity: Commodity) -> Amount:
        return self._by_commodity.get(commodity.id, Amount.zero(commodity))

    def __add__(self, other: Balances) -> Balances:
        out = Balances(self.amounts())
        for amount in other.amounts():
            out.add(amount)
        return out

    def __neg__(self) -> Balances:
        return Balances(-a for a in self.amounts())

    def __sub__(self, other: Balances) -> Balances:
        return self + (-other)

    @property
    def is_zero(self) -> bool:
        return all(a.is_zero for a in self._by_commodity.values())

    @property
    def is_single(self) -> bool:
        return len(self._by_commodity) == 1

    def sole(self) -> Amount:
        """The only amount, for the common single-commodity case."""
        if len(self._by_commodity) != 1:
            raise CommodityMismatchError(
                f"expected exactly one commodity, got {len(self._by_commodity)}"
            )
        return next(iter(self._by_commodity.values()))

    def __len__(self) -> int:
        return len(self._by_commodity)

    def __iter__(self):
        return iter(self.amounts())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Balances):
            return NotImplemented
        return self.amounts() == other.amounts()

    def __repr__(self) -> str:
        inner = ", ".join(a.format() for a in self.amounts())
        return f"Balances({inner})"
