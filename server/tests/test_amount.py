"""Amounts: booked integers vs. exact rationals."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pymoney.core import (
    Amount,
    Balances,
    Commodity,
    CommodityMismatchError,
    RationalAmount,
    RoundingMode,
    ValidationError,
)

F = Fraction


@pytest.fixture
def usd() -> Commodity:
    return Commodity.currency("USD", symbol="$")


@pytest.fixture
def jpy() -> Commodity:
    return Commodity.currency("JPY", scale=0)


@pytest.fixture
def aapl() -> Commodity:
    return Commodity.security("AAPL")


class TestConstruction:
    def test_zero(self, usd):
        assert Amount.zero(usd).minor == 0
        assert not Amount.zero(usd)

    def test_from_major_strings(self, usd):
        assert Amount.from_major(usd, "12.34").minor == 1234
        assert Amount.from_major(usd, "-0.01").minor == -1
        assert Amount.from_major(usd, 100).minor == 10000

    def test_from_major_rounds_half_even(self, usd):
        assert Amount.from_major(usd, "12.345").minor == 1234
        assert Amount.from_major(usd, "12.355").minor == 1236

    def test_from_major_honours_mode(self, usd):
        assert Amount.from_major(usd, "12.341", RoundingMode.CEILING).minor == 1235

    def test_from_major_with_a_rational(self, usd):
        assert Amount.from_major(usd, F(10, 3)).minor == 333

    def test_scale_zero_currency(self, jpy):
        assert Amount.from_major(jpy, "1500").minor == 1500
        assert Amount.from_major(jpy, "1500.4").minor == 1500

    def test_security_precision(self, aapl):
        assert Amount.from_major(aapl, "0.5").minor == 5000

    def test_exact_from_major_accepts_representable(self, usd):
        assert Amount.exact_from_major(usd, "12.34").minor == 1234

    def test_exact_from_major_refuses_to_lose_a_fraction(self, usd):
        with pytest.raises(ValidationError, match="not representable"):
            Amount.exact_from_major(usd, F(10, 3))

    def test_minor_must_be_an_integer(self, usd):
        with pytest.raises(ValidationError, match="integer minor units"):
            Amount(usd, 1.5)  # type: ignore[arg-type]

    def test_minor_must_not_be_a_bool(self, usd):
        with pytest.raises(ValidationError):
            Amount(usd, True)  # type: ignore[arg-type]


class TestArithmetic:
    def test_add_and_subtract(self, usd):
        a, b = Amount(usd, 1000), Amount(usd, 250)
        assert (a + b).minor == 1250
        assert (a - b).minor == 750

    def test_negate_and_abs(self, usd):
        assert (-Amount(usd, 500)).minor == -500
        assert abs(Amount(usd, -500)).minor == 500

    def test_integer_scaling(self, usd):
        assert (Amount(usd, 100) * 3).minor == 300
        assert (3 * Amount(usd, 100)).minor == 300

    def test_scaling_by_a_rational_requires_rebooking(self, usd):
        assert Amount(usd, 100).scaled(F(1, 3)).minor == 33
        assert Amount(usd, 100).scaled("0.5").minor == 50

    def test_cannot_scale_by_a_fraction_directly(self, usd):
        """Silently rounding inside ``*`` would hide the rounding decision."""
        with pytest.raises(TypeError, match="only be scaled by an int"):
            Amount(usd, 100) * F(1, 3)  # type: ignore[operator]

    def test_mixing_commodities_is_refused(self, usd, jpy):
        with pytest.raises(CommodityMismatchError, match="different commodities"):
            Amount(usd, 100) + Amount(jpy, 100)

    def test_comparisons(self, usd):
        assert Amount(usd, 100) < Amount(usd, 200)
        assert Amount(usd, 100) <= Amount(usd, 100)
        assert Amount(usd, 300) > Amount(usd, 200)
        assert Amount(usd, 300) >= Amount(usd, 300)

    def test_comparisons_refuse_mixed_commodities(self, usd, jpy):
        with pytest.raises(CommodityMismatchError):
            assert Amount(usd, 1) < Amount(jpy, 1)

    def test_sign_and_zero(self, usd):
        assert Amount(usd, 5).sign == 1
        assert Amount(usd, -5).sign == -1
        assert Amount(usd, 0).sign == 0
        assert Amount(usd, 0).is_zero

    def test_equality_ignores_a_stale_commodity_copy(self, usd):
        """A renamed commodity must not make old amounts unequal."""
        renamed = Commodity(
            id=usd.id, code="USD", name="United States Dollar", display_precision=4
        )
        assert Amount(usd, 100) == Amount(renamed, 100)
        assert hash(Amount(usd, 100)) == hash(Amount(renamed, 100))

    def test_equality_with_other_types(self, usd):
        assert Amount(usd, 100) != 100
        assert Amount(usd, 100) != "1.00"


class TestFormatting:
    def test_basic(self, usd):
        assert Amount(usd, 1234).format() == "12.34 $"
        assert Amount(usd, 1234).format(symbol=False) == "12.34"

    def test_negative(self, usd):
        assert Amount(usd, -5).format(symbol=False) == "-0.05"

    def test_zero_decimals(self, jpy):
        assert Amount(jpy, 1500).format() == "1500 JPY"

    def test_grouping(self, usd):
        assert Amount(usd, 123456789).format(symbol=False, group=",") == "1,234,567.89"
        assert Amount(usd, 100).format(symbol=False, group=",") == "1.00"

    def test_explicit_precision(self, usd):
        assert Amount(usd, 1234).format(symbol=False, precision=0) == "12"

    def test_str_and_repr(self, usd):
        assert str(Amount(usd, 1234)) == "12.34 $"
        assert repr(Amount(usd, 1234)) == "Amount(USD 12.34)"

    def test_symbol_falls_back_to_code(self):
        plain = Commodity.currency("CHF")
        assert Amount(plain, 100).format() == "1.00 CHF"


class TestRationalAmount:
    def test_keeps_the_fraction(self, usd):
        value = RationalAmount(usd, F(10, 3))
        assert value.value == F(10, 3)
        assert not value.is_bookable

    def test_coerces_input(self, usd):
        assert RationalAmount(usd, Decimal("1.5")).value == F(3, 2)  # type: ignore[arg-type]

    def test_bookable_detection(self, usd):
        assert RationalAmount(usd, F(1234, 100)).is_bookable
        assert not RationalAmount(usd, F(1, 3)).is_bookable

    def test_book(self, usd):
        assert RationalAmount(usd, F(10, 3)).book().minor == 333
        assert RationalAmount(usd, F(10, 3)).book(RoundingMode.CEILING).minor == 334

    def test_arithmetic_stays_exact(self, usd):
        third = RationalAmount(usd, F(1, 3))
        assert (third + third + third).value == 1
        assert (third - third).is_zero
        assert (-third).value == F(-1, 3)
        assert (third * 3).value == 1
        assert (3 * third).value == 1
        assert (third / 2).value == F(1, 6)

    def test_division_by_zero(self, usd):
        with pytest.raises(ZeroDivisionError):
            RationalAmount(usd, F(1)) / 0

    def test_mixing_commodities_is_refused(self, usd, jpy):
        with pytest.raises(CommodityMismatchError):
            RationalAmount(usd, F(1)) + RationalAmount(jpy, F(1))

    def test_round_trip_through_amount(self, usd):
        amount = Amount(usd, 1234)
        assert amount.rational.value == F(1234, 100)
        assert amount.rational.book() == amount

    def test_sign_and_zero(self, usd):
        assert RationalAmount(usd, F(-1, 3)).sign == -1
        assert RationalAmount.zero(usd).is_zero
        assert RationalAmount(usd, F(1, 3)).sign == 1

    def test_formatting_marks_inexact_values(self, usd):
        assert RationalAmount(usd, F(10, 3)).format() == "~3.33 $"
        assert RationalAmount(usd, F(1)).format() == "1.00 $"
        assert RationalAmount(usd, F(1)).format(symbol=False, precision=3) == "1.000"

    def test_equality(self, usd, jpy):
        assert RationalAmount(usd, F(1)) == RationalAmount(usd, F(1))
        assert RationalAmount(usd, F(1)) != RationalAmount(jpy, F(1))
        assert RationalAmount(usd, F(1)) != 1
        assert hash(RationalAmount(usd, F(1))) == hash(RationalAmount(usd, F(1)))

    def test_str_and_repr(self, usd):
        assert str(RationalAmount(usd, F(1))) == "1.00 $"
        assert repr(RationalAmount(usd, F(10, 3))) == "RationalAmount(USD 10/3)"

    @given(
        minor=st.integers(min_value=-(10**12), max_value=10**12),
        scale=st.integers(min_value=0, max_value=6),
    )
    def test_booking_a_booked_amount_is_the_identity(self, minor, scale):
        commodity = Commodity.currency("TST", scale=scale)
        amount = Amount(commodity, minor)
        assert amount.rational.book() == amount


class TestBalances:
    def test_accumulates_per_commodity(self, usd, jpy):
        balances = Balances([Amount(usd, 100), Amount(jpy, 50), Amount(usd, 25)])
        assert balances.of(usd).minor == 125
        assert balances.of(jpy).minor == 50
        assert len(balances) == 2

    def test_drops_positions_that_net_to_zero(self, usd, jpy):
        balances = Balances([Amount(usd, 100), Amount(usd, -100), Amount(jpy, 1)])
        assert len(balances) == 1
        assert balances.of(usd).is_zero

    def test_missing_commodity_reads_as_zero(self, usd, jpy):
        assert Balances([Amount(jpy, 5)]).of(usd) == Amount.zero(usd)

    def test_sole(self, usd, jpy):
        assert Balances([Amount(usd, 100)]).sole().minor == 100
        with pytest.raises(CommodityMismatchError, match="exactly one commodity"):
            Balances([Amount(usd, 1), Amount(jpy, 1)]).sole()

    def test_addition_and_negation(self, usd, jpy):
        a = Balances([Amount(usd, 100)])
        b = Balances([Amount(usd, 50), Amount(jpy, 7)])
        assert (a + b).of(usd).minor == 150
        assert (a - b).of(usd).minor == 50
        assert (-b).of(jpy).minor == -7
        assert (b - b).is_zero

    def test_is_single(self, usd, jpy):
        assert Balances([Amount(usd, 1)]).is_single
        assert not Balances([Amount(usd, 1), Amount(jpy, 1)]).is_single

    def test_iteration_is_sorted_by_code(self, usd, jpy):
        balances = Balances([Amount(usd, 1), Amount(jpy, 1)])
        assert [a.commodity.code for a in balances] == ["JPY", "USD"]

    def test_equality_and_repr(self, usd):
        assert Balances([Amount(usd, 1)]) == Balances([Amount(usd, 1)])
        assert Balances([Amount(usd, 1)]) != Balances([Amount(usd, 2)])
        assert Balances([Amount(usd, 1)]) != "nope"
        assert repr(Balances([Amount(usd, 100)])) == "Balances(1.00 $)"

    def test_empty_is_zero(self):
        assert Balances().is_zero
