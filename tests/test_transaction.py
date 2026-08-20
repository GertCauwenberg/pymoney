"""Transaction and split invariants, independent of any Book."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from fractions import Fraction

import pytest

from pymoney.core import (
    Amount,
    Commodity,
    ImmutableError,
    ReconcileState,
    Split,
    Transaction,
    ValidationError,
    new_id,
)

F = Fraction
D = date(2026, 1, 15)


@pytest.fixture
def eur():
    return Commodity.currency("EUR")


@pytest.fixture
def usd():
    return Commodity.currency("USD")


@pytest.fixture
def aapl():
    return Commodity.security("AAPL")


@pytest.fixture
def acc_a():
    return new_id()


@pytest.fixture
def acc_b():
    return new_id()


def split(account_id, commodity, minor, currency=None, value_minor=None, **kwargs):
    currency = currency or commodity
    value_minor = minor if value_minor is None else value_minor
    return Split(
        account_id=account_id,
        quantity=Amount(commodity, minor),
        value=Amount(currency, value_minor),
        **kwargs,
    )


def txn(currency, splits, **kwargs):
    return Transaction(date=D, currency=currency, splits=tuple(splits), **kwargs)


class TestSplit:
    def test_account_quantity_and_value_are_mandatory(self, eur, acc_a):
        """A split without all three is not a split; the type system says so."""
        with pytest.raises(TypeError, match="account_id"):
            Split(quantity=Amount(eur, 1), value=Amount(eur, 1))
        with pytest.raises(TypeError, match="value"):
            Split(account_id=acc_a, quantity=Amount(eur, 1))
        with pytest.raises(TypeError, match="quantity"):
            Split(account_id=acc_a, value=Amount(eur, 1))

    def test_price_must_be_positive(self, eur, acc_a):
        with pytest.raises(ValidationError, match="price must be positive"):
            split(acc_a, eur, 100, price=F(-1))

    def test_commodity_comes_from_the_quantity(self, aapl, usd, acc_a):
        s = split(acc_a, aapl, 30000, currency=usd, value_minor=500000)
        assert s.commodity.id == aapl.id

    def test_implied_price(self, aapl, usd, acc_a):
        s = split(acc_a, aapl, 30000, currency=usd, value_minor=571650)
        assert s.implied_price == F(571650, 100) / F(30000, 10000)

    def test_implied_price_of_a_zero_quantity_is_none(self, aapl, usd, acc_a):
        assert split(acc_a, aapl, 0, currency=usd, value_minor=100).implied_price is None

    def test_str(self, eur, acc_a):
        assert str(split(acc_a, eur, 1234)) == "12.34 EUR (= 12.34 EUR)"

    def test_is_locked(self, eur, acc_a):
        assert not split(acc_a, eur, 1).is_locked
        assert (
            split(acc_a, eur, 1, reconcile_state=ReconcileState.CLEARED).is_locked
            is False
        )
        assert split(acc_a, eur, 1, reconcile_state=ReconcileState.RECONCILED).is_locked


class TestReconcileState:
    def test_is_ordered(self):
        assert ReconcileState.NOT_RECONCILED < ReconcileState.CLEARED
        assert ReconcileState.CLEARED < ReconcileState.RECONCILED
        assert ReconcileState.RECONCILED < ReconcileState.FROZEN

    def test_locking(self):
        assert not ReconcileState.NOT_RECONCILED.is_locked
        assert not ReconcileState.CLEARED.is_locked
        assert ReconcileState.RECONCILED.is_locked
        assert ReconcileState.FROZEN.is_locked
        assert ReconcileState.FROZEN.is_final
        assert not ReconcileState.RECONCILED.is_final

    def test_transitions(self, eur, acc_a):
        s = split(acc_a, eur, 1)
        cleared = s.with_reconcile_state(ReconcileState.CLEARED, on=D)
        assert cleared.reconcile_state is ReconcileState.CLEARED
        assert cleared.reconcile_date == D
        back = cleared.with_reconcile_state(ReconcileState.NOT_RECONCILED)
        assert back.reconcile_state is ReconcileState.NOT_RECONCILED
        assert back.reconcile_date is None

    def test_reconciled_can_be_walked_back(self, eur, acc_a):
        """Explicit unlock is allowed; silent mutation is not (decision #11)."""
        s = split(acc_a, eur, 1, reconcile_state=ReconcileState.RECONCILED)
        assert (
            s.with_reconcile_state(ReconcileState.NOT_RECONCILED).reconcile_state
            is ReconcileState.NOT_RECONCILED
        )

    def test_frozen_is_final(self, eur, acc_a):
        s = split(acc_a, eur, 1, reconcile_state=ReconcileState.FROZEN)
        with pytest.raises(ImmutableError, match="frozen"):
            s.with_reconcile_state(ReconcileState.NOT_RECONCILED)
        assert s.with_reconcile_state(ReconcileState.FROZEN) == s


class TestTransactionValidation:
    def test_a_simple_balanced_transaction(self, eur, acc_a, acc_b):
        t = txn(eur, [split(acc_a, eur, -5000), split(acc_b, eur, 5000)])
        t.validate()
        assert t.is_balanced

    def test_date_currency_and_splits_are_mandatory(self, eur, acc_a, acc_b):
        splits = (split(acc_a, eur, -1), split(acc_b, eur, 1))
        with pytest.raises(TypeError, match="date"):
            Transaction(currency=eur, splits=splits)
        with pytest.raises(TypeError, match="currency"):
            Transaction(date=D, splits=splits)
        with pytest.raises(TypeError, match="splits"):
            Transaction(date=D, currency=eur)

    def test_requires_at_least_two_splits(self, eur, acc_a):
        with pytest.raises(ValidationError, match="at least 2 splits"):
            txn(eur, [split(acc_a, eur, 0)]).validate()
        with pytest.raises(ValidationError, match="at least 2 splits"):
            txn(eur, []).validate()

    def test_values_must_sum_to_zero(self, eur, acc_a, acc_b):
        t = txn(eur, [split(acc_a, eur, -5000), split(acc_b, eur, 4999)])
        assert not t.is_balanced
        with pytest.raises(ValidationError, match="sum to zero"):
            t.validate()

    def test_error_message_names_the_difference(self, eur, acc_a, acc_b):
        t = txn(eur, [split(acc_a, eur, -5000), split(acc_b, eur, 4999)])
        with pytest.raises(ValidationError, match=r"off by -0\.01"):
            t.validate()

    def test_split_values_must_be_in_the_transaction_currency(
        self, eur, usd, acc_a, acc_b
    ):
        t = txn(
            eur,
            [
                split(acc_a, eur, -100),
                Split(
                    account_id=acc_b,
                    quantity=Amount(usd, 100),
                    value=Amount(usd, 100),
                ),
            ],
        )
        with pytest.raises(ValidationError, match="transaction currency is EUR"):
            t.validate()

    def test_same_commodity_splits_must_have_equal_quantity_and_value(
        self, eur, acc_a, acc_b
    ):
        t = txn(
            eur,
            [
                split(acc_a, eur, -100),
                split(acc_b, eur, 50, value_minor=100),
            ],
        )
        with pytest.raises(ValidationError, match="equal quantity and value"):
            t.validate()

    def test_quantity_and_value_may_not_have_opposite_signs(
        self, aapl, eur, acc_a, acc_b
    ):
        t = txn(
            eur,
            [
                split(acc_a, eur, -10000),
                split(acc_b, aapl, -30000, currency=eur, value_minor=10000),
            ],
        )
        with pytest.raises(ValidationError, match="opposite signs"):
            t.validate()

    def test_a_zero_quantity_against_a_value_is_allowed(self, aapl, eur, acc_a, acc_b):
        """A return of capital moves value without moving shares."""
        t = txn(
            eur,
            [
                split(acc_a, eur, 10000),
                split(acc_b, aapl, 0, currency=eur, value_minor=-10000),
            ],
        )
        t.validate()

    def test_duplicate_split_ids_are_refused(self, eur, acc_a, acc_b):
        shared = new_id()
        t = txn(
            eur,
            [
                split(acc_a, eur, -100, id=shared),
                split(acc_b, eur, 100, id=shared),
            ],
        )
        with pytest.raises(ValidationError, match="duplicate split id"):
            t.validate()

    def test_multi_currency_transaction_validates(self, eur, usd, acc_a, acc_b):
        """A EUR account paying a USD-denominated transaction."""
        t = txn(
            usd,
            [
                split(acc_a, eur, -9000, currency=usd, value_minor=-10000),
                split(acc_b, usd, 10000),
            ],
        )
        t.validate()
        assert t.is_multi_currency


class TestTransactionQueries:
    @pytest.fixture
    def t(self, eur, acc_a, acc_b):
        return txn(
            eur,
            [
                split(acc_a, eur, -5000, memo="from"),
                split(acc_b, eur, 5000, memo="to"),
            ],
            memo="Rent",
        )

    def test_account_ids(self, t, acc_a, acc_b):
        assert t.account_ids == (acc_a, acc_b)

    def test_split_for(self, t, acc_a):
        assert t.split_for(acc_a).memo == "from"
        assert t.split_for(new_id()) is None

    def test_splits_for(self, t, acc_a):
        assert len(t.splits_for(acc_a)) == 1
        assert t.splits_for(new_id()) == ()

    def test_get_split(self, t):
        assert t.get_split(t.splits[0].id) is t.splits[0]
        with pytest.raises(ValidationError, match="has no split"):
            t.get_split(new_id())

    def test_commodities(self, t, eur):
        assert [c.id for c in t.commodities] == [eur.id]

    def test_not_multi_currency(self, t):
        assert not t.is_multi_currency

    def test_total_debits(self, t):
        assert t.total_debits().minor == 5000

    def test_str(self, t):
        assert str(t) == "2026-01-15 Rent [2 splits, EUR]"

    def test_str_without_memo(self, eur, acc_a, acc_b):
        t = txn(eur, [split(acc_a, eur, -1), split(acc_b, eur, 1)])
        assert "(no memo)" in str(t)

    def test_replace_splits(self, t, acc_a, eur):
        new = t.replace_splits((split(acc_a, eur, -1), split(acc_a, eur, 1)))
        assert len(new.splits) == 2
        assert new.id == t.id

    def test_with_split(self, t):
        target = t.splits[0]
        updated = t.with_split(replace(target, memo="changed"))
        assert updated.splits[0].memo == "changed"
        assert updated.splits[1].memo == "to"

    def test_with_split_requires_a_known_id(self, t, eur, acc_a):
        with pytest.raises(ValidationError, match="has no split"):
            t.with_split(split(acc_a, eur, 1))

    def test_multiple_splits_on_one_account(self, eur, acc_a, acc_b):
        """Allowed: a merge can legitimately produce this."""
        t = txn(
            eur,
            [
                split(acc_a, eur, -3000),
                split(acc_a, eur, -2000),
                split(acc_b, eur, 5000),
            ],
        )
        t.validate()
        assert len(t.splits_for(acc_a)) == 2


class TestTransactionLocking:
    def test_unlocked_by_default(self, eur, acc_a, acc_b):
        t = txn(eur, [split(acc_a, eur, -1), split(acc_b, eur, 1)])
        assert t.lock_state is ReconcileState.NOT_RECONCILED
        assert not t.is_locked
        t.require_mutable()

    def test_cleared_is_not_locked(self, eur, acc_a, acc_b):
        t = txn(
            eur,
            [
                split(acc_a, eur, -1, reconcile_state=ReconcileState.CLEARED),
                split(acc_b, eur, 1),
            ],
        )
        assert not t.is_locked
        t.require_mutable()

    def test_one_reconciled_split_locks_the_whole_transaction(self, eur, acc_a, acc_b):
        t = txn(
            eur,
            [
                split(acc_a, eur, -1, reconcile_state=ReconcileState.RECONCILED),
                split(acc_b, eur, 1),
            ],
        )
        assert t.is_locked
        assert t.lock_state is ReconcileState.RECONCILED
        with pytest.raises(ImmutableError, match="RECONCILED"):
            t.require_mutable()

    def test_the_strongest_state_wins(self, eur, acc_a, acc_b):
        t = txn(
            eur,
            [
                split(acc_a, eur, -1, reconcile_state=ReconcileState.CLEARED),
                split(acc_b, eur, 1, reconcile_state=ReconcileState.FROZEN),
            ],
        )
        assert t.lock_state is ReconcileState.FROZEN

    def test_the_verb_appears_in_the_message(self, eur, acc_a, acc_b):
        t = txn(
            eur,
            [
                split(acc_a, eur, -1, reconcile_state=ReconcileState.FROZEN),
                split(acc_b, eur, 1),
            ],
        )
        with pytest.raises(ImmutableError, match="cannot delete"):
            t.require_mutable("delete")

    def test_an_empty_transaction_has_no_lock_state(self, eur):
        assert txn(eur, []).lock_state is ReconcileState.NOT_RECONCILED
