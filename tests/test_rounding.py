"""Rounding and allocation: the arithmetic everything else depends on."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from pymoney.core import ValidationError
from pymoney.core.rounding import (
    DEFAULT_ROUNDING,
    RoundingMode,
    allocate,
    allocate_to_zero_sum,
    distribute,
    round_fraction,
    to_fraction,
)

F = Fraction


class TestToFraction:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1, F(1)),
            (-7, F(-7)),
            ("1/3", F(1, 3)),
            ("2.50", F(5, 2)),
            (Decimal("0.1"), F(1, 10)),
            (F(10, 3), F(10, 3)),
        ],
    )
    def test_accepts_exact_inputs(self, value, expected):
        assert to_fraction(value) == expected

    def test_rejects_float(self):
        """0.1 is not one tenth; accepting floats would corrupt the ledger."""
        with pytest.raises(TypeError, match="float is not accepted"):
            to_fraction(0.1)

    def test_rejects_bool(self):
        with pytest.raises(TypeError, match="bool"):
            to_fraction(True)

    def test_rejects_nan(self):
        with pytest.raises(ValueError, match="non-finite"):
            to_fraction(Decimal("NaN"))

    def test_rejects_unknown_type(self):
        with pytest.raises(TypeError, match="cannot convert"):
            to_fraction(object())  # type: ignore[arg-type]


class TestRoundFraction:
    @pytest.mark.parametrize(
        ("value", "mode", "expected"),
        [
            # exact values are never touched
            (F(5), RoundingMode.UP, 5),
            (F(-5), RoundingMode.CEILING, -5),
            # directional
            (F(3, 2), RoundingMode.FLOOR, 1),
            (F(3, 2), RoundingMode.CEILING, 2),
            (F(-3, 2), RoundingMode.FLOOR, -2),
            (F(-3, 2), RoundingMode.CEILING, -1),
            (F(7, 5), RoundingMode.DOWN, 1),
            (F(-7, 5), RoundingMode.DOWN, -1),
            (F(7, 5), RoundingMode.UP, 2),
            (F(-7, 5), RoundingMode.UP, -2),
            # not halfway: mode is irrelevant
            (F(6, 5), RoundingMode.HALF_UP, 1),
            (F(9, 5), RoundingMode.HALF_EVEN, 2),
            (F(-9, 5), RoundingMode.HALF_DOWN, -2),
            # exactly halfway
            (F(1, 2), RoundingMode.HALF_UP, 1),
            (F(1, 2), RoundingMode.HALF_DOWN, 0),
            (F(1, 2), RoundingMode.HALF_EVEN, 0),
            (F(3, 2), RoundingMode.HALF_EVEN, 2),
            (F(5, 2), RoundingMode.HALF_EVEN, 2),
            (F(-1, 2), RoundingMode.HALF_UP, -1),
            (F(-1, 2), RoundingMode.HALF_DOWN, 0),
            (F(-1, 2), RoundingMode.HALF_EVEN, 0),
            (F(-3, 2), RoundingMode.HALF_EVEN, -2),
        ],
    )
    def test_modes(self, value, mode, expected):
        assert round_fraction(value, mode) == expected

    def test_default_is_bankers_rounding(self):
        assert DEFAULT_ROUNDING is RoundingMode.HALF_EVEN
        assert round_fraction(F(1, 2)) == 0
        assert round_fraction(F(3, 2)) == 2

    @given(
        num=st.integers(min_value=-(10**9), max_value=10**9),
        den=st.integers(min_value=1, max_value=10**6),
        mode=st.sampled_from(list(RoundingMode)),
    )
    def test_result_is_always_a_neighbour(self, num, den, mode):
        """Any mode returns floor or ceil; nothing else is a rounding."""
        value = F(num, den)
        result = round_fraction(value, mode)
        low = value.numerator // value.denominator
        high = low if value.denominator == 1 else low + 1
        assert result in (low, high)

    @given(
        num=st.integers(min_value=-(10**6), max_value=10**6),
        den=st.integers(min_value=1, max_value=10**4),
    )
    def test_half_even_is_symmetric(self, num, den):
        """Banker's rounding must not be biased by sign."""
        value = F(num, den)
        assert round_fraction(-value) == -round_fraction(value)

    @given(
        num=st.integers(min_value=-(10**6), max_value=10**6),
        den=st.integers(min_value=1, max_value=10**4),
    )
    def test_directional_modes_bracket_the_value(self, num, den):
        value = F(num, den)
        assert round_fraction(value, RoundingMode.FLOOR) <= value
        assert round_fraction(value, RoundingMode.CEILING) >= value
        assert abs(round_fraction(value, RoundingMode.DOWN)) <= abs(value)
        assert abs(round_fraction(value, RoundingMode.UP)) >= abs(value)


class TestDistribute:
    def test_exact_shares_are_untouched(self):
        shares = [F(1), F(2), F(3)]
        assert distribute(shares, 6) == [1, 2, 3]

    def test_hands_units_to_largest_remainders(self):
        # floors are 0, 0, 0 and we owe 2 units: the two largest fractions win
        shares = [F(1, 10), F(9, 10), F(1, 2)]
        assert distribute(shares, 2) == [0, 1, 1]

    def test_takes_units_back_when_total_is_lower(self):
        shares = [F(11, 10), F(19, 10), F(15, 10)]  # floors 1, 1, 1 = 3
        # one unit has to come back, and it comes off the smallest fraction
        assert distribute(shares, 2) == [0, 1, 1]

    def test_ties_break_on_position(self):
        shares = [F(1, 2), F(1, 2)]
        assert distribute(shares, 1) == [1, 0]

    def test_rejects_empty(self):
        with pytest.raises(ValidationError, match="zero legs"):
            distribute([], 0)

    def test_rejects_impossible_total(self):
        """Refuse to distort a leg by more than one minor unit."""
        with pytest.raises(ValidationError, match="too far"):
            distribute([F(1), F(1)], 100)

    @given(
        shares=st.lists(
            st.fractions(min_value=-1000, max_value=1000, max_denominator=97),
            min_size=1,
            max_size=12,
        ),
        wobble=st.integers(min_value=-3, max_value=3),
    )
    def test_sums_exactly_and_never_distorts_by_more_than_one(self, shares, wobble):
        exact = sum(shares, F(0))
        booked_total = round_fraction(exact) + wobble
        assume(
            abs(booked_total - sum(s.numerator // s.denominator for s in shares))
            <= len(shares)
        )
        result = distribute(shares, booked_total)
        assert sum(result) == booked_total
        for value, share in zip(result, shares, strict=True):
            assert abs(value - share) < 2


class TestAllocate:
    def test_classic_three_way_split(self):
        """10/3 split three ways must still book to 10 units, not 9."""
        result = allocate(F(10), [F(1), F(1), F(1)])
        assert result == [4, 3, 3]
        assert sum(result) == 10

    def test_one_third_of_a_cent_problem(self):
        """100 cents in three equal parts."""
        result = allocate(F(100), [F(1), F(1), F(1)])
        assert sum(result) == 100
        assert sorted(result) == [33, 33, 34]

    def test_respects_weights(self):
        result = allocate(F(100), [F(1), F(3)])
        assert result == [25, 75]

    def test_uneven_weights_keep_the_total(self):
        result = allocate(F(1000), [F(1), F(1), F(1), F(1), F(1), F(1), F(7)])
        assert sum(result) == 1000

    def test_negative_total(self):
        result = allocate(F(-10), [F(1), F(1), F(1)])
        assert sum(result) == -10
        assert sorted(result) == [-4, -3, -3]

    def test_rejects_empty_weights(self):
        with pytest.raises(ValidationError, match="zero legs"):
            allocate(F(10), [])

    def test_rejects_zero_weight_sum(self):
        with pytest.raises(ValidationError, match="summing to zero"):
            allocate(F(10), [F(1), F(-1)])

    @given(
        total=st.fractions(min_value=-(10**6), max_value=10**6, max_denominator=997),
        weights=st.lists(
            st.integers(min_value=1, max_value=1000), min_size=1, max_size=10
        ),
        mode=st.sampled_from(list(RoundingMode)),
    )
    @settings(max_examples=300)
    def test_allocation_is_exact(self, total, weights, mode):
        fractions = [F(w) for w in weights]
        result = allocate(total, fractions, mode)
        assert sum(result) == round_fraction(total, mode)
        assert len(result) == len(weights)

    @given(
        total=st.integers(min_value=-(10**6), max_value=10**6),
        n=st.integers(min_value=1, max_value=20),
    )
    def test_equal_weights_differ_by_at_most_one(self, total, n):
        result = allocate(F(total), [F(1)] * n)
        assert sum(result) == total
        assert max(result) - min(result) <= 1


class TestAllocateToZeroSum:
    def test_already_exact_values_are_untouched(self):
        values = [F(500), F(-200), F(-300)]
        assert allocate_to_zero_sum(values) == [500, -200, -300]

    def test_pushes_the_cent_onto_an_inexact_leg(self):
        """An exactly representable leg must never absorb a rounding error."""
        values = [F(-1000), F(1000, 3), F(1000, 3), F(1000, 3)]
        assert sum(values, F(0)) == 0
        result = allocate_to_zero_sum(values)
        assert sum(result) == 0
        assert result[0] == -1000  # the exact leg is preserved
        assert sorted(result[1:]) == [333, 333, 334]

    def test_rejects_values_that_do_not_cancel(self):
        with pytest.raises(ValidationError, match="must sum to zero"):
            allocate_to_zero_sum([F(1), F(1)])

    def test_rejects_empty(self):
        with pytest.raises(ValidationError, match="empty set"):
            allocate_to_zero_sum([])

    @given(
        values=st.lists(
            st.fractions(min_value=-(10**5), max_value=10**5, max_denominator=97),
            min_size=1,
            max_size=10,
        )
    )
    @settings(max_examples=300)
    def test_zero_sum_is_preserved_and_exact_legs_untouched(self, values):
        balanced = [*list(values), -sum(values, F(0))]
        result = allocate_to_zero_sum(balanced)
        assert sum(result) == 0
        for booked, exact in zip(result, balanced, strict=True):
            assert abs(booked - exact) < 1
            if exact.denominator == 1:
                assert booked == exact
