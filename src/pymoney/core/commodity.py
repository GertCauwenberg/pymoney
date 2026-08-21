"""Commodities (decision #2).

Currencies and securities are the same kind of thing: something you can hold a
quantity of, and whose value relative to another commodity is a
:class:`~pymoney.core.price.Price`.  Treating them uniformly means FX
conversion, share valuation and stock splits all run through one mechanism.

Two numbers describe how a commodity is booked:

``minor_unit_factor``
    How many indivisible minor units make up one major unit.  100 for USD and
    EUR, 1 for JPY, 1000 for KWD, 5 for the Mauritanian ouguiya, and typically
    10**4 or more for securities that trade in fractional shares.  *All booked
    amounts are integer multiples of one minor unit* (decision #3).

``display_precision``
    How many decimal digits to show.  Usually ``log10(minor_unit_factor)``, but
    decoupled because a commodity with a non-decimal factor still has to be
    printed somehow, and because some users want to see 4 decimals on a
    2-decimal currency in price tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import Any

from .entity import Entity
from .errors import ValidationError

__all__ = ["Commodity", "CommodityKind"]


class CommodityKind(StrEnum):
    """What sort of thing a commodity is.

    Affects presentation and which quote provider is appropriate, not the
    arithmetic: all kinds are booked and priced identically.
    """

    CURRENCY = "CURRENCY"
    STOCK = "STOCK"
    FUND = "FUND"
    BOND = "BOND"
    CRYPTO = "CRYPTO"
    OTHER = "OTHER"


def _decimal_places(factor: int) -> int:
    """Digits needed to show one minor unit, for a power-of-ten factor."""
    places = 0
    value = factor
    while value % 10 == 0 and value > 1:
        value //= 10
        places += 1
    return places if value == 1 else places + 1


@dataclass(frozen=True, slots=True, kw_only=True)
class Commodity(Entity):
    """A currency, security or any other unit of holding.

    Immutable; replaced wholesale on update by the owning
    :class:`~pymoney.core.book.Book`.
    """

    code: str
    """Ticker or ISO 4217 code; unique per book (case-insensitively)."""
    kind: CommodityKind = CommodityKind.CURRENCY
    name: str = ""
    symbol: str | None = None
    """Display symbol such as ``$`` or ``€``; falls back to ``code``."""
    minor_unit_factor: int = 100
    display_precision: int | None = None
    isin: str | None = None
    quote_source: str | None = None
    """Name of the price provider to poll for this commodity, if any."""

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ValidationError("commodity code must not be empty")
        if self.minor_unit_factor < 1:
            raise ValidationError(
                f"minor_unit_factor must be >= 1, got {self.minor_unit_factor}"
            )
        if self.display_precision is not None and self.display_precision < 0:
            raise ValidationError("display_precision must be >= 0")

    # -- derived -----------------------------------------------------------
    @property
    def precision(self) -> int:
        """Decimal digits to display."""
        if self.display_precision is not None:
            return self.display_precision
        return _decimal_places(self.minor_unit_factor)

    @property
    def is_currency(self) -> bool:
        return self.kind is CommodityKind.CURRENCY

    @property
    def minor_unit(self) -> Fraction:
        """The size of one minor unit, in major units."""
        return Fraction(1, self.minor_unit_factor)

    @property
    def display_symbol(self) -> str:
        return self.symbol or self.code

    @property
    def key(self) -> str:
        """Normalised lookup key for uniqueness checks."""
        return self.code.strip().upper()

    # -- factories ---------------------------------------------------------
    @classmethod
    def currency(
        cls,
        code: str,
        *,
        name: str = "",
        scale: int = 2,
        symbol: str | None = None,
        minor_unit_factor: int | None = None,
        **kwargs: Any,
    ) -> Commodity:
        """Build a currency from a decimal ``scale`` (digits after the point)."""
        if scale < 0:
            raise ValidationError("scale must be >= 0")
        factor = 10**scale if minor_unit_factor is None else minor_unit_factor
        return cls(
            kind=CommodityKind.CURRENCY,
            code=code,
            name=name or code,
            symbol=symbol,
            minor_unit_factor=factor,
            **kwargs,
        )

    @classmethod
    def security(
        cls,
        code: str,
        *,
        name: str = "",
        kind: CommodityKind = CommodityKind.STOCK,
        scale: int = 4,
        **kwargs: Any,
    ) -> Commodity:
        """Build a security; defaults to 4 fractional-share decimals."""
        if scale < 0:
            raise ValidationError("scale must be >= 0")
        return cls(
            kind=kind,
            code=code,
            name=name or code,
            minor_unit_factor=10**scale,
            **kwargs,
        )

    def __str__(self) -> str:
        return self.code
