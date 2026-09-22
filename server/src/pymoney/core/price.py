"""Prices and valuation (decisions #2, #3, #9, #12).

A :class:`Price` is an exact rational rate between two commodities, stored as
``num``/``den`` integers.  ``1 EUR = 10/9 USD`` keeps all of its digits forever;
nothing is rounded until an amount is booked.

Prices are **bitemporal**:

``effective_date``
    The plain date the rate applies to -- the market close being quoted.

``as_of``
    The UTC instant at which we *learned* the rate.  Corrections get a later
    ``as_of`` with the same ``effective_date``, so the old number is never
    destroyed and "what did this report say last Tuesday?" stays answerable.

A :class:`ValuationPolicy` pins down everything a multi-currency report needs in
order to be reproducible: the valuation date, the knowledge cutoff, which price
sources are admissible and in what order, how stale a quote may be, and whether
inversion and triangulation are allowed.  Two reports run with the same policy
against the same book always produce the same numbers, even if quotes arrived in
between.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Any
from uuid import UUID

from .amount import Amount, RationalAmount
from .commodity import Commodity
from .entity import Entity
from .errors import NoPriceError, ValidationError
from .rounding import Numeric, to_fraction

__all__ = [
    "Conversion",
    "Price",
    "PriceDB",
    "PriceSource",
    "ValuationPolicy",
]


class PriceSource:
    """Well-known price source names.

    Not an enum: users may add providers, and an importer must be able to round
    trip whatever string the source file contained.
    """

    USER = "user"
    """Manually entered; wins over fetched quotes for the same date."""
    TRANSACTION = "transaction"
    """Implied by an actual trade that was posted."""
    ECB = "ecb"
    YAHOO = "yahoo"
    STOOQ = "stooq"
    IMPORT = "import"
    SPLIT = "split"
    """Synthetic rate recording a stock split ratio."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Price(Entity):
    """``1`` unit of ``base`` is worth ``num/den`` units of ``quote``."""

    base: Commodity
    quote: Commodity
    num: int
    den: int
    effective_date: date
    as_of: datetime
    source: str = PriceSource.USER

    def __post_init__(self) -> None:
        if self.base.id == self.quote.id:
            raise ValidationError(
                f"a price must relate two different commodities ({self.base.code})"
            )
        if self.den == 0:
            raise ValidationError("price denominator must not be zero")
        if self.num <= 0 or self.den < 0:
            raise ValidationError(f"prices must be positive, got {self.num}/{self.den}")
        if self.as_of.tzinfo is None:
            raise ValidationError("as_of must be timezone-aware (UTC)")
        if not self.source:
            raise ValidationError("a price needs a source")

    @classmethod
    def of(
        cls,
        base: Commodity,
        quote: Commodity,
        rate: Numeric,
        *,
        effective_date: date,
        as_of: datetime,
        source: str = PriceSource.USER,
        **kwargs: Any,
    ) -> Price:
        """Build a price from any exact numeric rate."""
        value = to_fraction(rate)
        if value <= 0:
            raise ValidationError(f"prices must be positive, got {value}")
        return cls(
            base=base,
            quote=quote,
            num=value.numerator,
            den=value.denominator,
            effective_date=effective_date,
            as_of=as_of,
            source=source,
            **kwargs,
        )

    @property
    def rate(self) -> Fraction:
        """The rate as an exact :class:`~fractions.Fraction`."""
        return Fraction(self.num, self.den)

    @property
    def pair(self) -> tuple[UUID, UUID]:
        return (self.base.id, self.quote.id)

    def inverted(self) -> Price:
        """The same information expressed the other way round."""
        return Price(
            id=self.id,
            book_id=self.book_id,
            base=self.quote,
            quote=self.base,
            num=self.den,
            den=self.num,
            effective_date=self.effective_date,
            as_of=self.as_of,
            source=self.source,
            created_at=self.created_at,
            updated_at=self.updated_at,
            revision=self.revision,
        )

    def __str__(self) -> str:
        return (
            f"1 {self.base} = {self.num}/{self.den} {self.quote} "
            f"@{self.effective_date} ({self.source})"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ValuationPolicy:
    """Everything needed to make a multi-currency report reproducible."""

    target: Commodity | None = None
    """Commodity to express the report in.  Required by report helpers."""
    as_of: date | None = None
    """Valuation date: ignore prices with a later ``effective_date``."""
    knowledge_time: datetime | None = None
    """Ignore prices we learned after this instant.  ``None`` means "latest"."""
    sources: tuple[str, ...] = ()
    """Admissible sources, most preferred first.

    Empty means "any", with one built-in preference: a price the user entered
    by hand (:attr:`PriceSource.USER`) outranks a fetched quote for the same
    date.  Someone who typed a rate in meant it to be used; a quote fetched
    later that day must not silently displace it.  Name the sources explicitly
    to rank them some other way.
    """
    restrict_to_sources: bool = False
    """If true, prices from sources outside :attr:`sources` are unusable."""
    prefer_source_over_recency: bool = False
    """Rank by source preference before effective date, rather than after."""
    max_staleness_days: int | None = None
    """Reject prices older than this many days before :attr:`as_of`."""
    allow_inverse: bool = True
    """Use a stored ``quote->base`` price as ``base->quote``."""
    allow_triangulation: bool = True
    """Chain prices through intermediate commodities (EUR->USD->JPY)."""
    max_hops: int = 4
    """Maximum number of prices to chain when triangulating."""

    def __post_init__(self) -> None:
        if self.max_hops < 1:
            raise ValidationError("max_hops must be >= 1")
        if self.max_staleness_days is not None and self.max_staleness_days < 0:
            raise ValidationError("max_staleness_days must be >= 0")
        if self.restrict_to_sources and not self.sources:
            raise ValidationError("restrict_to_sources needs a non-empty sources tuple")

    def for_date(self, value: date) -> ValuationPolicy:
        from dataclasses import replace

        return replace(self, as_of=value)

    def for_target(self, value: Commodity) -> ValuationPolicy:
        from dataclasses import replace

        return replace(self, target=value)

    def admits(self, price: Price) -> bool:
        """True if ``price`` may be used under this policy."""
        if self.as_of is not None and price.effective_date > self.as_of:
            return False
        if self.knowledge_time is not None and price.as_of > self.knowledge_time:
            return False
        if self.restrict_to_sources and price.source not in self.sources:
            return False
        return not (
            self.max_staleness_days is not None
            and self.as_of is not None
            and price.effective_date
            < self.as_of - timedelta(days=self.max_staleness_days)
        )

    def rank(self, price: Price) -> tuple:
        """Sort key; larger is better."""
        if not self.sources:
            # The default: a hand-entered price wins the day over a fetched one.
            source_rank = 1 if price.source == PriceSource.USER else 0
        else:
            try:
                source_rank = len(self.sources) - self.sources.index(price.source)
            except ValueError:
                source_rank = 0
        if self.prefer_source_over_recency:
            return (source_rank, price.effective_date, price.as_of, price.id.int)
        return (price.effective_date, source_rank, price.as_of, price.id.int)


@dataclass(frozen=True, slots=True, kw_only=True)
class Conversion:
    """The audit trail of one commodity conversion.

    Reports hand this back so a user can ask "where did that rate come from?"
    and get a real answer rather than a number.
    """

    base: Commodity
    quote: Commodity
    rate: Fraction
    prices: tuple[Price, ...] = ()
    """The chain actually used, already oriented base -> quote."""

    @property
    def hops(self) -> int:
        return len(self.prices)

    @property
    def is_identity(self) -> bool:
        return self.base.id == self.quote.id

    def apply(self, amount: Amount | RationalAmount) -> RationalAmount:
        """Convert an amount, keeping the result exact."""
        value = amount.as_fraction if isinstance(amount, Amount) else amount.value
        if amount.commodity.id != self.base.id:
            raise ValidationError(
                f"conversion expects {self.base.code}, got {amount.commodity.code}"
            )
        return RationalAmount(self.quote, value * self.rate)

    def describe(self) -> str:
        if self.is_identity:
            return f"{self.base.code} (identity)"
        chain = " -> ".join([self.base.code, *(p.quote.code for p in self.prices)])
        sources = ",".join(sorted({p.source for p in self.prices}))
        return f"{chain} @ {self.rate} [{sources}]"

    def __str__(self) -> str:
        return self.describe()


class PriceDB:
    """In-memory bitemporal price store with policy-driven lookup.

    Indexed by unordered commodity pair, because a EUR/USD quote answers
    USD/EUR questions too (subject to ``allow_inverse``).
    """

    __slots__ = ("_by_id", "_by_pair")

    def __init__(self, prices: Sequence[Price] = ()) -> None:
        self._by_id: dict[UUID, Price] = {}
        self._by_pair: dict[frozenset[UUID], list[Price]] = {}
        for price in prices:
            self.add(price)

    # -- mutation ----------------------------------------------------------
    @staticmethod
    def _pair_key(a: Commodity, b: Commodity) -> frozenset[UUID]:
        return frozenset((a.id, b.id))

    def add(self, price: Price) -> Price:
        """Insert or replace a price by id."""
        if price.id in self._by_id:
            self.remove(price.id)
        self._by_id[price.id] = price
        key = self._pair_key(price.base, price.quote)
        self._by_pair.setdefault(key, []).append(price)
        return price

    def remove(self, price_id: UUID) -> Price | None:
        price = self._by_id.pop(price_id, None)
        if price is None:
            return None
        key = self._pair_key(price.base, price.quote)
        bucket = self._by_pair.get(key, [])
        self._by_pair[key] = [p for p in bucket if p.id != price_id]
        if not self._by_pair[key]:
            del self._by_pair[key]
        return price

    def get(self, price_id: UUID) -> Price | None:
        return self._by_id.get(price_id)

    def __len__(self) -> int:
        return len(self._by_id)

    def all(self) -> tuple[Price, ...]:
        """Every stored price, newest effective date first."""
        return tuple(
            sorted(
                self._by_id.values(),
                key=lambda p: (p.effective_date, p.as_of, p.id.int),
                reverse=True,
            )
        )

    def history(
        self, base: Commodity, quote: Commodity, *, include_inverse: bool = True
    ) -> tuple[Price, ...]:
        """All stored prices for a pair, oldest effective date first."""
        bucket = self._by_pair.get(self._pair_key(base, quote), [])
        out = []
        for price in bucket:
            if price.base.id == base.id:
                out.append(price)
            elif include_inverse:
                out.append(price.inverted())
        return tuple(sorted(out, key=lambda p: (p.effective_date, p.as_of)))

    # -- lookup ------------------------------------------------------------
    def direct(
        self, base: Commodity, quote: Commodity, policy: ValuationPolicy
    ) -> Price | None:
        """Best single stored price taking ``base`` to ``quote``, or None."""
        bucket = self._by_pair.get(self._pair_key(base, quote))
        if not bucket:
            return None
        best: Price | None = None
        best_rank: tuple | None = None
        for stored in bucket:
            if not policy.admits(stored):
                continue
            if stored.base.id == base.id:
                candidate = stored
            elif policy.allow_inverse:
                candidate = stored.inverted()
            else:
                continue
            rank = policy.rank(stored)
            if best_rank is None or rank > best_rank:
                best, best_rank = candidate, rank
        return best

    def convert(
        self, base: Commodity, quote: Commodity, policy: ValuationPolicy
    ) -> Conversion:
        """Find a rate from ``base`` to ``quote``.

        Breadth-first over the graph of commodities connected by admissible
        prices, so the result uses the fewest hops possible; ties are broken
        deterministically by commodity code to keep reports reproducible.

        :raises NoPriceError: if no admissible chain exists.
        """
        if base.id == quote.id:
            return Conversion(base=base, quote=quote, rate=Fraction(1))

        direct = self.direct(base, quote, policy)
        if direct is not None:
            return Conversion(base=base, quote=quote, rate=direct.rate, prices=(direct,))
        if not policy.allow_triangulation:
            raise NoPriceError(
                f"no direct price from {base.code} to {quote.code} "
                f"and triangulation is disabled"
            )

        neighbours = self._adjacency(policy)
        start, goal = base.id, quote.id
        queue: deque[tuple[UUID, tuple[Price, ...]]] = deque([(start, ())])
        seen = {start}
        while queue:
            node, chain = queue.popleft()
            if len(chain) >= policy.max_hops:
                continue
            for step in neighbours.get(node, ()):  # already sorted
                nxt = step.quote.id
                if nxt in seen:
                    continue
                new_chain = (*chain, step)
                if nxt == goal:
                    rate = Fraction(1)
                    for price in new_chain:
                        rate *= price.rate
                    return Conversion(base=base, quote=quote, rate=rate, prices=new_chain)
                seen.add(nxt)
                queue.append((nxt, new_chain))

        raise NoPriceError(
            f"no price path from {base.code} to {quote.code} "
            f"as of {policy.as_of} within {policy.max_hops} hops"
        )

    def rate(
        self, base: Commodity, quote: Commodity, policy: ValuationPolicy
    ) -> Fraction:
        """Just the rate, for callers that do not need the audit trail."""
        return self.convert(base, quote, policy).rate

    def _adjacency(self, policy: ValuationPolicy) -> dict[UUID, tuple[Price, ...]]:
        """Best admissible price per directed commodity pair."""
        best: dict[tuple[UUID, UUID], tuple[tuple, Price]] = {}
        for price in self._by_id.values():
            if not policy.admits(price):
                continue
            rank = policy.rank(price)
            options = [price]
            if policy.allow_inverse:
                options.append(price.inverted())
            for oriented in options:
                key = (oriented.base.id, oriented.quote.id)
                current = best.get(key)
                if current is None or rank > current[0]:
                    best[key] = (rank, oriented)
        out: dict[UUID, list[Price]] = {}
        for (src, _dst), (_rank, price) in best.items():
            out.setdefault(src, []).append(price)
        return {
            src: tuple(sorted(edges, key=lambda p: (p.quote.code, p.quote.id.int)))
            for src, edges in out.items()
        }
