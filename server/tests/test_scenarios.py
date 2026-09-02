"""End-to-end scenarios: the workflows a KMyMoney user would recognise.

Each test drives the book only through commands, the way a client would, and
checks the numbers an accountant would check.
"""

from __future__ import annotations

from datetime import date
from fractions import Fraction

import pytest

from pymoney.core import (
    AccountClass,
    AccountType,
    Book,
    Commodity,
    LegSpec,
    PriceSource,
    ReconcileState,
    RoundingMode,
    balance_sheet,
    holdings,
    income_statement,
)
from pymoney.core import (
    commands as c,
)
from tests.conftest import Chart

F = Fraction


class TestHouseholdMonth:
    """A month of ordinary personal finance, checked end to end."""

    def test_a_full_month(self, chart: Chart):
        book = chart.book
        employer = book.add_payee("ACME Corp")
        shop = book.add_payee("Supermarket")

        book.post(
            date(2026, 1, 1),
            [LegSpec.of(chart.checking.id, "2500.00"), LegSpec.of(chart.opening.id)],
            memo="Opening balance",
        )
        book.post(
            date(2026, 1, 25),
            [LegSpec.of(chart.checking.id, "3200.00"), LegSpec.of(chart.salary.id)],
            memo="Salary",
            payee_id=employer.id,
        )
        for day, amount in ((3, "-54.20"), (10, "-61.05"), (17, "-48.30")):
            book.post(
                date(2026, 1, day),
                [
                    LegSpec.of(chart.checking.id, amount),
                    LegSpec.of(chart.groceries.id),
                ],
                payee_id=shop.id,
            )
        book.post(
            date(2026, 1, 14),
            [LegSpec.of(chart.card.id, "-72.40"), LegSpec.of(chart.fuel.id)],
            memo="Petrol",
        )
        # Paying off part of the credit card: a transfer, not an expense.
        book.post(
            date(2026, 1, 28),
            [LegSpec.of(chart.card.id, "50.00"), LegSpec.of(chart.checking.id)],
            memo="Card payment",
        )

        groceries = 5420 + 6105 + 4830
        assert book.balance(chart.groceries.id).minor == groceries
        assert book.balance(chart.card.id).minor == -7240 + 5000
        assert book.balance(chart.checking.id).minor == (
            250000 + 320000 - groceries - 5000
        )

        policy = book.default_policy(date(2026, 1, 31))
        sheet = balance_sheet(book, policy)
        pnl = income_statement(book, policy, start=date(2026, 1, 1))

        assert pnl.income_earned.minor == 320000
        assert pnl.expenses.minor == groceries + 7240
        assert pnl.net.minor == 320000 - groceries - 7240
        assert sheet.net_worth.minor == (
            book.balance(chart.checking.id).minor + book.balance(chart.card.id).minor
        )

        # The fundamental identity: everything nets to zero across all classes.
        total = sum(
            book.class_balances(klass).of(chart.eur).minor for klass in AccountClass
        )
        assert total == 0

    def test_double_entry_holds_after_every_command(self, chart: Chart):
        """Whatever you do, the ledger sums to zero."""
        book = chart.book
        steps = [
            c.PostTransaction(
                date=date(2026, 1, 1),
                legs=(
                    LegSpec.of(chart.checking.id, "1000.00"),
                    LegSpec.of(chart.opening.id),
                ),
            ),
            c.PostTransaction(
                date=date(2026, 1, 2),
                legs=(
                    LegSpec.of(chart.checking.id, "-10.00"),
                    LegSpec.of(chart.groceries.id, F(10, 3)),
                    LegSpec.of(chart.fuel.id, F(10, 3)),
                    LegSpec.of(chart.fees.id, F(10, 3)),
                ),
            ),
        ]
        for step in steps:
            book.execute(step)
            for transaction in book.transactions():
                assert transaction.is_balanced
            assert (
                sum(
                    book.class_balances(klass).of(chart.eur).minor
                    for klass in AccountClass
                )
                == 0
            )


class TestForeignCurrencyPurchase:
    """Buying something in USD from a EUR account."""

    def test_the_eur_side_stays_exact(self, chart: Chart):
        book = chart.book
        # 120.00 USD at 0.9174 EUR/USD = 110.088 EUR -> 110.09 EUR
        book.post(
            date(2026, 1, 12),
            [
                LegSpec.of(chart.groceries.id, quantity="110.088", price=1),
                LegSpec.of(chart.checking.id),
            ],
            memo="Foreign purchase",
        )
        assert book.balance(chart.groceries.id).minor == 11009
        assert book.balance(chart.checking.id).minor == -11009

    def test_a_usd_transaction_paid_from_a_eur_account(self, chart: Chart):
        book = chart.book
        rate = F(9174, 10000)  # EUR per USD
        transaction = book.post(
            date(2026, 1, 12),
            [
                LegSpec.of(chart.usd_account.id, "120.00"),
                LegSpec.of(chart.checking.id, "-120.00", price=1 / rate),
            ],
            currency=chart.usd,
            memo="Buy USD",
        )
        usd_split = transaction.split_for(chart.usd_account.id)
        eur_split = transaction.split_for(chart.checking.id)
        assert usd_split.quantity.minor == 12000
        assert usd_split.value.minor == 12000
        # 120 USD * 0.9174 = 110.088 EUR -> 110.09
        assert eur_split.quantity.minor == -11009
        assert eur_split.value.minor == -12000
        assert transaction.is_balanced
        assert transaction.is_multi_currency
        assert book.balance(chart.usd_account.id).minor == 12000
        assert book.balance(chart.checking.id).minor == -11009

    def test_the_implied_rate_is_recoverable(self, chart: Chart):
        book = chart.book
        transaction = book.post(
            date(2026, 1, 12),
            [
                LegSpec.of(chart.usd_account.id, "120.00"),
                LegSpec.of(chart.checking.id, "-120.00", price=F(10000, 9174)),
            ],
            currency=chart.usd,
        )
        split = transaction.split_for(chart.checking.id)
        assert split.price == F(10000, 9174)
        # the booked rate differs slightly from the intended one, by design
        assert split.implied_price == F(12000, 11009)


class TestInvestment:
    """A share purchase, a dividend and a valuation."""

    @pytest.fixture
    def brokerage(self, chart: Chart):
        book = chart.book
        dividends = book.add_account(
            "Dividends", AccountType.INCOME, parent=chart.income, commodity=chart.usd
        )
        book.post(
            date(2026, 1, 2),
            [
                LegSpec.of(chart.brokerage.id, "10000.00"),
                LegSpec.of(chart.opening_usd.id, "-10000.00"),
            ],
            currency=chart.usd,
            memo="Fund brokerage",
        )
        return dividends

    def test_buy_shares_with_a_fee(self, chart: Chart, brokerage):
        book = chart.book
        transaction = book.post(
            date(2026, 1, 5),
            [
                LegSpec.of(
                    chart.aapl_account.id,
                    quantity="12",
                    price="183.2433",
                    action="BUY",
                ),
                LegSpec.of(chart.fees.id, "4.95", price=1),
                LegSpec.of(chart.brokerage.id),
            ],
            currency=chart.usd,
            memo="Buy 12 AAPL",
        )
        shares = transaction.split_for(chart.aapl_account.id)
        # 12 * 183.2433 = 2198.9196 -> 2198.92
        assert shares.quantity.minor == 120000
        assert shares.value.minor == 219892
        assert transaction.is_balanced
        assert book.balance(chart.brokerage.id).minor == 1000000 - 219892 - 495
        assert book.balance(chart.aapl_account.id).minor == 120000

    def test_a_dividend(self, chart: Chart, brokerage):
        book = chart.book
        book.post(
            date(2026, 2, 10),
            [
                LegSpec.of(chart.brokerage.id, "8.40"),
                LegSpec.of(brokerage.id, "-8.40"),
            ],
            currency=chart.usd,
            memo="AAPL dividend",
        )
        assert book.balance(brokerage.id).minor == -840

    def test_valuation_chains_aapl_to_eur(self, chart: Chart, brokerage):
        book = chart.book
        book.post(
            date(2026, 1, 5),
            [
                LegSpec.of(chart.aapl_account.id, quantity="12", price="183.2433"),
                LegSpec.of(chart.brokerage.id),
            ],
            currency=chart.usd,
        )
        book.add_price(
            chart.aapl,
            chart.usd,
            "190.50",
            effective_date=date(2026, 1, 31),
            source=PriceSource.YAHOO,
        )
        book.add_price(
            chart.usd,
            chart.eur,
            F(9174, 10000),
            effective_date=date(2026, 1, 31),
            source=PriceSource.ECB,
        )
        report = holdings(book, book.default_policy(date(2026, 1, 31)))
        position = report.positions[0]
        assert position.quantity.minor == 120000
        # 12 * 190.50 USD * 0.9174 EUR/USD = 2097.1764 EUR exactly,
        # booked to 2097.18 once -- not rounded at each hop
        exact = F(12) * F(19050, 100) * F(9174, 10000)
        assert exact == F(52429410, 25000)
        assert position.market_value.minor == 209718
        assert position.conversion.apply(position.quantity).value == exact
        assert position.conversion.hops == 2
        assert "AAPL -> USD -> EUR" in position.conversion.describe()

    def test_a_stock_split_is_just_a_price_and_a_quantity(self, chart: Chart, brokerage):
        """Decision #2 pays off: a 4:1 split needs no special entity."""
        book = chart.book
        book.post(
            date(2026, 1, 5),
            [
                LegSpec.of(chart.aapl_account.id, quantity="12", price="100.00"),
                LegSpec.of(chart.brokerage.id),
            ],
            currency=chart.usd,
        )
        # 36 extra shares arrive, carrying no value.
        book.post(
            date(2026, 3, 1),
            [
                LegSpec.of(chart.aapl_account.id, "0", quantity="36"),
                LegSpec.of(chart.brokerage.id, "0"),
            ],
            currency=chart.usd,
            memo="4:1 split",
        )
        assert book.balance(chart.aapl_account.id).minor == 480000
        assert book.value_balance(chart.aapl_account.id).minor == 120000


class TestReconciliation:
    """A statement arrives, gets reconciled, and then the period is closed."""

    def test_the_full_cycle(self, funded: Chart):
        book = funded.book
        refs = book.register(funded.checking.id, end=date(2026, 1, 31))
        split_ids = tuple(r.split.id for r in refs)

        book.execute(
            c.ReconcileAccount(
                account_id=funded.checking.id,
                split_ids=split_ids,
                state=ReconcileState.CLEARED,
            )
        )
        # Still editable while only cleared.
        transaction = refs[1].transaction
        book.execute(c.UpdateTransaction(transaction_id=transaction.id, memo="Fixed"))

        book.execute(
            c.ReconcileAccount(
                account_id=funded.checking.id,
                split_ids=split_ids,
                state=ReconcileState.RECONCILED,
                statement_date=date(2026, 1, 31),
            )
        )
        from pymoney.core import ImmutableError

        with pytest.raises(ImmutableError):
            book.execute(c.UpdateTransaction(transaction_id=transaction.id, memo="Nope"))

        # Closing the period freezes it for good.
        book.execute(
            c.ReconcileAccount(
                account_id=funded.checking.id,
                split_ids=split_ids,
                state=ReconcileState.FROZEN,
            )
        )
        with pytest.raises(ImmutableError, match="frozen"):
            book.execute(
                c.ReconcileAccount(
                    account_id=funded.checking.id,
                    split_ids=split_ids,
                    state=ReconcileState.NOT_RECONCILED,
                )
            )
        assert book.balance(funded.checking.id).minor == 1311580

    def test_the_reconciled_balance_is_auditable(self, funded: Chart):
        book = funded.book
        refs = book.register(funded.checking.id)
        book.execute(
            c.ReconcileAccount(
                account_id=funded.checking.id,
                split_ids=(refs[0].split.id, refs[1].split.id),
                state=ReconcileState.RECONCILED,
                statement_date=date(2026, 1, 10),
            )
        )
        reconciled = sum(
            r.split.quantity.minor
            for r in book.register(funded.checking.id)
            if r.split.reconcile_state >= ReconcileState.RECONCILED
        )
        assert reconciled == 1000000 - 8420


class TestSyncRoundTrip:
    """A minimal read-only replica, built only from the sync surface."""

    def test_a_replica_can_be_rebuilt_from_the_changelog(self, funded: Chart):
        book = funded.book
        replica: dict = {}
        watermark = 0

        def pull() -> int:
            nonlocal watermark
            for change in book.changes_since(watermark):
                if change.is_delete:
                    replica.pop(change.entity_id, None)
                else:
                    replica[change.entity_id] = book.entity_for_change(change)
            watermark = book.revision
            return watermark

        pull()
        assert watermark == book.revision
        transactions = {
            k: v for k, v in replica.items() if type(v).__name__ == "Transaction"
        }
        assert len(transactions) == 4

        # Mutate, then pull only the delta.
        target = book.transactions(account_id=funded.card.id)[0]
        book.execute(c.DeleteTransaction(transaction_id=target.id))
        new_transaction = book.post(
            date(2026, 2, 2),
            [LegSpec.of(funded.checking.id, "-19.99"), LegSpec.of(funded.fuel.id)],
        )
        before = watermark
        pull()
        assert watermark > before
        assert target.id not in replica
        assert replica[new_transaction.id].memo == new_transaction.memo

        # The replica's view of accounts matches the server's.
        accounts = {k for k, v in replica.items() if type(v).__name__ == "Account"}
        assert accounts == set(book.snapshot_ids()["accounts"])

    def test_balances_can_be_recomputed_from_a_replica(self, funded: Chart):
        """A read-only cache is enough to show an account register offline."""
        book = funded.book
        cached = [
            book.entity_for_change(ch)
            for ch in book.changes_since(0)
            if ch.kind.value == "TRANSACTION"
        ]
        total = sum(
            split.quantity.minor
            for transaction in cached
            for split in transaction.splits
            if split.account_id == funded.checking.id
        )
        assert total == book.balance(funded.checking.id).minor


class TestRoundingPolicy:
    def test_per_transaction_override(self, chart: Chart):
        """Decision #4: the default is banker's rounding, overridable per row."""
        book = chart.book
        default = book.post(
            date(2026, 1, 5),
            [
                LegSpec.of(chart.aapl_account.id, "100.00", price="190.50"),
                LegSpec.of(chart.brokerage.id),
            ],
            currency=chart.usd,
        )
        ceiling = book.post(
            date(2026, 1, 6),
            [
                LegSpec.of(chart.aapl_account.id, "100.00", price="190.50"),
                LegSpec.of(chart.brokerage.id),
            ],
            currency=chart.usd,
            rounding=RoundingMode.CEILING,
        )
        assert default.splits[0].quantity.minor == 5249
        assert ceiling.splits[0].quantity.minor == 5250
        assert default.rounding is RoundingMode.HALF_EVEN
        assert ceiling.rounding is RoundingMode.CEILING


class TestZeroDecimalCurrency:
    def test_a_jpy_book(self, clock):
        """No minor unit at all, so every invariant has to hold on integers."""
        jpy = Commodity.currency("JPY", scale=0, symbol="\u00a5")
        book = Book(base_currency=jpy, clock=clock)
        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        rent = book.add_account("Rent", AccountType.EXPENSE)
        equity = book.add_account("Opening", AccountType.EQUITY)

        book.post(
            date(2026, 1, 1),
            [LegSpec.of(cash.id, "500000"), LegSpec.of(equity.id)],
        )
        book.post(
            date(2026, 1, 2),
            [
                LegSpec.of(cash.id, "-1000"),
                LegSpec.of(food.id, F(1000, 3)),
                LegSpec.of(rent.id, F(2000, 3)),
            ],
        )
        assert book.balance(cash.id).minor == 499000
        assert book.balance(food.id).minor + book.balance(rent.id).minor == 1000
        assert str(book.balance(cash.id)) == "499000 \u00a5"
        assert book.check_integrity() == ()


class TestBookIntegrityUnderStress:
    def test_a_long_sequence_of_commands_stays_consistent(self, chart: Chart):
        book = chart.book
        book.post(
            date(2026, 1, 1),
            [LegSpec.of(chart.checking.id, "5000.00"), LegSpec.of(chart.opening.id)],
        )
        for day in range(1, 29):
            book.post(
                date(2026, 1, day),
                [
                    LegSpec.of(chart.checking.id, f"-{day}.{day:02d}"),
                    LegSpec.of(chart.groceries.id),
                ],
                memo=f"day {day}",
            )
        # Restructure underneath all that history.
        food = book.add_account("Food", AccountType.EXPENSE, parent=chart.expenses)
        book.execute(c.MergeAccounts(source_id=chart.groceries.id, target_id=food.id))
        book.execute(c.RenameAccount(account_id=food.id, name="Food & Drink"))
        book.execute(c.MoveAccount(account_id=food.id, new_parent_id=None))

        assert book.check_integrity() == ()
        assert book.path(food.id) == "Food & Drink"
        spent = sum(int(f"{day}{day:02d}") for day in range(1, 29))
        assert book.balance(food.id).minor == spent
        assert book.balance(chart.checking.id).minor == 500000 - spent
        assert book.changelog.head == book.revision
