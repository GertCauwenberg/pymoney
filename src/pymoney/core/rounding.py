"""Exact rounding and allocation (decisions #3 and #4).

Everything here works on :class:`fractions.Fraction` and returns ``int``.  There
is deliberately no float anywhere: a rate of ``10/3`` stays ``10/3`` until the
moment we are forced to book it into integer minor units.

Two distinct operations live here:

* :func:`round_fraction` -- turn one rational into one integer under a
  documented rounding mode.
* :func:`allocate` -- turn one rational *total* into N integers that sum
  **exactly** to the booked total.  This is what makes "10/3 dollars split
  three ways" add up to 10.00 instead of 9.99.

The allocation uses the largest-remainder (Hamilton) method, which has the
property we care about most: every output is either ``floor`` or ``ceil`` of
its exact share, so no single leg is ever off by more than one minor unit, and
legs whose exact share is already integral are *never* perturbed.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction

from .errors import ValidationError

__all__ = [
    "DEFAULT_ROUNDING",
    "RoundingMode",
    "allocate",
    "allocate_to_zero_sum",
    "distribute",
    "round_fraction",
    "to_fraction",
]


class RoundingMode(StrEnum):
    """Rounding modes, matching the Decimal/ISO vocabulary.

    ``HALF_EVEN`` ("banker's rounding") is the default: it is unbiased over a
    large number of postings, which is what accountants expect.
    """

    UP = "UP"  # away from zero
    DOWN = "DOWN"  # toward zero (truncate)
    CEILING = "CEILING"  # toward +inf
    FLOOR = "FLOOR"  # toward -inf
    HALF_UP = "HALF_UP"  # .5 away from zero
    HALF_DOWN = "HALF_DOWN"  # .5 toward zero
    HALF_EVEN = "HALF_EVEN"  # .5 to the even neighbour


DEFAULT_ROUNDING = RoundingMode.HALF_EVEN

Numeric = Fraction | int | Decimal | str


def to_fraction(value: Numeric) -> Fraction:
    """Coerce a user-supplied number to an exact :class:`Fraction`.

    ``float`` is intentionally rejected: ``0.1`` is not one tenth, and silently
    accepting it would put inexact data into the ledger.
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):  # bool is an int subclass; almost surely a bug
        raise TypeError("refusing to coerce bool to Fraction")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"cannot convert non-finite Decimal: {value}")
        return Fraction(value)
    if isinstance(value, str):
        return Fraction(value)
    if isinstance(value, float):
        raise TypeError(
            "float is not accepted: use str, Decimal or Fraction to keep amounts exact"
        )
    raise TypeError(f"cannot convert {type(value).__name__} to Fraction")


def round_fraction(value: Fraction, mode: RoundingMode = DEFAULT_ROUNDING) -> int:
    """Round an exact rational to an integer under ``mode``."""
    num = value.numerator
    den = value.denominator  # always positive for Fraction
    floor_q, rem = divmod(num, den)  # 0 <= rem < den
    if rem == 0:
        return floor_q
    ceil_q = floor_q + 1

    match mode:
        case RoundingMode.FLOOR:
            return floor_q
        case RoundingMode.CEILING:
            return ceil_q
        case RoundingMode.DOWN:
            return ceil_q if num < 0 else floor_q
        case RoundingMode.UP:
            return floor_q if num < 0 else ceil_q

    twice = 2 * rem
    if twice > den:
        return ceil_q
    if twice < den:
        return floor_q

    # Exactly halfway.
    match mode:
        case RoundingMode.HALF_UP:
            return floor_q if num < 0 else ceil_q
        case RoundingMode.HALF_DOWN:
            return ceil_q if num < 0 else floor_q
        case RoundingMode.HALF_EVEN:
            return floor_q if floor_q % 2 == 0 else ceil_q

    raise ValidationError(f"unknown rounding mode: {mode!r}")  # pragma: no cover


def allocate(
    total: Fraction,
    weights: Sequence[Fraction],
    mode: RoundingMode = DEFAULT_ROUNDING,
) -> list[int]:
    """Split ``total`` into ``len(weights)`` integers summing to the booked total.

    The exact share of leg *i* is ``total * weights[i] / sum(weights)``.  The
    result satisfies::

        sum(allocate(total, weights, mode)) == round_fraction(total, mode)

    and every element is ``floor`` or ``ceil`` of its exact share.

    :raises ValidationError: if ``weights`` is empty or sums to zero.
    """
    if not weights:
        raise ValidationError("cannot allocate across zero legs")
    total_weight = sum(weights, Fraction(0))
    if total_weight == 0:
        raise ValidationError("cannot allocate across weights summing to zero")
    shares = [total * w / total_weight for w in weights]
    return distribute(shares, round_fraction(total, mode))


def allocate_to_zero_sum(values: Sequence[Fraction]) -> list[int]:
    """Book rational values that sum to zero into integers that sum to zero.

    This is the multi-currency transaction case: the *values* of all splits are
    expressed in the transaction currency and must cancel out exactly, both
    before and after rounding.

    :raises ValidationError: if ``values`` does not sum to exactly zero.
    """
    if not values:
        raise ValidationError("cannot book an empty set of values")
    residual = sum(values, Fraction(0))
    if residual != 0:
        raise ValidationError(f"values must sum to zero before booking, got {residual}")
    return distribute(list(values), 0)


def distribute(shares: Sequence[Fraction], booked_total: int) -> list[int]:
    """Round exact ``shares`` to integers summing exactly to ``booked_total``.

    The general largest-remainder primitive the other helpers build on.  Units
    that cannot be derived from ``floor`` alone are handed to the legs with the
    largest fractional part (or taken back from the smallest, if
    ``booked_total`` is below the sum of the floors).

    :raises ValidationError: if ``shares`` is empty, or if ``booked_total`` is
        further than one minor unit per leg away from the exact sum -- that
        would mean distorting some leg by more than a minor unit, which is
        never what the caller wants.
    """
    if not shares:
        raise ValidationError("cannot distribute across zero legs")
    floors = [s.numerator // s.denominator for s in shares]
    remainder = booked_total - sum(floors)

    if remainder == 0:
        return floors
    if not -len(shares) <= remainder <= len(shares):
        raise ValidationError(
            f"booked total {booked_total} is too far from the exact sum "
            f"{sum(shares, Fraction(0))} to distribute over {len(shares)} legs"
        )

    # Fractional parts are in [0, 1).  Hand out the remaining units to the
    # largest ones; ties break on position so the result is deterministic.
    fracs = [s - f for s, f in zip(shares, floors, strict=True)]
    order = sorted(range(len(shares)), key=lambda i: (-fracs[i], i))

    result = list(floors)
    if remainder > 0:
        for i in order[:remainder]:
            result[i] += 1
    else:
        # Negative remainder: take units back from the *smallest* fractional
        # parts, i.e. walk the order backwards.
        for i in order[len(order) + remainder :]:
            result[i] -= 1
    return result
