"""Booking legs into splits: the multi-currency rounding problem.

These are the tests that justify keeping rationals in the model at all.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pymoney.core import (
    Account,
    AccountType,
    Commodity,
    LegSpec,
    RoundingMode,
    UnbalancedTransactionError,
    ValidationError,
    book_legs,
    new_id,
)

F = Fraction


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
def aapl():
    return Commodity.security("AAPL")


class World:
    """A tiny account resolver, so these tests need no Book."""

    def __init__(self) -> None:
        self.accounts: dict = {}

    def account(self, commodity, type_=AccountType.CASH, name=None) -> Account:
        account = Account(
            id=new_id(),
            type=type_,
            name=name or f"{commodity.code} account",
            commodity=commodity,
        )
        self.accounts[account.id] = account
        return account

    def resolve(self, account_id):
        return self.accounts[account_id]


@pytest.fixture
def world():
    return World()


def book(world, legs, currency, **kwargs):
    return book_legs(legs, currency=currency, resolve_account=world.resolve, **kwargs)


class TestSingleCurrency:
    def test_two_legs_with_a_residual(self, world, eur):
        cash = world.account(eur)
        food = world.account(eur, AccountType.EXPENSE)
        result = book(
            world,
            [LegSpec.of(cash.id, "-23.40"), LegSpec.of(food.id)],
            eur,
        )
        assert [s.value.minor for s in result.splits] == [-2340, 2340]
        assert [s.quantity.minor for s in result.splits] == [-2340, 2340]
        assert not result.was_rounded

    def test_quantity_equals_value_in_the_transaction_currency(self, world, eur):
        cash = world.account(eur)
        food = world.account(eur, AccountType.EXPENSE)
        result = book(world, [LegSpec.of(cash.id, "-10"), LegSpec.of(food.id)], eur)
        for s in result.splits:
            assert s.quantity == s.value

    def test_many_legs(self, world, eur):
        cash = world.account(eur)
        a = world.account(eur, AccountType.EXPENSE)
        b = world.account(eur, AccountType.EXPENSE)
        result = book(
            world,
            [
                LegSpec.of(cash.id, "-100.00"),
                LegSpec.of(a.id, "60.00"),
                LegSpec.of(b.id),
            ],
            eur,
        )
        assert [s.value.minor for s in result.splits] == [-10000, 6000, 4000]

    def test_value_and_quantity_must_agree(self, world, eur):
        cash = world.account(eur)
        food = world.account(eur, AccountType.EXPENSE)
        with pytest.raises(ValidationError, match="must be equal"):
            book(
                world,
                [
                    LegSpec.of(cash.id, "-10", quantity="-11"),
                    LegSpec.of(food.id),
                ],
                eur,
            )

    def test_supplying_only_a_quantity_is_fine(self, world, eur):
        cash = world.account(eur)
        food = world.account(eur, AccountType.EXPENSE)
        result = book(
            world,
            [LegSpec.of(cash.id, quantity="-10"), LegSpec.of(food.id)],
            eur,
        )
        assert [s.value.minor for s in result.splits] == [-1000, 1000]

    def test_unbalanced_without_a_residual_leg(self, world, eur):
        cash = world.account(eur)
        food = world.account(eur, AccountType.EXPENSE)
        with pytest.raises(UnbalancedTransactionError, match="off by"):
            book(
                world,
                [LegSpec.of(cash.id, "-10"), LegSpec.of(food.id, "9")],
                eur,
            )

    def test_imbalance_account_soaks_up_the_difference(self, world, eur):
        cash = world.account(eur)
        food = world.account(eur, AccountType.EXPENSE)
        imbalance = world.account(eur, AccountType.EQUITY)
        result = book(
            world,
            [LegSpec.of(cash.id, "-10"), LegSpec.of(food.id, "9")],
            eur,
            imbalance_account_id=imbalance.id,
        )
        assert len(result.splits) == 3
        assert result.splits[2].value.minor == 100
        assert result.splits[2].memo == "Imbalance"
        assert sum(s.value.minor for s in result.splits) == 0

    def test_at_most_one_residual_leg(self, world, eur):
        a = world.account(eur)
        b = world.account(eur)
        c = world.account(eur)
        with pytest.raises(ValidationError, match="at most one leg"):
            book(
                world,
                [LegSpec.of(a.id, "-10"), LegSpec.of(b.id), LegSpec.of(c.id)],
                eur,
            )

    def test_needs_at_least_one_leg(self, world, eur):
        with pytest.raises(ValidationError, match="at least one leg"):
            book(world, [], eur)


class TestRoundingIsPushedOntoInexactLegs:
    def test_the_ten_thirds_problem(self, world, eur):
        """10/3 EUR split three ways must still book to exactly 10.00."""
        cash = world.account(eur)
        a = world.account(eur, AccountType.EXPENSE)
        b = world.account(eur, AccountType.EXPENSE)
        c = world.account(eur, AccountType.EXPENSE)
        third = F(10, 3)
        result = book(
            world,
            [
                LegSpec.of(cash.id, "-10.00"),
                LegSpec.of(a.id, third),
                LegSpec.of(b.id, third),
                LegSpec.of(c.id, third),
            ],
            eur,
        )
        values = [s.value.minor for s in result.splits]
        assert sum(values) == 0
        assert values[0] == -1000
        assert sorted(values[1:]) == [333, 333, 334]
        assert result.was_rounded

    def test_an_exact_leg_is_never_distorted(self, world, eur, usd):
        """Paying an invoice of exactly 100.00 EUR from a USD account."""
        usd_cash = world.account(usd)
        supplier = world.account(eur, AccountType.EXPENSE)
        result = book(
            world,
            [
                LegSpec.of(supplier.id, "100.00"),
                LegSpec.of(usd_cash.id, price=F(3, 10)),
            ],
            eur,
        )
        by_account = {s.account_id: s for s in result.splits}
        assert by_account[supplier.id].value.minor == 10000
        assert by_account[usd_cash.id].value.minor == -10000
        # -100 EUR / (3/10 EUR per USD) = -1000/3 USD -> -333.33
        assert by_account[usd_cash.id].quantity.minor == -33333

    def test_adjustments_are_reported_exactly(self, world, eur):
        cash = world.account(eur)
        a = world.account(eur, AccountType.EXPENSE)
        b = world.account(eur, AccountType.EXPENSE)
        result = book(
            world,
            [
                LegSpec.of(cash.id, "-10.00"),
                LegSpec.of(a.id, F(10, 3)),
                LegSpec.of(b.id, F(20, 3)),
            ],
            eur,
        )
        assert result.rounding_adjustments[0] == 0
        assert sum(result.rounding_adjustments) == 0
        assert result.total_adjustment == F(2, 3)

    def test_exact_values_report_no_adjustment(self, world, eur):
        cash = world.account(eur)
        food = world.account(eur, AccountType.EXPENSE)
        result = book(world, [LegSpec.of(cash.id, "-5.00"), LegSpec.of(food.id)], eur)
        assert result.total_adjustment == 0
        assert not result.was_rounded

    def test_zero_decimal_currency(self, world, jpy):
        """JPY has no minor unit; thirds of a yen have to go somewhere."""
        cash = world.account(jpy)
        a = world.account(jpy, AccountType.EXPENSE)
        b = world.account(jpy, AccountType.EXPENSE)
        c = world.account(jpy, AccountType.EXPENSE)
        result = book(
            world,
            [
                LegSpec.of(cash.id, "-100"),
                LegSpec.of(a.id, F(100, 3)),
                LegSpec.of(b.id, F(100, 3)),
                LegSpec.of(c.id, F(100, 3)),
            ],
            jpy,
        )
        values = [s.value.minor for s in result.splits]
        assert sum(values) == 0
        assert sorted(values[1:]) == [33, 33, 34]


class TestForeignCurrencyLegs:
    def test_price_derives_the_quantity(self, world, eur, usd):
        eur_cash = world.account(eur)
        usd_cash = world.account(usd)
        result = book(
            world,
            [
                LegSpec.of(eur_cash.id, "-90.00"),
                LegSpec.of(usd_cash.id, "90.00", price=F(9, 10)),
            ],
            eur,
        )
        usd_split = result.splits[1]
        assert usd_split.value.minor == 9000
        assert usd_split.quantity.minor == 10000  # 90 EUR / 0.9 = 100 USD
        assert usd_split.price == F(9, 10)

    def test_quantity_and_price_derive_the_value(self, world, eur, usd):
        eur_cash = world.account(eur)
        usd_cash = world.account(usd)
        result = book(
            world,
            [
                LegSpec.of(usd_cash.id, quantity="100.00", price=F(9, 10)),
                LegSpec.of(eur_cash.id),
            ],
            eur,
        )
        assert result.splits[0].value.minor == 9000
        assert result.splits[1].value.minor == -9000

    def test_explicit_value_and_quantity_need_no_price(self, world, eur, usd):
        eur_cash = world.account(eur)
        usd_cash = world.account(usd)
        result = book(
            world,
            [
                LegSpec.of(usd_cash.id, "90.00", quantity="100.00"),
                LegSpec.of(eur_cash.id),
            ],
            eur,
        )
        assert result.splits[0].quantity.minor == 10000
        assert result.splits[0].value.minor == 9000

    def test_a_foreign_leg_needs_a_price_or_a_quantity(self, world, eur, usd):
        usd_cash = world.account(usd)
        eur_cash = world.account(eur)
        with pytest.raises(ValidationError, match="needs a price or a quantity"):
            book(
                world,
                [LegSpec.of(usd_cash.id, "90.00"), LegSpec.of(eur_cash.id)],
                eur,
            )

    def test_a_foreign_leg_needs_a_price_or_a_value(self, world, eur, usd):
        usd_cash = world.account(usd)
        eur_cash = world.account(eur)
        with pytest.raises(ValidationError, match="needs a price or a value"):
            book(
                world,
                [
                    LegSpec.of(usd_cash.id, quantity="100.00"),
                    LegSpec.of(eur_cash.id),
                ],
                eur,
            )

    def test_a_foreign_residual_leg_needs_a_price(self, world, eur, usd):
        eur_cash = world.account(eur)
        usd_cash = world.account(usd)
        with pytest.raises(ValidationError, match=r"residual leg.*needs a price"):
            book(
                world,
                [LegSpec.of(eur_cash.id, "-90.00"), LegSpec.of(usd_cash.id)],
                eur,
            )

    def test_share_purchase(self, world, usd, aapl):
        """The canonical investment posting: cash out, shares in."""
        cash = world.account(usd)
        shares = world.account(aapl, AccountType.STOCK)
        fees = world.account(usd, AccountType.EXPENSE)
        result = book(
            world,
            [
                LegSpec.of(shares.id, quantity="3", price="127.5501", action="BUY"),
                LegSpec.of(fees.id, "1.95"),
                LegSpec.of(cash.id),
            ],
            usd,
        )
        share_split, fee_split, cash_split = result.splits
        # 3 * 127.5501 = 382.6503 -> 382.65
        assert share_split.value.minor == 38265
        assert share_split.quantity.minor == 30000
        assert fee_split.value.minor == 195
        assert cash_split.value.minor == -(38265 + 195)
        assert sum(s.value.minor for s in result.splits) == 0

    def test_fractional_shares(self, world, usd, aapl):
        cash = world.account(usd)
        shares = world.account(aapl, AccountType.STOCK)
        result = book(
            world,
            [
                LegSpec.of(shares.id, "100.00", price="190.50"),
                LegSpec.of(cash.id),
            ],
            usd,
        )
        # 100 / 190.50 = 0.524934... -> 0.5249 at 4 decimals
        assert result.splits[0].quantity.minor == 5249

    def test_quantity_rounding_mode_is_honoured(self, world, usd, aapl):
        cash = world.account(usd)
        shares = world.account(aapl, AccountType.STOCK)
        result = book(
            world,
            [
                LegSpec.of(shares.id, "100.00", price="190.50"),
                LegSpec.of(cash.id),
            ],
            usd,
            rounding=RoundingMode.CEILING,
        )
        assert result.splits[0].quantity.minor == 5250

    def test_a_negative_price_is_refused(self, world, usd):
        account = world.account(usd)
        with pytest.raises(ValidationError, match="price must be positive"):
            LegSpec.of(account.id, "10", price="-1")


class TestLegSpec:
    def test_coerces_exact_numeric_types(self):
        account_id = new_id()
        leg = LegSpec.of(account_id, Decimal("1.50"), quantity="2/3", price=2)
        assert leg.value == F(3, 2)
        assert leg.quantity == F(2, 3)
        assert leg.price == F(2)

    def test_rejects_floats(self):
        with pytest.raises(TypeError, match="float is not accepted"):
            LegSpec.of(new_id(), 1.5)

    def test_is_residual(self):
        assert LegSpec.of(new_id()).is_residual
        assert not LegSpec.of(new_id(), "1").is_residual
        assert not LegSpec.of(new_id(), quantity="1").is_residual

    def test_carries_metadata_through_to_the_split(self, world, eur):
        cash = world.account(eur)
        food = world.account(eur, AccountType.EXPENSE)
        payee = new_id()
        result = book(
            world,
            [
                LegSpec.of(
                    cash.id,
                    "-10",
                    memo="card payment",
                    number="0815",
                    payee_id=payee,
                    action="WITHDRAWAL",
                ),
                LegSpec.of(food.id),
            ],
            eur,
        )
        split = result.splits[0]
        assert split.memo == "card payment"
        assert split.number == "0815"
        assert split.payee_id == payee
        assert split.action == "WITHDRAWAL"

    def test_split_ids_come_from_the_leg(self, world, eur):
        """Stable ids let a client update a split it already knows about."""
        cash = world.account(eur)
        food = world.account(eur, AccountType.EXPENSE)
        leg = LegSpec.of(cash.id, "-10")
        result = book(world, [leg, LegSpec.of(food.id)], eur)
        assert result.splits[0].id == leg.id


class TestPostingProperties:
    """Hypothesis: the guarantees that must hold for every possible input."""

    @given(
        amounts=st.lists(
            st.fractions(min_value=-(10**5), max_value=10**5, max_denominator=997),
            min_size=1,
            max_size=8,
        ),
        scale=st.integers(min_value=0, max_value=4),
    )
    def test_values_always_sum_to_zero(self, amounts, scale):
        currency = Commodity.currency("TST", scale=scale)
        world = World()
        legs = [LegSpec.of(world.account(currency).id, amount) for amount in amounts]
        legs.append(LegSpec.of(world.account(currency).id))
        result = book_legs(legs, currency=currency, resolve_account=world.resolve)
        assert sum(s.value.minor for s in result.splits) == 0

    @given(
        amounts=st.lists(
            st.fractions(min_value=-(10**5), max_value=10**5, max_denominator=997),
            min_size=1,
            max_size=8,
        ),
        scale=st.integers(min_value=0, max_value=4),
    )
    def test_no_leg_is_distorted_by_more_than_one_minor_unit(self, amounts, scale):
        currency = Commodity.currency("TST", scale=scale)
        world = World()
        legs = [LegSpec.of(world.account(currency).id, amount) for amount in amounts]
        legs.append(LegSpec.of(world.account(currency).id))
        result = book_legs(legs, currency=currency, resolve_account=world.resolve)
        for adjustment in result.rounding_adjustments:
            assert abs(adjustment) < 1

    @given(
        exact=st.integers(min_value=-(10**7), max_value=10**7),
        inexact=st.lists(
            st.fractions(min_value=-(10**4), max_value=10**4, max_denominator=97),
            min_size=1,
            max_size=5,
        ),
    )
    def test_representable_legs_are_never_touched(self, exact, inexact):
        """The cent always lands on a leg that was inexact to begin with."""
        currency = Commodity.currency("TST", scale=2)
        world = World()
        exact_value = F(exact, 100)
        legs = [LegSpec.of(world.account(currency).id, exact_value)]
        legs.extend(LegSpec.of(world.account(currency).id, value) for value in inexact)
        legs.append(LegSpec.of(world.account(currency).id))
        result = book_legs(legs, currency=currency, resolve_account=world.resolve)
        assert result.splits[0].value.minor == exact * 1

    @given(
        value=st.fractions(min_value=1, max_value=10**5, max_denominator=997),
        rate=st.fractions(min_value=F(1, 997), max_value=1000, max_denominator=997),
        quote_scale=st.integers(min_value=0, max_value=6),
    )
    def test_foreign_legs_balance_in_the_transaction_currency(
        self, value, rate, quote_scale
    ):
        currency = Commodity.currency("HOM", scale=2)
        foreign = Commodity.currency("FOR", scale=quote_scale)
        world = World()
        local = world.account(currency)
        abroad = world.account(foreign)
        result = book_legs(
            [
                LegSpec.of(local.id, value),
                LegSpec.of(abroad.id, -value, price=rate),
            ],
            currency=currency,
            resolve_account=world.resolve,
        )
        assert sum(s.value.minor for s in result.splits) == 0
        assert result.splits[1].quantity.sign in (0, -1)


class TestOverSpecifiedLegs:
    """value + quantity + price: allowed, but checked for self-consistency."""

    def test_mutually_contradictory_numbers_are_refused(self, world, eur, usd):
        """``value=10, quantity=2, price=3`` is a typo, not a transaction."""
        cash = world.account(usd)
        other = world.account(eur, AccountType.EXPENSE)
        with pytest.raises(ValidationError, match="over-specified and inconsistent"):
            book(
                world,
                [
                    LegSpec.of(cash.id, value="10", quantity="2", price="3"),
                    LegSpec.of(other.id),
                ],
                eur,
            )

    def test_the_message_names_the_implied_price(self, world, eur, usd):
        cash = world.account(usd)
        other = world.account(eur, AccountType.EXPENSE)
        with pytest.raises(ValidationError, match="implies a price of 5, not 3"):
            book(
                world,
                [
                    LegSpec.of(cash.id, value="10", quantity="2", price="3"),
                    LegSpec.of(other.id),
                ],
                eur,
            )

    def test_exactly_consistent_numbers_are_accepted(self, world, eur, usd):
        cash = world.account(usd)
        other = world.account(eur, AccountType.EXPENSE)
        result = book(
            world,
            [
                LegSpec.of(cash.id, value="6", quantity="2", price="3"),
                LegSpec.of(other.id),
            ],
            eur,
        )
        assert result.splits[0].value.minor == 600
        assert result.splits[0].quantity.minor == 200
        assert result.splits[0].price == F(3)

    def test_a_pre_rounded_value_is_accepted(self, world, usd, aapl):
        """3 shares at 127.5501 is 382.6503; a UI sends 382.65."""
        cash = world.account(usd)
        shares = world.account(aapl, AccountType.STOCK)
        result = book(
            world,
            [
                LegSpec.of(shares.id, value="382.65", quantity="3", price="127.5501"),
                LegSpec.of(cash.id),
            ],
            usd,
        )
        share_split = result.splits[0]
        assert share_split.value.minor == 38265
        assert share_split.quantity.minor == 30000
        # the stated price is kept, even though the booked figures imply 127.55
        assert share_split.price == F(1275501, 10000)
        assert share_split.implied_price == F(2551, 20)

    def test_a_pre_rounded_quantity_is_accepted(self, world, usd, aapl):
        """100 USD at 190.50 is 0.524934... shares; a UI sends 0.5249."""
        cash = world.account(usd)
        shares = world.account(aapl, AccountType.STOCK)
        result = book(
            world,
            [
                LegSpec.of(shares.id, value="100.00", quantity="0.5249", price="190.50"),
                LegSpec.of(cash.id),
            ],
            usd,
        )
        assert result.splits[0].quantity.minor == 5249

    def test_the_tolerance_is_one_minor_unit_on_each_side(self, world, eur):
        """Just outside the tolerance still fails, so the check has teeth."""
        cash = world.account(eur)
        other = world.account(eur, AccountType.EXPENSE)
        # same-commodity leg: value == quantity == 100, so the price must be ~1
        with pytest.raises(ValidationError, match="over-specified"):
            book(
                world,
                [
                    LegSpec.of(cash.id, value="100", quantity="100", price="2"),
                    LegSpec.of(other.id),
                ],
                eur,
            )

    def test_a_price_of_one_is_consistent_in_the_transaction_currency(self, world, eur):
        cash = world.account(eur)
        other = world.account(eur, AccountType.EXPENSE)
        result = book(
            world,
            [LegSpec.of(cash.id, "-10.00", price=1), LegSpec.of(other.id)],
            eur,
        )
        assert result.splits[0].price == F(1)

    def test_a_price_on_a_zero_value_leg_is_refused(self, world, usd, aapl):
        """A stock split moves shares at no value; a price there is nonsense."""
        shares = world.account(aapl, AccountType.STOCK)
        cash = world.account(usd)
        with pytest.raises(ValidationError, match="over-specified"):
            book(
                world,
                [
                    LegSpec.of(shares.id, value="0", quantity="36", price="100"),
                    LegSpec.of(cash.id, "0"),
                ],
                usd,
            )

    def test_a_stock_split_without_a_price_is_fine(self, world, usd, aapl):
        shares = world.account(aapl, AccountType.STOCK)
        cash = world.account(usd)
        result = book(
            world,
            [
                LegSpec.of(shares.id, value="0", quantity="36"),
                LegSpec.of(cash.id, "0"),
            ],
            usd,
        )
        assert result.splits[0].quantity.minor == 360000
        assert result.splits[0].value.is_zero
