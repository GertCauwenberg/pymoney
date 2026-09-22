"""Prices: exact rates, bitemporality, and reproducible valuation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from fractions import Fraction

import pytest

from pymoney.core import (
    Amount,
    Commodity,
    NoPriceError,
    Price,
    PriceDB,
    PriceSource,
    RationalAmount,
    ValidationError,
    ValuationPolicy,
    new_id,
)

F = Fraction
T0 = datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 6, 9, 0, tzinfo=timezone.utc)
D0 = date(2026, 1, 5)


@pytest.fixture
def eur():
    return Commodity.currency("EUR")


@pytest.fixture
def usd():
    return Commodity.currency("USD")


@pytest.fixture
def jpy():
    return Commodity.currency("JPY", scale=0)


@pytest.fixture
def gbp():
    return Commodity.currency("GBP")


@pytest.fixture
def aapl():
    return Commodity.security("AAPL")


def price(base, quote, rate, *, on=D0, at=T0, source=PriceSource.ECB, **kwargs):
    return Price.of(
        base, quote, rate, effective_date=on, as_of=at, source=source, **kwargs
    )


class TestPrice:
    def test_stores_the_rate_exactly(self, eur, usd):
        p = price(eur, usd, F(10, 3))
        assert (p.num, p.den) == (10, 3)
        assert p.rate == F(10, 3)

    def test_accepts_strings_and_decimals(self, eur, usd):
        assert price(eur, usd, "1.0925").rate == F(1_0925, 10000)

    def test_inversion_is_exact(self, eur, usd):
        p = price(eur, usd, F(10, 3))
        assert p.inverted().rate == F(3, 10)
        assert p.inverted().base.id == usd.id
        assert p.inverted().inverted().rate == p.rate

    def test_inversion_preserves_identity_and_stamps(self, eur, usd):
        p = price(eur, usd, 2, id=new_id())
        flipped = p.inverted()
        assert flipped.id == p.id
        assert flipped.effective_date == p.effective_date
        assert flipped.as_of == p.as_of
        assert flipped.source == p.source

    def test_pair(self, eur, usd):
        assert price(eur, usd, 1).pair == (eur.id, usd.id)

    def test_str(self, eur, usd):
        assert str(price(eur, usd, F(10, 3))) == ("1 EUR = 10/3 USD @2026-01-05 (ecb)")

    @pytest.mark.parametrize("rate", [0, -1, F(-1, 2)])
    def test_must_be_positive(self, eur, usd, rate):
        with pytest.raises(ValidationError, match="positive"):
            price(eur, usd, rate)

    def test_rejects_zero_denominator(self, eur, usd):
        with pytest.raises(ValidationError, match="denominator"):
            Price(base=eur, quote=usd, num=1, den=0, effective_date=D0, as_of=T0)

    def test_rejects_negative_numerator(self, eur, usd):
        with pytest.raises(ValidationError, match="positive"):
            Price(base=eur, quote=usd, num=-1, den=2, effective_date=D0, as_of=T0)

    def test_rejects_the_same_commodity(self, eur):
        with pytest.raises(ValidationError, match="two different commodities"):
            price(eur, eur, 1)

    def test_every_field_is_mandatory(self):
        """All of base, quote, rate and both timestamps are required."""
        for missing in ("base", "quote", "num", "den", "effective_date", "as_of"):
            kwargs = dict(
                base=Commodity.currency("EUR"),
                quote=Commodity.currency("USD"),
                num=1,
                den=1,
                effective_date=D0,
                as_of=T0,
            )
            del kwargs[missing]
            with pytest.raises(TypeError, match=missing):
                Price(**kwargs)  # type: ignore[arg-type]

    def test_as_of_must_be_aware(self, eur, usd):
        with pytest.raises(ValidationError, match="timezone-aware"):
            Price(
                base=eur,
                quote=usd,
                num=1,
                den=1,
                effective_date=D0,
                as_of=datetime(2026, 1, 1),
            )

    def test_requires_a_source(self, eur, usd):
        with pytest.raises(ValidationError, match="source"):
            price(eur, usd, 1, source="")


class TestPriceDB:
    def test_add_get_remove(self, eur, usd):
        db = PriceDB()
        p = db.add(price(eur, usd, 2))
        assert len(db) == 1
        assert db.get(p.id) is p
        assert db.remove(p.id) is p
        assert len(db) == 0
        assert db.get(p.id) is None
        assert db.remove(p.id) is None

    def test_add_replaces_by_id(self, eur, usd):
        db = PriceDB()
        first = price(eur, usd, 2, id=new_id())
        db.add(first)
        db.add(Price.of(eur, usd, 3, effective_date=D0, as_of=T0, id=first.id))
        assert len(db) == 1
        assert db.get(first.id).rate == 3

    def test_constructor_takes_prices(self, eur, usd):
        assert len(PriceDB([price(eur, usd, 2)])) == 1

    def test_all_is_sorted_newest_first(self, eur, usd):
        db = PriceDB(
            [
                price(eur, usd, 2, on=date(2026, 2, 1)),
                price(eur, usd, 3, on=date(2026, 1, 1)),
                price(eur, usd, 4, on=date(2026, 2, 1), at=T1),
            ]
        )
        assert [p.rate for p in db.all()] == [4, 2, 3]

    def test_history_orients_to_the_requested_direction(self, eur, usd):
        db = PriceDB([price(eur, usd, 2)])
        assert [p.rate for p in db.history(eur, usd)] == [2]
        assert [p.rate for p in db.history(usd, eur)] == [F(1, 2)]
        assert db.history(usd, eur, include_inverse=False) == ()

    def test_history_of_an_unknown_pair_is_empty(self, eur, jpy):
        assert PriceDB().history(eur, jpy) == ()


class TestLookup:
    @pytest.fixture
    def db(self, eur, usd):
        return PriceDB(
            [
                price(eur, usd, "1.05", on=date(2026, 1, 1)),
                price(eur, usd, "1.10", on=date(2026, 1, 15)),
                price(eur, usd, "1.20", on=date(2026, 2, 1)),
            ]
        )

    def test_picks_the_most_recent_on_or_before_the_valuation_date(self, db, eur, usd):
        policy = ValuationPolicy(as_of=date(2026, 1, 20))
        assert db.direct(eur, usd, policy).rate == F(110, 100)

    def test_ignores_the_future(self, db, eur, usd):
        policy = ValuationPolicy(as_of=date(2026, 1, 2))
        assert db.direct(eur, usd, policy).rate == F(105, 100)

    def test_no_price_yet(self, db, eur, usd):
        policy = ValuationPolicy(as_of=date(2025, 12, 31))
        assert db.direct(eur, usd, policy) is None

    def test_unknown_pair(self, db, eur, jpy):
        assert db.direct(eur, jpy, ValuationPolicy()) is None

    def test_inverse_is_used_when_allowed(self, db, eur, usd):
        policy = ValuationPolicy(as_of=date(2026, 1, 20))
        assert db.direct(usd, eur, policy).rate == F(100, 110)

    def test_inverse_can_be_disabled(self, db, eur, usd):
        policy = ValuationPolicy(as_of=date(2026, 1, 20), allow_inverse=False)
        assert db.direct(usd, eur, policy) is None

    def test_staleness_limit(self, db, eur, usd):
        fresh = ValuationPolicy(as_of=date(2026, 1, 20), max_staleness_days=30)
        stale = ValuationPolicy(as_of=date(2026, 1, 20), max_staleness_days=2)
        assert db.direct(eur, usd, fresh) is not None
        assert db.direct(eur, usd, stale) is None


class TestBitemporality:
    """The same effective date can be known differently at different times."""

    @pytest.fixture
    def corrected(self, eur, usd):
        return PriceDB(
            [
                price(eur, usd, "1.10", on=D0, at=T0, source=PriceSource.YAHOO),
                price(eur, usd, "1.11", on=D0, at=T1, source=PriceSource.YAHOO),
            ]
        )

    def test_latest_knowledge_wins_by_default(self, corrected, eur, usd):
        policy = ValuationPolicy(as_of=date(2026, 1, 31))
        assert corrected.direct(eur, usd, policy).rate == F(111, 100)

    def test_a_knowledge_cutoff_reproduces_the_old_report(self, corrected, eur, usd):
        """Re-running last week's report must give last week's numbers."""
        policy = ValuationPolicy(
            as_of=date(2026, 1, 31),
            knowledge_time=datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc),
        )
        assert corrected.direct(eur, usd, policy).rate == F(110, 100)

    def test_a_cutoff_before_everything_finds_nothing(self, corrected, eur, usd):
        policy = ValuationPolicy(
            as_of=date(2026, 1, 31),
            knowledge_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert corrected.direct(eur, usd, policy) is None


class TestSourcePreference:
    @pytest.fixture
    def db(self, eur, usd):
        return PriceDB(
            [
                price(eur, usd, "1.10", on=D0, source=PriceSource.YAHOO),
                price(eur, usd, "1.11", on=D0, source=PriceSource.ECB),
                price(eur, usd, "1.15", on=date(2026, 1, 6), source=PriceSource.USER),
            ]
        )

    def test_preference_breaks_ties_on_the_same_date(self, db, eur, usd):
        policy = ValuationPolicy(as_of=D0, sources=(PriceSource.ECB, PriceSource.YAHOO))
        assert db.direct(eur, usd, policy).rate == F(111, 100)
        flipped = ValuationPolicy(as_of=D0, sources=(PriceSource.YAHOO, PriceSource.ECB))
        assert db.direct(eur, usd, flipped).rate == F(110, 100)

    def test_recency_beats_preference_by_default(self, db, eur, usd):
        policy = ValuationPolicy(as_of=date(2026, 1, 31), sources=(PriceSource.ECB,))
        assert db.direct(eur, usd, policy).rate == F(115, 100)

    def test_preference_can_outrank_recency(self, db, eur, usd):
        policy = ValuationPolicy(
            as_of=date(2026, 1, 31),
            sources=(PriceSource.ECB,),
            prefer_source_over_recency=True,
        )
        assert db.direct(eur, usd, policy).rate == F(111, 100)

    def test_a_hand_entered_price_wins_the_day_by_default(self, eur, usd):
        """A quote fetched later the same day must not displace what the user
        typed in; the policy has to say so explicitly to rank it otherwise."""
        later = datetime(2026, 1, 5, 18, tzinfo=timezone.utc)
        db = PriceDB(
            [
                price(eur, usd, "1.10", on=D0, source=PriceSource.USER),
                Price.of(
                    eur,
                    usd,
                    "1.12",
                    effective_date=D0,
                    as_of=later,
                    source=PriceSource.ECB,
                ),
            ]
        )
        assert db.direct(eur, usd, ValuationPolicy(as_of=D0)).rate == F(110, 100)
        # A newer *date* still wins: the rule is about the same day only.
        db.add(price(eur, usd, "1.13", on=date(2026, 1, 6), source=PriceSource.ECB))
        assert db.direct(eur, usd, ValuationPolicy(as_of=date(2026, 1, 6))).rate == F(
            113, 100
        )
        # Naming the sources replaces the default.
        explicit = ValuationPolicy(as_of=D0, sources=(PriceSource.ECB,))
        assert db.direct(eur, usd, explicit).rate == F(112, 100)

    def test_restricting_sources_excludes_everything_else(self, db, eur, usd):
        policy = ValuationPolicy(
            as_of=date(2026, 1, 31),
            sources=(PriceSource.ECB,),
            restrict_to_sources=True,
        )
        assert db.direct(eur, usd, policy).rate == F(111, 100)

    def test_restricting_to_an_absent_source_finds_nothing(self, db, eur, usd):
        policy = ValuationPolicy(
            as_of=date(2026, 1, 31),
            sources=("nasdaq",),
            restrict_to_sources=True,
        )
        assert db.direct(eur, usd, policy) is None


class TestPolicyValidation:
    def test_max_hops_must_be_positive(self):
        with pytest.raises(ValidationError, match="max_hops"):
            ValuationPolicy(max_hops=0)

    def test_staleness_must_not_be_negative(self):
        with pytest.raises(ValidationError, match="max_staleness_days"):
            ValuationPolicy(max_staleness_days=-1)

    def test_restricting_needs_sources(self):
        with pytest.raises(ValidationError, match="non-empty sources"):
            ValuationPolicy(restrict_to_sources=True)

    def test_for_date_and_for_target(self, eur):
        policy = ValuationPolicy()
        assert policy.for_date(D0).as_of == D0
        assert policy.for_target(eur).target.id == eur.id


class TestConversion:
    def test_identity(self, eur):
        conversion = PriceDB().convert(eur, eur, ValuationPolicy())
        assert conversion.rate == 1
        assert conversion.is_identity
        assert conversion.hops == 0
        assert conversion.describe() == "EUR (identity)"

    def test_direct(self, eur, usd):
        db = PriceDB([price(eur, usd, "1.10")])
        conversion = db.convert(eur, usd, ValuationPolicy(as_of=D0))
        assert conversion.rate == F(110, 100)
        assert conversion.hops == 1
        assert "EUR -> USD" in conversion.describe()

    def test_triangulation(self, eur, usd, jpy):
        db = PriceDB([price(eur, usd, F(10, 9)), price(usd, jpy, 150)])
        conversion = db.convert(eur, jpy, ValuationPolicy(as_of=D0))
        assert conversion.rate == F(10, 9) * 150
        assert conversion.hops == 2
        assert conversion.describe().startswith("EUR -> USD -> JPY")

    def test_triangulation_through_inverses(self, eur, usd, jpy):
        """USD/EUR and JPY/USD still answer a EUR->JPY question."""
        db = PriceDB([price(usd, eur, F(9, 10)), price(jpy, usd, F(1, 150))])
        conversion = db.convert(eur, jpy, ValuationPolicy(as_of=D0))
        assert conversion.rate == F(10, 9) * 150

    def test_triangulation_can_be_disabled(self, eur, usd, jpy):
        db = PriceDB([price(eur, usd, 1), price(usd, jpy, 1)])
        policy = ValuationPolicy(as_of=D0, allow_triangulation=False)
        with pytest.raises(NoPriceError, match="triangulation is disabled"):
            db.convert(eur, jpy, policy)

    def test_hop_limit(self, eur, usd, jpy, gbp):
        db = PriceDB([price(eur, gbp, 1), price(gbp, usd, 1), price(usd, jpy, 1)])
        assert db.convert(eur, jpy, ValuationPolicy(as_of=D0, max_hops=3)).hops == 3
        with pytest.raises(NoPriceError, match="within 2 hops"):
            db.convert(eur, jpy, ValuationPolicy(as_of=D0, max_hops=2))

    def test_prefers_the_shortest_path(self, eur, usd, jpy):
        db = PriceDB(
            [
                price(eur, usd, 2),
                price(usd, jpy, 3),
                price(eur, jpy, 7),
            ]
        )
        conversion = db.convert(eur, jpy, ValuationPolicy(as_of=D0))
        assert conversion.hops == 1
        assert conversion.rate == 7

    def test_no_path(self, eur, jpy):
        with pytest.raises(NoPriceError, match="no price path"):
            PriceDB().convert(eur, jpy, ValuationPolicy(as_of=D0))

    def test_rate_shortcut(self, eur, usd):
        db = PriceDB([price(eur, usd, "1.10")])
        assert db.rate(eur, usd, ValuationPolicy(as_of=D0)) == F(110, 100)

    def test_apply_keeps_the_result_exact(self, eur, usd):
        db = PriceDB([price(eur, usd, F(10, 3))])
        conversion = db.convert(eur, usd, ValuationPolicy(as_of=D0))
        result = conversion.apply(Amount.from_major(eur, "100.00"))
        assert isinstance(result, RationalAmount)
        assert result.value == F(1000, 3)
        assert result.commodity.id == usd.id
        assert result.book().minor == 33333

    def test_apply_accepts_a_rational(self, eur, usd):
        db = PriceDB([price(eur, usd, 2)])
        conversion = db.convert(eur, usd, ValuationPolicy(as_of=D0))
        assert conversion.apply(RationalAmount(eur, F(1, 3))).value == F(2, 3)

    def test_apply_checks_the_commodity(self, eur, usd, jpy):
        db = PriceDB([price(eur, usd, 2)])
        conversion = db.convert(eur, usd, ValuationPolicy(as_of=D0))
        with pytest.raises(ValidationError, match="expects EUR"):
            conversion.apply(Amount(jpy, 100))

    def test_str(self, eur, usd):
        db = PriceDB([price(eur, usd, 2)])
        conversion = db.convert(eur, usd, ValuationPolicy(as_of=D0))
        assert str(conversion) == conversion.describe()

    def test_securities_convert_like_currencies(self, aapl, usd, eur):
        """Decision #2: one mechanism for FX and for share valuation."""
        db = PriceDB([price(aapl, usd, "190.50"), price(usd, eur, F(9, 10))])
        conversion = db.convert(aapl, eur, ValuationPolicy(as_of=D0))
        assert conversion.rate == F(1905, 10) * F(9, 10)
        value = conversion.apply(Amount.from_major(aapl, "10"))
        assert value.book().minor == 171450

    def test_a_deterministic_path_is_chosen_among_equals(self, eur, usd, jpy, gbp):
        """Reproducibility: the same book must always pick the same route."""
        db = PriceDB(
            [
                price(eur, usd, 2),
                price(eur, gbp, 3),
                price(usd, jpy, 5),
                price(gbp, jpy, 7),
            ]
        )
        policy = ValuationPolicy(as_of=D0)
        rates = {db.convert(eur, jpy, policy).rate for _ in range(10)}
        assert len(rates) == 1


class TestPairBucketHousekeeping:
    """The pair index must not leak empty buckets or drop live prices."""

    def test_removing_one_of_several_keeps_the_rest(self, eur, usd):
        db = PriceDB()
        first = db.add(price(eur, usd, 2, on=date(2026, 1, 1)))
        second = db.add(price(eur, usd, 3, on=date(2026, 1, 2)))
        db.remove(first.id)
        assert len(db) == 1
        assert db.history(eur, usd) == (second,)
        assert db.direct(eur, usd, ValuationPolicy(as_of=date(2026, 1, 2))).rate == 3

    def test_removing_the_last_one_empties_the_pair(self, eur, usd):
        db = PriceDB()
        only = db.add(price(eur, usd, 2))
        db.remove(only.id)
        assert db.history(eur, usd) == ()
        assert db.direct(eur, usd, ValuationPolicy(as_of=D0)) is None
