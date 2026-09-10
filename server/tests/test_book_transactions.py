"""Book: posting, editing, reconciling and deleting transactions."""

from __future__ import annotations

from datetime import date, datetime, timezone
from fractions import Fraction

import pytest

from pymoney.core import (
    AccountType,
    ConflictError,
    ImmutableError,
    LegSpec,
    NotFoundError,
    ReconcileState,
    RoundingMode,
    UnbalancedTransactionError,
    ValidationError,
    new_id,
)
from pymoney.core import (
    commands as c,
)
from tests.conftest import Chart

F = Fraction


class TestPosting:
    def test_a_simple_expense(self, chart: Chart):
        book = chart.book
        transaction = book.post(
            date(2026, 1, 10),
            [LegSpec.of(chart.checking.id, "-42.50"), LegSpec.of(chart.groceries.id)],
            memo="Market",
        )
        assert transaction.date == date(2026, 1, 10)
        assert transaction.memo == "Market"
        assert transaction.currency.code == "EUR"
        assert book.balance(chart.checking.id).minor == -4250
        assert book.balance(chart.groceries.id).minor == 4250

    def test_entered_at_is_stamped_from_the_clock(self, chart: Chart):
        transaction = chart.book.post(
            date(2026, 1, 10),
            [LegSpec.of(chart.checking.id, "-1"), LegSpec.of(chart.groceries.id)],
        )
        assert transaction.entered_at == datetime(2026, 1, 1, 9, tzinfo=timezone.utc)

    def test_entered_at_can_be_supplied(self, chart: Chart):
        when = datetime(2025, 12, 31, 23, 0, tzinfo=timezone.utc)
        transaction = chart.book.post(
            date(2026, 1, 10),
            [LegSpec.of(chart.checking.id, "-1"), LegSpec.of(chart.groceries.id)],
            entered_at=when,
        )
        assert transaction.entered_at == when

    def test_audit_stamps_and_revision(self, chart: Chart):
        book = chart.book
        result = book.execute(
            c.PostTransaction(
                date=date(2026, 1, 10),
                legs=(
                    LegSpec.of(chart.checking.id, "-1"),
                    LegSpec.of(chart.groceries.id),
                ),
            )
        )
        assert result.entity.revision == result.revision
        assert result.entity.created_at == result.entity.updated_at
        assert result.entity.book_id == book.id

    def test_a_multi_split_transaction(self, chart: Chart):
        book = chart.book
        book.post(
            date(2026, 1, 10),
            [
                LegSpec.of(chart.checking.id, "-100.00"),
                LegSpec.of(chart.groceries.id, "70.00"),
                LegSpec.of(chart.fuel.id),
            ],
        )
        assert book.balance(chart.fuel.id).minor == 3000

    def test_currency_defaults_to_the_base_currency(self, chart: Chart):
        transaction = chart.book.post(
            date(2026, 1, 10),
            [LegSpec.of(chart.checking.id, "-1"), LegSpec.of(chart.groceries.id)],
        )
        assert transaction.currency.id == chart.eur.id

    def test_an_explicit_currency(self, chart: Chart):
        book = chart.book
        transaction = book.post(
            date(2026, 1, 10),
            [
                LegSpec.of(chart.usd_account.id, "-20.00"),
                LegSpec.of(chart.groceries.id, "20.00", price=1),
            ],
            currency=chart.usd,
        )
        assert transaction.currency.code == "USD"

    def test_payees_are_validated(self, chart: Chart):
        with pytest.raises(NotFoundError, match="no payee"):
            chart.book.post(
                date(2026, 1, 10),
                [LegSpec.of(chart.checking.id, "-1"), LegSpec.of(chart.groceries.id)],
                payee_id=new_id(),
            )

    def test_split_payees_are_validated(self, chart: Chart):
        with pytest.raises(NotFoundError, match="no payee"):
            chart.book.post(
                date(2026, 1, 10),
                [
                    LegSpec.of(chart.checking.id, "-1", payee_id=new_id()),
                    LegSpec.of(chart.groceries.id),
                ],
            )

    def test_a_payee_is_attached(self, chart: Chart):
        book = chart.book
        payee = book.add_payee("Supermarket")
        transaction = book.post(
            date(2026, 1, 10),
            [LegSpec.of(chart.checking.id, "-1"), LegSpec.of(chart.groceries.id)],
            payee_id=payee.id,
        )
        assert transaction.payee_id == payee.id
        assert book.transactions(payee_id=payee.id) == (transaction,)

    def test_unbalanced_legs_are_refused(self, chart: Chart):
        with pytest.raises(UnbalancedTransactionError):
            chart.book.post(
                date(2026, 1, 10),
                [
                    LegSpec.of(chart.checking.id, "-10"),
                    LegSpec.of(chart.groceries.id, "9"),
                ],
            )

    def test_an_imbalance_account_absorbs_the_difference(self, chart: Chart):
        book = chart.book
        book.post(
            date(2026, 1, 10),
            [
                LegSpec.of(chart.checking.id, "-10"),
                LegSpec.of(chart.groceries.id, "9"),
            ],
            imbalance_account_id=chart.imbalance.id,
        )
        assert book.balance(chart.imbalance.id).minor == 100

    def test_unknown_account(self, chart: Chart):
        with pytest.raises(NotFoundError, match="no account"):
            chart.book.post(
                date(2026, 1, 10),
                [LegSpec.of(new_id(), "-1"), LegSpec.of(chart.groceries.id)],
            )

    def test_imported_ids_are_unique(self, chart: Chart):
        book = chart.book
        legs = [LegSpec.of(chart.checking.id, "-1"), LegSpec.of(chart.groceries.id)]
        book.post(date(2026, 1, 10), legs, imported_id="FITID-1")
        with pytest.raises(ConflictError, match="imported_id"):
            book.post(
                date(2026, 1, 11),
                [
                    LegSpec.of(chart.checking.id, "-2"),
                    LegSpec.of(chart.groceries.id),
                ],
                imported_id="FITID-1",
            )

    def test_explicit_transaction_id_must_be_unique(self, chart: Chart):
        book = chart.book
        legs = (
            LegSpec.of(chart.checking.id, "-1"),
            LegSpec.of(chart.groceries.id),
        )
        first = book.execute(c.PostTransaction(date=date(2026, 1, 10), legs=legs)).entity
        with pytest.raises(ConflictError, match="already exists"):
            book.execute(
                c.PostTransaction(
                    date=date(2026, 1, 10),
                    legs=(
                        LegSpec.of(chart.checking.id, "-2"),
                        LegSpec.of(chart.groceries.id),
                    ),
                    id=first.id,
                )
            )

    def test_a_failed_post_leaves_no_trace(self, chart: Chart):
        book = chart.book
        before = book.revision
        with pytest.raises(UnbalancedTransactionError):
            book.post(
                date(2026, 1, 10),
                [
                    LegSpec.of(chart.checking.id, "-10"),
                    LegSpec.of(chart.groceries.id, "9"),
                ],
            )
        assert book.revision == before
        assert book.transactions() == ()
        assert book.balance(chart.checking.id).is_zero


class TestForeignCommodityPosting:
    """A leg in a commodity other than the transaction currency is priced the
    way the register forms are meant to drive it: a stated value *and* quantity
    define a fresh 'transaction' rate, a value on its own borrows the book's
    price history, and a value with nowhere to get a rate from is refused."""

    def test_a_value_and_a_quantity_record_a_transaction_price(self, chart: Chart):
        book = chart.book
        transaction = book.post(
            date(2026, 2, 1),
            [
                LegSpec.of(chart.checking.id, "-90.00"),
                LegSpec.of(chart.usd_account.id, "90.00", quantity="100.00"),
            ],
        )
        usd_leg = transaction.split_for(chart.usd_account.id)
        assert usd_leg.quantity.minor == 10000
        assert usd_leg.price == F(9, 10)

        history = book.prices.history(chart.usd, chart.eur)
        assert len(history) == 1
        assert history[0].rate == F(9, 10)
        assert history[0].effective_date == date(2026, 2, 1)
        assert history[0].source == "transaction"

    def test_a_stated_price_is_left_as_the_users_intent(self, chart: Chart):
        """An explicit price is the user's figure, not a derived one: it is
        kept verbatim and does not spawn a 'transaction' price."""
        book = chart.book
        transaction = book.post(
            date(2026, 2, 1),
            [
                LegSpec.of(chart.checking.id, "-90.00"),
                LegSpec.of(chart.usd_account.id, "90.00", price="9/10"),
            ],
        )
        assert transaction.split_for(chart.usd_account.id).price == F(9, 10)
        assert book.prices.history(chart.usd, chart.eur) == ()

    def test_a_zero_quantity_leg_keeps_its_own_commodity(self, chart: Chart):
        """A stock split moves shares at no cost; there is no rate to record."""
        book = chart.book
        book.post(
            date(2026, 2, 1),
            [LegSpec.of(chart.opening_usd.id, "1000.00"), LegSpec.of(chart.brokerage.id)],
            currency=chart.usd,
        )
        transaction = book.post(
            date(2026, 2, 2),
            [
                LegSpec.of(chart.aapl_account.id, "0", quantity="10"),
                LegSpec.of(chart.opening_usd.id, "0"),
            ],
            currency=chart.usd,
        )
        assert transaction.split_for(chart.aapl_account.id).quantity.minor == 100000
        assert book.prices.history(chart.aapl, chart.usd) == ()

    def test_a_value_only_leg_uses_the_rate_for_the_day(self, chart: Chart):
        book = chart.book
        book.add_price(chart.usd, chart.eur, "0.80", effective_date=date(2026, 2, 5))
        before = len(book.prices)
        transaction = book.post(
            date(2026, 2, 5),
            [
                LegSpec.of(chart.checking.id, "-80.00"),
                LegSpec.of(chart.usd_account.id, "80.00"),
            ],
        )
        assert transaction.split_for(chart.usd_account.id).quantity.minor == 10000
        assert len(book.prices) == before  # the rate was read, not written

    def test_a_value_only_leg_falls_back_to_the_last_known_rate(
        self, chart: Chart, caplog
    ):
        book = chart.book
        book.add_price(chart.usd, chart.eur, "0.80", effective_date=date(2026, 9, 1))
        book.add_price(chart.usd, chart.eur, "0.95", effective_date=date(2026, 9, 8))
        with caplog.at_level("WARNING", logger="pymoney.core.transactions"):
            transaction = book.post(
                date(2026, 9, 5),
                [
                    LegSpec.of(chart.checking.id, "-80.00"),
                    LegSpec.of(chart.usd_account.id, "80.00"),
                ],
            )
        # 0.80 is effective 1 Sep, 0.95 is 8 Sep; 5 Sep is valued at 0.80.
        assert transaction.split_for(chart.usd_account.id).quantity.minor == 10000
        assert any(
            "2026-09-01" in record.message and "2026-09-05" in record.message
            for record in caplog.records
        )

    def test_a_value_only_leg_with_no_rate_at_all_is_refused(self, chart: Chart):
        book = chart.book
        before = book.revision
        with pytest.raises(ValidationError, match=r"USD.*price on or before 2026-02-05"):
            book.post(
                date(2026, 2, 5),
                [
                    LegSpec.of(chart.checking.id, "-80.00"),
                    LegSpec.of(chart.usd_account.id, "80.00"),
                ],
            )
        assert book.revision == before

    def test_a_rate_dated_after_the_transaction_is_no_help(self, chart: Chart):
        book = chart.book
        book.add_price(chart.usd, chart.eur, "0.80", effective_date=date(2026, 2, 10))
        with pytest.raises(ValidationError, match="price on or before 2026-02-05"):
            book.post(
                date(2026, 2, 5),
                [
                    LegSpec.of(chart.checking.id, "-80.00"),
                    LegSpec.of(chart.usd_account.id, "80.00"),
                ],
            )

    def test_a_foreign_residual_leg_is_valued_from_the_history(self, chart: Chart):
        book = chart.book
        book.add_price(chart.usd, chart.eur, "0.80", effective_date=date(2026, 2, 5))
        transaction = book.post(
            date(2026, 2, 5),
            [
                LegSpec.of(chart.checking.id, "-80.00"),
                LegSpec.of(chart.usd_account.id),
            ],
        )
        assert transaction.split_for(chart.usd_account.id).quantity.minor == 10000

    def test_a_foreign_residual_leg_without_a_rate_is_refused(self, chart: Chart):
        with pytest.raises(ValidationError, match="price on or before"):
            chart.book.post(
                date(2026, 2, 5),
                [
                    LegSpec.of(chart.checking.id, "-80.00"),
                    LegSpec.of(chart.usd_account.id),
                ],
            )

    def test_editing_a_transaction_invents_no_price(self, chart: Chart):
        """The currency is fixed once a transaction exists; re-sending its legs
        (value and quantity, as the form does) must not mint a new rate."""
        book = chart.book
        transaction = book.post(
            date(2026, 2, 1),
            [
                LegSpec.of(chart.checking.id, "-90.00"),
                LegSpec.of(chart.usd_account.id, "90.00", quantity="100.00"),
            ],
        )
        assert len(book.prices) == 1
        book.execute(
            c.UpdateTransaction(
                transaction_id=transaction.id,
                legs=(
                    LegSpec.of(chart.checking.id, "-95.00"),
                    LegSpec.of(chart.usd_account.id, "95.00", quantity="100.00"),
                ),
            )
        )
        assert len(book.prices) == 1  # still just the original post's rate


class TestStockSplit:
    """A share split is the one transaction with a single leg: a change in
    quantity, no value, no counterparty."""

    @pytest.fixture
    def holding(self, chart: Chart) -> Chart:
        book = chart.book
        book.post(
            date(2026, 1, 1),
            [
                LegSpec.of(chart.brokerage.id, "10000.00"),
                LegSpec.of(chart.opening_usd.id, "-10000.00"),
            ],
            currency=chart.usd,
        )
        book.post(
            date(2026, 1, 10),
            [
                LegSpec.of(chart.aapl_account.id, quantity="10", price="150.00"),
                LegSpec.of(chart.brokerage.id),
            ],
            currency=chart.usd,
        )
        return chart

    def test_a_forward_split_multiplies_the_shares(self, holding: Chart):
        book = holding.book
        split = book.record_stock_split(
            holding.aapl_account.id, date(2026, 2, 1), 3, memo="3-for-1"
        )
        assert split.is_stock_split
        (leg,) = split.splits
        assert leg.account_id == holding.aapl_account.id
        assert leg.quantity.minor == 200000  # +20 shares: 10 -> 30
        assert leg.value.minor == 0
        assert leg.action == "SPLIT"
        assert book.balance(holding.aapl_account.id).minor == 300000
        # The cost basis does not move; a split changes no money.
        assert book.value_balance(holding.aapl_account.id).minor == 150000

    def test_a_reverse_split_shrinks_the_shares(self, holding: Chart):
        book = holding.book
        split = book.record_stock_split(
            holding.aapl_account.id, date(2026, 2, 1), F(1, 5)
        )
        assert split.splits[0].quantity.minor == -80000  # 10 -> 2 shares
        assert book.balance(holding.aapl_account.id).minor == 20000

    def test_it_adjusts_the_price_so_valuation_holds(self, holding: Chart):
        book = holding.book
        book.add_price(
            holding.aapl, holding.usd, "180.00", effective_date=date(2026, 1, 20)
        )
        book.record_stock_split(holding.aapl_account.id, date(2026, 2, 1), 4)
        history = book.prices.history(holding.aapl, holding.usd)
        adjusted = history[-1]
        assert adjusted.effective_date == date(2026, 2, 1)
        assert adjusted.rate == F(45)  # 180 / 4
        assert adjusted.source == "split"

    def test_it_leaves_the_price_alone_when_there_is_none(self, holding: Chart):
        book = holding.book
        before = len(book.prices)
        book.record_stock_split(holding.aapl_account.id, date(2026, 2, 1), 2)
        assert len(book.prices) == before

    def test_a_quote_after_the_split_is_not_adjusted(self, holding: Chart):
        """A price dated after the split is already post-split; only an
        earlier one needs the synthetic adjustment."""
        book = holding.book
        book.add_price(
            holding.aapl, holding.usd, "40.00", effective_date=date(2026, 3, 1)
        )
        book.record_stock_split(holding.aapl_account.id, date(2026, 2, 1), 2)
        assert [p.source for p in book.prices.history(holding.aapl, holding.usd)] == [
            "user"
        ]

    def test_a_one_for_one_split_is_refused(self, holding: Chart):
        with pytest.raises(ValidationError, match="change nothing"):
            holding.book.record_stock_split(holding.aapl_account.id, date(2026, 2, 1), 1)

    def test_a_non_positive_ratio_is_refused(self, holding: Chart):
        with pytest.raises(ValidationError, match="must be positive"):
            holding.book.record_stock_split(holding.aapl_account.id, date(2026, 2, 1), 0)

    def test_splitting_an_empty_holding_is_refused(self, chart: Chart):
        with pytest.raises(ValidationError, match="nothing to split"):
            chart.book.record_stock_split(chart.aapl_account.id, date(2026, 2, 1), 2)

    def test_a_split_that_rounds_to_no_change_is_refused(self, chart: Chart):
        book = chart.book
        # JPY has no minor unit. One yen, scaled by 5/4, rounds back to one.
        jpy_cash = book.add_account(
            "Yen Jar", AccountType.CASH, parent=chart.assets, commodity=chart.jpy
        )
        book.post(
            date(2026, 1, 2),
            [
                LegSpec.of(jpy_cash.id, quantity="1", price="1"),
                LegSpec.of(chart.opening.id),
            ],
        )
        with pytest.raises(ValidationError, match="no change"):
            book.record_stock_split(jpy_cash.id, date(2026, 2, 1), F(5, 4))

    def test_a_split_can_be_deleted(self, holding: Chart):
        book = holding.book
        split = book.record_stock_split(holding.aapl_account.id, date(2026, 2, 1), 2)
        assert book.balance(holding.aapl_account.id).minor == 200000
        book.execute(c.DeleteTransaction(transaction_id=split.id))
        assert book.balance(holding.aapl_account.id).minor == 100000
        with pytest.raises(NotFoundError):
            book.transaction(split.id)

    def test_the_split_shows_up_in_the_register(self, holding: Chart):
        book = holding.book
        book.record_stock_split(holding.aapl_account.id, date(2026, 2, 1), 2)
        register = book.register(holding.aapl_account.id)
        assert [r.quantity.minor for r in register] == [100000, 100000]
        assert register[-1].split.action == "SPLIT"

    def test_a_placeholder_account_cannot_be_split(self, chart: Chart):
        with pytest.raises(ValidationError, match="placeholder"):
            chart.book.record_stock_split(chart.assets.id, date(2026, 2, 1), 2)

    def test_an_explicit_transaction_id_is_honoured(self, holding: Chart):
        book = holding.book
        chosen = new_id()
        split = book.record_stock_split(
            holding.aapl_account.id, date(2026, 2, 1), 2, transaction_id=chosen
        )
        assert split.id == chosen
        # Replaying the same id (an offline client re-sending) is a conflict,
        # not a second split.
        with pytest.raises(ConflictError, match="already exists"):
            book.record_stock_split(
                holding.aapl_account.id, date(2026, 3, 1), 2, transaction_id=chosen
            )

    def test_re_recording_a_split_does_not_compound_the_price(self, holding: Chart):
        book = holding.book
        book.add_price(
            holding.aapl, holding.usd, "100.00", effective_date=date(2026, 1, 15)
        )
        book.record_stock_split(holding.aapl_account.id, date(2026, 2, 1), 2)
        first = book.prices.history(holding.aapl, holding.usd)[-1]
        assert first.rate == F(50)  # 100 / 2
        # A second split on the same day (a correction that forgot to delete
        # the first) must not halve the already-halved rate.
        book.record_stock_split(holding.aapl_account.id, date(2026, 2, 1), 2)
        on_that_day = [
            p
            for p in book.prices.history(holding.aapl, holding.usd)
            if p.effective_date == date(2026, 2, 1)
        ]
        assert {p.rate for p in on_that_day} == {F(50)}


class TestQueries:
    def test_transactions_are_date_ordered(self, funded: Chart):
        dates = [t.date for t in funded.book.transactions()]
        assert dates == sorted(dates)

    def test_filter_by_account(self, funded: Chart):
        book = funded.book
        assert len(book.transactions(account_id=funded.checking.id)) == 3
        assert len(book.transactions(account_id=funded.card.id)) == 1

    def test_filter_by_account_checks_existence(self, funded: Chart):
        with pytest.raises(NotFoundError):
            funded.book.transactions(account_id=new_id())

    def test_filter_by_date_range(self, funded: Chart):
        book = funded.book
        assert len(book.transactions(start=date(2026, 1, 6))) == 2
        assert len(book.transactions(end=date(2026, 1, 5))) == 2
        assert len(book.transactions(start=date(2026, 1, 5), end=date(2026, 1, 12))) == 2

    def test_unknown_transaction(self, funded: Chart):
        with pytest.raises(NotFoundError, match="no transaction"):
            funded.book.transaction(new_id())

    def test_register(self, funded: Chart):
        book = funded.book
        register = book.register(funded.checking.id)
        assert [r.quantity.minor for r in register] == [1000000, -8420, 320000]
        assert register[0].date == date(2026, 1, 1)
        assert register[0].transaction.memo == "Opening balance"
        assert "SplitRef" in repr(register[0])

    def test_register_with_children(self, funded: Chart):
        book = funded.book
        assert len(book.register(funded.expenses.id, include_children=True)) == 2
        assert len(book.register(funded.expenses.id)) == 0

    def test_register_respects_dates(self, funded: Chart):
        book = funded.book
        assert len(book.register(funded.checking.id, end=date(2026, 1, 5))) == 2

    def test_register_row_accessors(self, funded: Chart):
        ref = funded.book.register(funded.checking.id)[0]
        assert ref.value.minor == 1000000
        assert ref.quantity.minor == 1000000
        assert ref.account_id == funded.checking.id
        assert ref.date == date(2026, 1, 1)
        assert ref.reconcile_state is ReconcileState.NOT_RECONCILED
        assert ref.split is ref.transaction.splits[0]

    def test_register_memo_falls_back_to_the_transaction(self, funded: Chart):
        ref = funded.book.register(funded.checking.id)[0]
        assert ref.split.memo == ""
        assert ref.memo == "Opening balance"

    def test_balances(self, funded: Chart):
        book = funded.book
        assert book.balance(funded.checking.id).minor == 1000000 - 8420 + 320000
        assert book.balance(funded.card.id).minor == -6150
        assert book.balance(funded.groceries.id).minor == 8420

    def test_balance_as_of(self, funded: Chart):
        book = funded.book
        assert book.balance(funded.checking.id, as_of=date(2026, 1, 1)).minor == 1000000
        assert book.balance(funded.checking.id, as_of=date(2025, 1, 1)).minor == 0

    def test_subtree_balances(self, funded: Chart):
        book = funded.book
        balances = book.subtree_balances(funded.expenses.id)
        assert balances.of(funded.eur).minor == 8420 + 6150

    def test_subtree_balances_can_be_multi_commodity(self, priced: Chart):
        book = priced.book
        book.post(
            date(2026, 1, 20),
            [
                LegSpec.of(priced.usd_account.id, quantity="100", price=1),
                LegSpec.of(priced.opening.id, price=1),
            ],
            currency=priced.usd,
        )
        balances = book.subtree_balances(priced.assets.id)
        assert {a.commodity.code for a in balances} == {"EUR", "USD"}

    def test_class_balances(self, funded: Chart):
        book = funded.book
        from pymoney.core import AccountClass

        assert book.class_balances(AccountClass.EXPENSE).of(funded.eur).minor == 14570
        assert book.class_balances(AccountClass.INCOME).of(funded.eur).minor == -320000

    def test_value_balance(self, funded: Chart):
        book = funded.book
        assert book.value_balance(funded.groceries.id).minor == 8420

    def test_value_balance_of_an_empty_account(self, funded: Chart):
        assert funded.book.value_balance(funded.savings.id).is_zero


class TestUpdate:
    @pytest.fixture
    def transaction(self, chart: Chart):
        return chart.book.post(
            date(2026, 1, 10),
            [LegSpec.of(chart.checking.id, "-42.50"), LegSpec.of(chart.groceries.id)],
            memo="Market",
        )

    def test_header_fields(self, chart: Chart, transaction):
        book = chart.book
        updated = book.execute(
            c.UpdateTransaction(
                transaction_id=transaction.id,
                memo="Supermarket",
                date=date(2026, 1, 11),
                number="42",
            )
        ).entity
        assert updated.memo == "Supermarket"
        assert updated.date == date(2026, 1, 11)
        assert updated.number == "42"
        assert len(updated.splits) == 2

    def test_legs_are_replaced_wholesale(self, chart: Chart, transaction):
        book = chart.book
        book.execute(
            c.UpdateTransaction(
                transaction_id=transaction.id,
                legs=(
                    LegSpec.of(chart.checking.id, "-50.00"),
                    LegSpec.of(chart.fuel.id),
                ),
            )
        )
        assert book.balance(chart.checking.id).minor == -5000
        assert book.balance(chart.groceries.id).minor == 0
        assert book.balance(chart.fuel.id).minor == 5000

    def test_indexes_follow_the_new_accounts(self, chart: Chart, transaction):
        book = chart.book
        book.execute(
            c.UpdateTransaction(
                transaction_id=transaction.id,
                legs=(
                    LegSpec.of(chart.checking.id, "-50.00"),
                    LegSpec.of(chart.fuel.id),
                ),
            )
        )
        assert book.transactions(account_id=chart.groceries.id) == ()
        assert len(book.transactions(account_id=chart.fuel.id)) == 1

    def test_payee(self, chart: Chart, transaction):
        book = chart.book
        payee = book.add_payee("Market")
        updated = book.execute(
            c.UpdateTransaction(transaction_id=transaction.id, payee_id=payee.id)
        ).entity
        assert updated.payee_id == payee.id

    def test_unknown_payee(self, chart: Chart, transaction):
        with pytest.raises(NotFoundError, match="no payee"):
            chart.book.execute(
                c.UpdateTransaction(transaction_id=transaction.id, payee_id=new_id())
            )

    def test_rounding_mode_override(self, chart: Chart, transaction):
        book = chart.book
        updated = book.execute(
            c.UpdateTransaction(
                transaction_id=transaction.id, rounding=RoundingMode.CEILING
            )
        ).entity
        assert updated.rounding is RoundingMode.CEILING

    def test_currency_change_requires_new_legs(self, chart: Chart, transaction):
        with pytest.raises(ValidationError, match="re-supplying the legs"):
            chart.book.execute(
                c.UpdateTransaction(
                    transaction_id=transaction.id, currency_id=chart.usd.id
                )
            )

    def test_currency_change_with_legs(self, chart: Chart, transaction):
        book = chart.book
        updated = book.execute(
            c.UpdateTransaction(
                transaction_id=transaction.id,
                currency_id=chart.usd.id,
                legs=(
                    LegSpec.of(chart.usd_account.id, "-20.00"),
                    LegSpec.of(chart.groceries.id, "20.00", price=1),
                ),
            )
        ).entity
        assert updated.currency.code == "USD"

    def test_unbalanced_update_is_refused(self, chart: Chart, transaction):
        with pytest.raises(UnbalancedTransactionError):
            chart.book.execute(
                c.UpdateTransaction(
                    transaction_id=transaction.id,
                    legs=(
                        LegSpec.of(chart.checking.id, "-10"),
                        LegSpec.of(chart.groceries.id, "9"),
                    ),
                )
            )

    def test_a_failed_update_changes_nothing(self, chart: Chart, transaction):
        book = chart.book
        before = book.revision
        with pytest.raises(UnbalancedTransactionError):
            book.execute(
                c.UpdateTransaction(
                    transaction_id=transaction.id,
                    legs=(
                        LegSpec.of(chart.checking.id, "-10"),
                        LegSpec.of(chart.groceries.id, "9"),
                    ),
                )
            )
        assert book.revision == before
        assert book.balance(chart.checking.id).minor == -4250
        assert book.check_integrity() == ()

    def test_a_no_op_update_burns_no_revision(self, chart: Chart, transaction):
        book = chart.book
        before = book.revision
        result = book.execute(
            c.UpdateTransaction(transaction_id=transaction.id, memo="Market")
        )
        assert result.revision == before

    def test_update_bumps_the_revision_and_updated_at(self, chart: Chart, transaction):
        book = chart.book
        chart.clock.advance(hours=1)
        updated = book.execute(
            c.UpdateTransaction(transaction_id=transaction.id, memo="x")
        ).entity
        assert updated.revision > transaction.revision
        assert updated.updated_at > transaction.updated_at
        assert updated.created_at == transaction.created_at


class TestDelete:
    def test_delete(self, funded: Chart):
        book = funded.book
        transaction = book.transactions(account_id=funded.card.id)[0]
        book.execute(c.DeleteTransaction(transaction_id=transaction.id))
        with pytest.raises(NotFoundError):
            book.transaction(transaction.id)
        assert book.balance(funded.card.id).is_zero
        assert book.transactions(account_id=funded.card.id) == ()

    def test_delete_emits_a_tombstone(self, funded: Chart):
        """A replica has to learn that the row is gone, not just absent."""
        book = funded.book
        transaction = book.transactions(account_id=funded.card.id)[0]
        result = book.execute(c.DeleteTransaction(transaction_id=transaction.id))
        assert result.changes[0].op.value == "DELETE"
        assert result.changes[0].entity_id == transaction.id
        assert book.changelog.tombstones()[0].entity_id == transaction.id

    def test_delete_unknown(self, funded: Chart):
        with pytest.raises(NotFoundError):
            funded.book.execute(c.DeleteTransaction(transaction_id=new_id()))


class TestReconciliation:
    @pytest.fixture
    def transaction(self, funded: Chart):
        return funded.book.transactions(account_id=funded.card.id)[0]

    def test_set_a_split_to_cleared(self, funded: Chart, transaction):
        book = funded.book
        split = transaction.split_for(funded.card.id)
        updated = book.execute(
            c.SetSplitReconcileState(
                transaction_id=transaction.id,
                split_id=split.id,
                state=ReconcileState.CLEARED,
                on=date(2026, 1, 31),
            )
        ).entity
        assert updated.get_split(split.id).reconcile_state is ReconcileState.CLEARED
        assert updated.get_split(split.id).reconcile_date == date(2026, 1, 31)
        assert not updated.is_locked

    def test_reconciled_transactions_cannot_be_edited(self, funded: Chart, transaction):
        """Decision #11: reconciled data is immutable."""
        book = funded.book
        split = transaction.split_for(funded.card.id)
        book.execute(
            c.SetSplitReconcileState(
                transaction_id=transaction.id,
                split_id=split.id,
                state=ReconcileState.RECONCILED,
            )
        )
        with pytest.raises(ImmutableError, match="cannot modify"):
            book.execute(c.UpdateTransaction(transaction_id=transaction.id, memo="nope"))

    def test_reconciled_transactions_cannot_be_deleted(self, funded: Chart, transaction):
        book = funded.book
        split = transaction.split_for(funded.card.id)
        book.execute(
            c.SetSplitReconcileState(
                transaction_id=transaction.id,
                split_id=split.id,
                state=ReconcileState.RECONCILED,
            )
        )
        with pytest.raises(ImmutableError, match="cannot delete"):
            book.execute(c.DeleteTransaction(transaction_id=transaction.id))

    def test_unreconciling_unlocks_again(self, funded: Chart, transaction):
        book = funded.book
        split = transaction.split_for(funded.card.id)
        book.execute(
            c.SetSplitReconcileState(
                transaction_id=transaction.id,
                split_id=split.id,
                state=ReconcileState.RECONCILED,
            )
        )
        book.execute(
            c.SetSplitReconcileState(
                transaction_id=transaction.id,
                split_id=split.id,
                state=ReconcileState.NOT_RECONCILED,
            )
        )
        assert (
            book.execute(
                c.UpdateTransaction(transaction_id=transaction.id, memo="ok")
            ).entity.memo
            == "ok"
        )

    def test_frozen_splits_can_never_be_unlocked(self, funded: Chart, transaction):
        book = funded.book
        split = transaction.split_for(funded.card.id)
        book.execute(
            c.SetSplitReconcileState(
                transaction_id=transaction.id,
                split_id=split.id,
                state=ReconcileState.FROZEN,
            )
        )
        with pytest.raises(ImmutableError, match="frozen"):
            book.execute(
                c.SetSplitReconcileState(
                    transaction_id=transaction.id,
                    split_id=split.id,
                    state=ReconcileState.CLEARED,
                )
            )

    def test_setting_the_same_state_is_a_no_op(self, funded: Chart, transaction):
        book = funded.book
        split = transaction.split_for(funded.card.id)
        before = book.revision
        result = book.execute(
            c.SetSplitReconcileState(
                transaction_id=transaction.id,
                split_id=split.id,
                state=ReconcileState.NOT_RECONCILED,
            )
        )
        assert result.revision == before

    def test_unknown_split(self, funded: Chart, transaction):
        with pytest.raises(ValidationError, match="has no split"):
            funded.book.execute(
                c.SetSplitReconcileState(
                    transaction_id=transaction.id,
                    split_id=new_id(),
                    state=ReconcileState.CLEARED,
                )
            )

    def test_the_change_note_records_the_transition(self, funded: Chart, transaction):
        book = funded.book
        split = transaction.split_for(funded.card.id)
        result = book.execute(
            c.SetSplitReconcileState(
                transaction_id=transaction.id,
                split_id=split.id,
                state=ReconcileState.CLEARED,
            )
        )
        assert "CLEARED" in result.changes[0].note


class TestBatchReconcile:
    def test_reconcile_several_splits_at_once(self, funded: Chart):
        """One statement, one revision."""
        book = funded.book
        refs = book.register(funded.checking.id)
        split_ids = tuple(r.split.id for r in refs)
        before = book.revision
        result = book.execute(
            c.ReconcileAccount(
                account_id=funded.checking.id,
                split_ids=split_ids,
                state=ReconcileState.RECONCILED,
                statement_date=date(2026, 1, 31),
            )
        )
        assert result.revision == before + 1
        assert len(result.changes) == 3
        for ref in book.register(funded.checking.id):
            assert ref.split.reconcile_state is ReconcileState.RECONCILED
            assert ref.split.reconcile_date == date(2026, 1, 31)

    def test_only_the_named_splits_are_touched(self, funded: Chart):
        book = funded.book
        refs = book.register(funded.checking.id)
        book.execute(
            c.ReconcileAccount(
                account_id=funded.checking.id,
                split_ids=(refs[0].split.id,),
            )
        )
        states = [r.split.reconcile_state for r in book.register(funded.checking.id)]
        assert states.count(ReconcileState.RECONCILED) == 1

    def test_the_other_side_of_the_transaction_is_untouched(self, funded: Chart):
        book = funded.book
        refs = book.register(funded.checking.id)
        book.execute(
            c.ReconcileAccount(
                account_id=funded.checking.id, split_ids=(refs[1].split.id,)
            )
        )
        grocery_split = book.register(funded.groceries.id)[0].split
        assert grocery_split.reconcile_state is ReconcileState.NOT_RECONCILED

    def test_splits_must_belong_to_the_account(self, funded: Chart):
        book = funded.book
        other = book.register(funded.groceries.id)[0].split
        with pytest.raises(NotFoundError, match="not in 'Assets:Checking'"):
            book.execute(
                c.ReconcileAccount(account_id=funded.checking.id, split_ids=(other.id,))
            )

    def test_a_failed_batch_rolls_everything_back(self, funded: Chart):
        book = funded.book
        refs = book.register(funded.checking.id)
        before = book.revision
        with pytest.raises(NotFoundError):
            book.execute(
                c.ReconcileAccount(
                    account_id=funded.checking.id,
                    split_ids=(refs[0].split.id, new_id()),
                )
            )
        assert book.revision == before
        assert all(
            r.split.reconcile_state is ReconcileState.NOT_RECONCILED
            for r in book.register(funded.checking.id)
        )

    def test_an_empty_batch_is_a_no_op(self, funded: Chart):
        book = funded.book
        before = book.revision
        result = book.execute(
            c.ReconcileAccount(account_id=funded.checking.id, split_ids=())
        )
        assert result.revision == before

    def test_reconciling_to_the_current_state_is_a_no_op(self, funded: Chart):
        book = funded.book
        refs = book.register(funded.checking.id)
        book.execute(
            c.ReconcileAccount(
                account_id=funded.checking.id,
                split_ids=(refs[0].split.id,),
            )
        )
        before = book.revision
        result = book.execute(
            c.ReconcileAccount(
                account_id=funded.checking.id,
                split_ids=(refs[0].split.id,),
            )
        )
        assert result.revision == before

    def test_frozen_splits_refuse_a_downgrade(self, funded: Chart):
        book = funded.book
        refs = book.register(funded.checking.id)
        book.execute(
            c.ReconcileAccount(
                account_id=funded.checking.id,
                split_ids=(refs[0].split.id,),
                state=ReconcileState.FROZEN,
            )
        )
        with pytest.raises(ImmutableError, match="frozen"):
            book.execute(
                c.ReconcileAccount(
                    account_id=funded.checking.id,
                    split_ids=(refs[0].split.id,),
                    state=ReconcileState.CLEARED,
                )
            )


class TestPrices:
    def test_add(self, chart: Chart):
        book = chart.book
        price = book.add_price(
            chart.usd, chart.eur, "0.92", effective_date=date(2026, 1, 31)
        )
        assert price.rate == F(92, 100)
        assert price.as_of == datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
        assert book.prices.get(price.id) is not None

    def test_as_of_can_be_supplied(self, chart: Chart):
        when = datetime(2026, 1, 31, 17, 30, tzinfo=timezone.utc)
        price = chart.book.add_price(
            chart.usd, chart.eur, "0.92", effective_date=date(2026, 1, 31), as_of=when
        )
        assert price.as_of == when

    def test_accepts_ids(self, chart: Chart):
        price = chart.book.add_price(
            chart.usd.id, chart.eur.id, "0.92", effective_date=date(2026, 1, 31)
        )
        assert price.base.id == chart.usd.id

    def test_unknown_commodity(self, chart: Chart):
        with pytest.raises(NotFoundError, match="no commodity"):
            chart.book.add_price(
                new_id(), chart.eur, "1", effective_date=date(2026, 1, 31)
            )

    def test_invalid_rate(self, chart: Chart):
        with pytest.raises(ValidationError, match="positive"):
            chart.book.add_price(
                chart.usd, chart.eur, "-1", effective_date=date(2026, 1, 31)
            )

    def test_replacing_a_price_by_id_is_an_update(self, chart: Chart):
        book = chart.book
        first = book.add_price(
            chart.usd, chart.eur, "0.92", effective_date=date(2026, 1, 31)
        )
        result = book.execute(
            c.AddPrice(
                base_id=chart.usd.id,
                quote_id=chart.eur.id,
                rate="0.93",
                effective_date=date(2026, 1, 31),
                id=first.id,
            )
        )
        assert result.changes[0].op.value == "UPDATE"
        assert len(book.prices) == 1

    def test_delete(self, chart: Chart):
        book = chart.book
        price = book.add_price(
            chart.usd, chart.eur, "0.92", effective_date=date(2026, 1, 31)
        )
        book.execute(c.DeletePrice(price_id=price.id))
        assert book.prices.get(price.id) is None
        assert len(book.prices) == 0

    def test_delete_unknown(self, chart: Chart):
        with pytest.raises(NotFoundError, match="no price"):
            chart.book.execute(c.DeletePrice(price_id=new_id()))

    def test_prices_are_rolled_back_on_failure(self, chart: Chart):
        """Price mutations take part in command atomicity like any other row."""
        book = chart.book
        before = len(book.prices)
        with pytest.raises(NotFoundError):
            book.execute(
                c.AddPrice(
                    base_id=chart.usd.id,
                    quote_id=new_id(),
                    rate="1",
                    effective_date=date(2026, 1, 31),
                )
            )
        assert len(book.prices) == before


class TestUpdateSplitPayees:
    @pytest.fixture
    def transaction(self, chart: Chart):
        return chart.book.post(
            date(2026, 1, 10),
            [LegSpec.of(chart.checking.id, "-42.50"), LegSpec.of(chart.groceries.id)],
        )

    def test_split_payees_are_kept(self, chart: Chart, transaction):
        book = chart.book
        payee = book.add_payee("Market")
        updated = book.execute(
            c.UpdateTransaction(
                transaction_id=transaction.id,
                legs=(
                    LegSpec.of(chart.checking.id, "-42.50"),
                    LegSpec.of(chart.groceries.id, payee_id=payee.id),
                ),
            )
        ).entity
        assert updated.splits[1].payee_id == payee.id

    def test_unknown_split_payees_are_refused(self, chart: Chart, transaction):
        with pytest.raises(NotFoundError, match="no payee"):
            chart.book.execute(
                c.UpdateTransaction(
                    transaction_id=transaction.id,
                    legs=(
                        LegSpec.of(chart.checking.id, "-42.50"),
                        LegSpec.of(chart.groceries.id, payee_id=new_id()),
                    ),
                )
            )


class TestFacadeIdCoercion:
    """The convenience façade accepts entities or ids interchangeably."""

    def test_prices_require_a_real_commodity(self, chart: Chart):
        with pytest.raises(ValidationError, match="id or entity is required"):
            chart.book.add_price(None, chart.eur, "1", effective_date=date(2026, 1, 31))

    def test_entities_and_ids_are_equivalent(self, chart: Chart):
        book = chart.book
        by_entity = book.add_price(
            chart.usd, chart.eur, "0.91", effective_date=date(2026, 1, 30)
        )
        by_id = book.add_price(
            chart.usd.id, chart.eur.id, "0.92", effective_date=date(2026, 1, 31)
        )
        assert by_entity.base.id == by_id.base.id
