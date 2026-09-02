"""Reports: valuation, balance sheet, income statement, holdings.

The running theme: a report is a pure function of (book, valuation policy), and
it shows its work.
"""

from __future__ import annotations

from datetime import date
from fractions import Fraction

import pytest

from pymoney.core import (
    AccountClass,
    AccountType,
    Amount,
    Balances,
    LegSpec,
    NoPriceError,
    PriceSource,
    ValidationError,
    ValuationPolicy,
    Valuer,
    account_tree,
    balance_sheet,
    holdings,
    income_statement,
)
from tests.conftest import Chart

F = Fraction
JAN31 = date(2026, 1, 31)


@pytest.fixture
def invested(priced: Chart) -> Chart:
    """Add a USD cash position and an AAPL holding to the priced chart."""
    book = priced.book
    # Fund the USD wallet with 2,000 USD from equity.
    book.post(
        date(2026, 1, 2),
        [
            LegSpec.of(priced.usd_account.id, "2000.00"),
            LegSpec.of(priced.opening_usd.id, "-2000.00"),
        ],
        currency=priced.usd,
    )
    # Fund the brokerage account and buy 10 AAPL at 180 USD plus fees.
    book.post(
        date(2026, 1, 3),
        [
            LegSpec.of(priced.brokerage.id, "5000.00"),
            LegSpec.of(priced.opening_usd.id, "-5000.00"),
        ],
        currency=priced.usd,
    )
    book.post(
        date(2026, 1, 6),
        [
            LegSpec.of(priced.aapl_account.id, quantity="10", price="180.00"),
            LegSpec.of(priced.brokerage.id),
        ],
        currency=priced.usd,
        memo="Buy AAPL",
    )
    return priced


class TestValuer:
    def test_needs_a_target(self, book):
        with pytest.raises(ValidationError, match="needs a target"):
            Valuer(book, ValuationPolicy(as_of=JAN31))

    def test_identity_conversion(self, priced: Chart):
        valuer = Valuer(priced.book, priced.book.default_policy(JAN31))
        conversion = valuer.conversion(priced.eur)
        assert conversion.is_identity
        assert valuer.conversions == ()  # identities are not worth showing

    def test_converts_and_stays_exact(self, priced: Chart):
        valuer = Valuer(priced.book, priced.book.default_policy(JAN31))
        value = valuer.value(Balances([Amount.from_major(priced.usd, "100.00")]))
        assert value.value == F(90)
        assert value.commodity.code == "EUR"

    def test_sums_before_rounding(self, priced: Chart):
        """Three 0.05-cent positions must not each be rounded away."""
        book = priced.book
        book.add_price(priced.jpy, priced.eur, F(1, 150), effective_date=JAN31)
        valuer = Valuer(book, book.default_policy(JAN31))
        balances = Balances([Amount(priced.jpy, 1)])
        # 1 JPY = 1/150 EUR = 0.00667 -> a single position books to 0.01
        assert valuer.value(balances).value == F(1, 150)
        assert valuer.value_amount(balances).minor == 1

    def test_caches_conversions(self, priced: Chart):
        valuer = Valuer(priced.book, priced.book.default_policy(JAN31))
        first = valuer.conversion(priced.usd)
        assert valuer.conversion(priced.usd) is first

    def test_records_what_it_used(self, invested: Chart):
        book = invested.book
        valuer = Valuer(book, book.default_policy(JAN31))
        valuer.value(
            Balances(
                [
                    Amount.from_major(invested.usd, "100"),
                    Amount.from_major(invested.aapl, "10"),
                ]
            )
        )
        described = [c.describe() for c in valuer.conversions]
        assert any("AAPL -> USD -> EUR" in d for d in described)
        assert any(d.startswith("USD -> EUR") for d in described)

    def test_strict_mode_raises_on_a_missing_price(self, funded: Chart):
        book = funded.book
        valuer = Valuer(book, book.default_policy(JAN31))
        with pytest.raises(NoPriceError):
            valuer.conversion(funded.usd)

    def test_lenient_mode_collects_unpriced_commodities(self, funded: Chart):
        book = funded.book
        valuer = Valuer(book, book.default_policy(JAN31), strict=False)
        balances = Balances(
            [Amount.from_major(funded.eur, "10"), Amount.from_major(funded.usd, "99")]
        )
        assert valuer.value(balances).value == F(10)
        assert [c.code for c in valuer.unpriced] == ["USD"]

    def test_lenient_mode_caches_the_failure(self, funded: Chart):
        book = funded.book
        valuer = Valuer(book, book.default_policy(JAN31), strict=False)
        assert valuer.conversion(funded.usd) is None
        assert valuer.conversion(funded.usd) is None
        assert len(valuer.unpriced) == 1


class TestAccountTree:
    def test_rows_are_depth_first(self, funded: Chart):
        book = funded.book
        rows = account_tree(book, book.default_policy(JAN31))
        paths = [r.path for r in rows]
        assert paths.index("Assets") < paths.index("Assets:Checking")
        assert [r.depth for r in rows if r.path == "Assets:Checking"] == [1]

    def test_filter_by_class(self, funded: Chart):
        book = funded.book
        rows = account_tree(
            book, book.default_policy(JAN31), account_class=AccountClass.EXPENSE
        )
        assert {r.account.name for r in rows} == {
            "Expenses",
            "Groceries",
            "Fuel",
            "Bank Fees",
        }

    def test_subtree_totals_roll_up(self, funded: Chart):
        book = funded.book
        rows = {
            r.path: r
            for r in account_tree(
                book, book.default_policy(JAN31), account_class=AccountClass.EXPENSE
            )
        }
        assert rows["Expenses"].own.is_zero
        assert rows["Expenses"].value.minor == 8420 + 6150
        assert rows["Expenses:Groceries"].value.minor == 8420

    def test_leaf_detection(self, funded: Chart):
        book = funded.book
        rows = {r.path: r for r in account_tree(book, book.default_policy(JAN31))}
        assert rows["Assets:Checking"].is_leaf
        assert not rows["Assets"].is_leaf

    def test_display_value_applies_the_report_sign(self, funded: Chart):
        book = funded.book
        rows = {r.path: r for r in account_tree(book, book.default_policy(JAN31))}
        card = rows["Liabilities:Visa"]
        assert card.value.minor == -6150
        assert card.display_value.minor == 6150
        assert card.sign == -1

    def test_own_value_excludes_children(self, funded: Chart):
        book = funded.book
        rows = {r.path: r for r in account_tree(book, book.default_policy(JAN31))}
        assert rows["Expenses"].own_value.is_zero

    def test_closed_accounts_are_hidden_by_default(self, funded: Chart):
        from pymoney.core import commands as c

        book = funded.book
        book.execute(c.SetAccountClosed(account_id=funded.savings.id))
        paths = {r.path for r in account_tree(book, book.default_policy(JAN31))}
        assert "Assets:Savings" not in paths
        paths = {
            r.path
            for r in account_tree(book, book.default_policy(JAN31), include_closed=True)
        }
        assert "Assets:Savings" in paths

    def test_explicit_roots(self, funded: Chart):
        book = funded.book
        rows = account_tree(book, book.default_policy(JAN31), roots=[funded.expenses])
        assert rows[0].path == "Expenses"
        assert all(r.path.startswith("Expenses") for r in rows)

    def test_empty_accounts_can_be_dropped(self, funded: Chart):
        book = funded.book
        rows = account_tree(
            book,
            book.default_policy(JAN31),
            account_class=AccountClass.ASSET,
            include_empty=False,
        )
        assert "Assets:Savings" not in {r.path for r in rows}

    def test_row_str(self, funded: Chart):
        book = funded.book
        rows = {r.path: r for r in account_tree(book, book.default_policy(JAN31))}
        assert str(rows["Assets:Checking"]).strip() == "Checking: 13115.80 \u20ac"


class TestBalanceSheet:
    def test_totals(self, funded: Chart):
        book = funded.book
        sheet = balance_sheet(book, book.default_policy(JAN31))
        assert sheet.assets.minor == 1311580
        assert sheet.liabilities.minor == -6150
        assert sheet.liabilities_owed.minor == 6150
        assert sheet.net_worth.minor == 1311580 - 6150

    def test_equity_makes_it_balance(self, funded: Chart):
        """Opening balances posted to equity keep the identity exact."""
        book = funded.book
        sheet = balance_sheet(book, book.default_policy(JAN31))
        assert sheet.equity.minor == -1000000
        assert not sheet.balances  # income and expenses are not closed out

    def test_as_of_a_past_date(self, funded: Chart):
        book = funded.book
        sheet = balance_sheet(book, book.default_policy(date(2026, 1, 1)))
        assert sheet.assets.minor == 1000000
        assert sheet.liabilities.is_zero

    def test_income_accounts_are_excluded(self, funded: Chart):
        book = funded.book
        sheet = balance_sheet(book, book.default_policy(JAN31))
        assert all(r.account.account_class.is_balance_sheet for r in sheet.rows)

    def test_rows_for(self, funded: Chart):
        book = funded.book
        sheet = balance_sheet(book, book.default_policy(JAN31))
        assert {r.account.name for r in sheet.rows_for(AccountClass.LIABILITY)} == {
            "Liabilities",
            "Visa",
        }

    def test_multi_currency_totals(self, invested: Chart):
        book = invested.book
        sheet = balance_sheet(book, book.default_policy(JAN31))
        # EUR 13,115.80 + USD 2,000 + brokerage USD 3,200 + 10 AAPL @ 190.50
        expected = (
            F(1311580, 100)
            + F(2000) * F(9, 10)
            + F(3200) * F(9, 10)
            + F(10) * F(19050, 100) * F(9, 10)
        )
        assert sheet.assets.rational.value == expected

    def test_conversions_are_reported(self, invested: Chart):
        book = invested.book
        sheet = balance_sheet(book, book.default_policy(JAN31))
        assert {c.base.code for c in sheet.conversions} == {"USD", "AAPL"}
        assert sheet.unpriced == ()

    def test_a_missing_price_is_fatal_by_default(self, funded: Chart):
        """Silently valuing a position at zero would be worse than an error."""
        book = funded.book
        book.post(
            date(2026, 1, 20),
            [
                LegSpec.of(funded.usd_account.id, "100.00"),
                LegSpec.of(funded.opening_usd.id, "-100.00"),
            ],
            currency=funded.usd,
        )
        with pytest.raises(NoPriceError):
            balance_sheet(book, book.default_policy(JAN31))

    def test_lenient_mode_lists_what_it_skipped(self, funded: Chart):
        book = funded.book
        book.post(
            date(2026, 1, 20),
            [
                LegSpec.of(funded.usd_account.id, "100.00"),
                LegSpec.of(funded.opening_usd.id, "-100.00"),
            ],
            currency=funded.usd,
        )
        sheet = balance_sheet(book, book.default_policy(JAN31), strict=False)
        assert [c.code for c in sheet.unpriced] == ["USD"]

    def test_needs_a_valuation_date(self, funded: Chart):
        book = funded.book
        policy = ValuationPolicy(target=funded.eur)
        with pytest.raises(ValidationError, match=r"needs policy\.as_of"):
            balance_sheet(book, policy)

    def test_str(self, funded: Chart):
        book = funded.book
        sheet = balance_sheet(book, book.default_policy(JAN31))
        assert "Balance sheet 2026-01-31 (EUR)" in str(sheet)
        assert "net worth" in str(sheet)

    def test_reproducibility_under_a_knowledge_cutoff(self, priced: Chart):
        """Decision #12: the same policy always yields the same number."""
        book = priced.book
        book.post(
            date(2026, 1, 20),
            [
                LegSpec.of(priced.usd_account.id, "100.00"),
                LegSpec.of(priced.opening_usd.id, "-100.00"),
            ],
            currency=priced.usd,
        )
        cutoff = book.clock.now()
        before = balance_sheet(
            book, book.default_policy(JAN31, knowledge_time=cutoff)
        ).assets
        # A corrected rate arrives later, for the same effective date.
        book.clock.advance(days=5)
        book.add_price(
            priced.usd,
            priced.eur,
            "0.95",
            effective_date=JAN31,
            source=PriceSource.ECB,
        )
        after = balance_sheet(
            book, book.default_policy(JAN31, knowledge_time=cutoff)
        ).assets
        latest = balance_sheet(book, book.default_policy(JAN31)).assets
        assert after == before
        assert latest != before


class TestIncomeStatement:
    def test_totals(self, funded: Chart):
        book = funded.book
        report = income_statement(
            book, book.default_policy(JAN31), start=date(2026, 1, 1)
        )
        assert report.income.minor == -320000
        assert report.income_earned.minor == 320000
        assert report.expenses.minor == 14570
        assert report.net.minor == 320000 - 14570

    def test_period_filtering(self, funded: Chart):
        book = funded.book
        report = income_statement(
            book, book.default_policy(date(2026, 1, 10)), start=date(2026, 1, 6)
        )
        assert report.income.is_zero
        assert report.expenses.is_zero

    def test_only_the_period_counts(self, funded: Chart):
        book = funded.book
        report = income_statement(
            book, book.default_policy(JAN31), start=date(2026, 1, 6)
        )
        assert report.expenses.minor == 6150  # the petrol, not the groceries

    def test_balance_sheet_accounts_are_excluded(self, funded: Chart):
        book = funded.book
        report = income_statement(
            book, book.default_policy(JAN31), start=date(2026, 1, 1)
        )
        assert all(not r.account.account_class.is_balance_sheet for r in report.rows)

    def test_rows_roll_up(self, funded: Chart):
        book = funded.book
        report = income_statement(
            book, book.default_policy(JAN31), start=date(2026, 1, 1)
        )
        rows = {r.path: r for r in report.rows}
        assert rows["Expenses"].value.minor == 14570
        assert rows["Expenses"].own.is_zero
        assert rows["Expenses:Fuel"].value.minor == 6150

    def test_rows_for(self, funded: Chart):
        book = funded.book
        report = income_statement(
            book, book.default_policy(JAN31), start=date(2026, 1, 1)
        )
        assert {r.account.name for r in report.rows_for(AccountClass.INCOME)} == {
            "Income",
            "Salary",
        }

    def test_savings_rate(self, funded: Chart):
        book = funded.book
        report = income_statement(
            book, book.default_policy(JAN31), start=date(2026, 1, 1)
        )
        assert report.savings_rate == F(320000 - 14570, 320000)

    def test_savings_rate_without_income(self, funded: Chart):
        book = funded.book
        report = income_statement(
            book, book.default_policy(date(2026, 1, 12)), start=date(2026, 1, 12)
        )
        assert report.savings_rate is None

    def test_closed_accounts_are_hidden(self, funded: Chart):
        from pymoney.core import commands as c

        book = funded.book
        book.execute(c.SetAccountClosed(account_id=funded.fees.id))
        report = income_statement(
            book, book.default_policy(JAN31), start=date(2026, 1, 1)
        )
        assert "Expenses:Bank Fees" not in {r.path for r in report.rows}
        report = income_statement(
            book,
            book.default_policy(JAN31),
            start=date(2026, 1, 1),
            include_closed=True,
        )
        assert "Expenses:Bank Fees" in {r.path for r in report.rows}

    def test_needs_an_end_date(self, funded: Chart):
        book = funded.book
        with pytest.raises(ValidationError, match=r"policy\.as_of as its end"):
            income_statement(
                book, ValuationPolicy(target=funded.eur), start=date(2026, 1, 1)
            )

    def test_start_must_precede_end(self, funded: Chart):
        book = funded.book
        with pytest.raises(ValidationError, match="is after end"):
            income_statement(
                book, book.default_policy(date(2026, 1, 1)), start=date(2026, 2, 1)
            )

    def test_foreign_expenses_are_translated_at_one_rate(self, priced: Chart):
        """A period report still values at a single point in time."""
        book = priced.book
        foreign = book.add_account(
            "Travel",
            AccountType.EXPENSE,
            parent=priced.expenses,
            commodity=priced.usd,
        )
        book.post(
            date(2026, 1, 15),
            [
                LegSpec.of(foreign.id, "300.00"),
                LegSpec.of(priced.opening_usd.id, "-300.00"),
            ],
            currency=priced.usd,
        )
        report = income_statement(
            book, book.default_policy(JAN31), start=date(2026, 1, 1)
        )
        rows = {r.path: r for r in report.rows}
        assert rows["Expenses:Travel"].value.minor == 27000  # 300 USD * 0.9

    def test_str(self, funded: Chart):
        book = funded.book
        report = income_statement(
            book, book.default_policy(JAN31), start=date(2026, 1, 1)
        )
        assert "Income statement 2026-01-01..2026-01-31 (EUR)" in str(report)


class TestHoldings:
    def test_positions(self, invested: Chart):
        book = invested.book
        report = holdings(book, book.default_policy(JAN31))
        assert len(report.positions) == 1
        position = report.positions[0]
        assert position.path == "Assets:Brokerage:AAPL"
        assert position.quantity.minor == 100000  # 10 shares at 4 decimals
        assert position.commodity.code == "AAPL"

    def test_market_value_uses_the_price_chain(self, invested: Chart):
        book = invested.book
        report = holdings(book, book.default_policy(JAN31))
        position = report.positions[0]
        # 10 * 190.50 USD * 0.9 EUR/USD
        assert position.market_value.rational.value == F(10) * F(19050, 100) * F(9, 10)
        assert position.unit_price == F(19050, 100) * F(9, 10)

    def test_cost_comes_from_the_value_column(self, invested: Chart):
        book = invested.book
        report = holdings(book, book.default_policy(JAN31))
        position = report.positions[0]
        assert position.cost.commodity.code == "USD"
        assert position.cost.minor == 180000  # 10 * 180.00 USD

    def test_unrealised_gain(self, invested: Chart):
        book = invested.book
        # Report in USD so cost and market value share a commodity.
        policy = book.default_policy(JAN31).for_target(invested.usd)
        report = holdings(book, policy)
        position = report.positions[0]
        assert position.market_value.minor == 190500
        assert position.unrealised_gain.minor == 10500

    def test_totals(self, invested: Chart):
        book = invested.book
        policy = book.default_policy(JAN31).for_target(invested.usd)
        report = holdings(book, policy)
        assert report.total_value.minor == 190500
        assert report.total_cost.minor == 180000

    def test_empty_positions_are_hidden(self, invested: Chart):
        book = invested.book
        book.post(
            date(2026, 1, 20),
            [
                LegSpec.of(invested.aapl_account.id, quantity="-10", price="190.00"),
                LegSpec.of(invested.brokerage.id),
            ],
            currency=invested.usd,
        )
        assert holdings(book, book.default_policy(JAN31)).positions == ()
        report = holdings(book, book.default_policy(JAN31), include_empty=True)
        assert len(report.positions) == 1
        assert report.positions[0].quantity.is_zero

    def test_a_book_without_securities(self, funded: Chart):
        book = funded.book
        report = holdings(book, book.default_policy(JAN31))
        assert report.positions == ()
        assert report.total_value.is_zero
        assert report.total_cost.is_zero

    def test_needs_a_valuation_date(self, invested: Chart):
        book = invested.book
        with pytest.raises(ValidationError, match=r"needs policy\.as_of"):
            holdings(book, ValuationPolicy(target=invested.eur))

    def test_str(self, invested: Chart):
        book = invested.book
        report = holdings(book, book.default_policy(JAN31))
        assert "Holdings 2026-01-31 (EUR): 1 positions" in str(report)
        assert "AAPL" in str(report.positions[0])

    def test_as_of_before_the_purchase(self, invested: Chart):
        book = invested.book
        report = holdings(book, book.default_policy(date(2026, 1, 5)))
        assert report.positions == ()


class TestHoldingsCostInAnotherCurrency:
    def test_total_cost_only_sums_matching_commodities(self, invested: Chart):
        """Cost is recorded in the trade currency; mixing it with FX would lie."""
        book = invested.book
        report = holdings(book, book.default_policy(JAN31))  # target EUR
        assert report.positions[0].cost.commodity.code == "USD"
        assert report.total_cost.commodity.code == "EUR"
        assert report.total_cost.is_zero
        assert not report.total_value.is_zero


class TestPeriods:
    def test_months_are_clipped_to_the_window(self):
        from pymoney.core import periods_between

        months = periods_between(date(2026, 1, 15), date(2026, 3, 10))
        assert [(p.start, p.end, p.label) for p in months] == [
            (date(2026, 1, 15), date(2026, 1, 31), "2026-01"),
            (date(2026, 2, 1), date(2026, 2, 28), "2026-02"),
            (date(2026, 3, 1), date(2026, 3, 10), "2026-03"),
        ]

    def test_quarters_and_years(self):
        from pymoney.core import periods_between

        quarters = periods_between(date(2025, 11, 1), date(2026, 4, 30), "quarter")
        assert [p.label for p in quarters] == ["2025-Q4", "2026-Q1", "2026-Q2"]
        assert quarters[1].start == date(2026, 1, 1) and quarters[1].end == date(
            2026, 3, 31
        )
        years = periods_between(date(2024, 6, 1), date(2026, 1, 1), "year")
        assert [p.label for p in years] == ["2024", "2025", "2026"]
        assert years[0].start == date(2024, 6, 1) and years[-1].end == date(2026, 1, 1)

    def test_a_backwards_window_is_refused(self):
        from pymoney.core import periods_between

        with pytest.raises(ValidationError):
            periods_between(date(2026, 2, 1), date(2026, 1, 1))


class TestNetWorthSeries:
    def test_one_point_per_period_end(self, funded: Chart):
        from pymoney.core import net_worth_series

        book = funded.book
        # Something in February, so the line moves.
        book.post(
            date(2026, 2, 10),
            [LegSpec.of(funded.checking.id, "-500.00"), LegSpec.of(funded.fuel.id)],
        )
        series = net_worth_series(
            book, book.default_policy(date(2026, 3, 15)), start=date(2026, 1, 1)
        )
        assert [p.label for p in series.points] == ["2026-01", "2026-02", "2026-03"]
        assert [p.as_of for p in series.points] == [
            date(2026, 1, 31),
            date(2026, 2, 28),
            date(2026, 3, 15),
        ]
        january, february, march = series.points
        # Checking 10000 - 84.20 + 3200 = 13115.80; card owes 61.50.
        assert january.assets.minor == 1311580
        assert january.liabilities.minor == -6150
        assert january.net_worth.minor == 1311580 - 6150
        assert february.net_worth.minor == january.net_worth.minor - 50000
        assert march.net_worth == february.net_worth
        assert series.target is book.base_currency

    def test_each_point_is_valued_at_its_own_date(self, invested: Chart):
        from pymoney.core import net_worth_series

        book = invested.book
        # A better AAPL price arrives in February.
        book.add_price(
            invested.aapl, invested.usd, "200", effective_date=date(2026, 2, 15)
        )
        series = net_worth_series(
            book, book.default_policy(date(2026, 2, 28)), start=date(2026, 1, 1)
        )
        january, february = series.points
        assert february.net_worth > january.net_worth
        # Both rates show up in the provenance.
        rates = {(c.base.code, c.quote.code) for c in series.conversions}
        assert ("AAPL", "EUR") in rates
        assert len([c for c in series.conversions if c.base.code == "AAPL"]) == 2

    def test_needs_an_end_date(self, funded: Chart):
        from pymoney.core import net_worth_series

        with pytest.raises(ValidationError):
            net_worth_series(
                funded.book,
                ValuationPolicy(target=funded.book.base_currency),
                start=date(2026, 1, 1),
            )


class TestPeriodTable:
    def test_rows_by_account_and_columns_by_month(self, funded: Chart):
        from pymoney.core import period_table

        book = funded.book
        book.post(
            date(2026, 2, 3),
            [LegSpec.of(funded.checking.id, "-40.00"), LegSpec.of(funded.groceries.id)],
        )
        table = period_table(
            book, book.default_policy(date(2026, 2, 28)), start=date(2026, 1, 1)
        )
        assert [c.label for c in table.columns] == ["2026-01", "2026-02"]
        by_path = {r.path: r for r in table.rows}
        groceries = by_path["Expenses:Groceries"]
        assert [c.minor for c in groceries.cells] == [8420, 4000]
        assert groceries.total.minor == 12420
        assert groceries.display_total.minor == 12420
        # The parent row is the sum of its children, column by column.
        expenses = by_path["Expenses"]
        assert [c.minor for c in expenses.cells] == [8420 + 6150, 4000]
        assert expenses.depth == 0 and groceries.depth == 1
        # Income is stored negative; the display sign flips it.
        salary = by_path["Income:Salary"]
        assert [c.minor for c in salary.cells] == [-320000, 0]
        assert [c.minor for c in salary.display_cells] == [320000, 0]
        # Class totals, with the grand total last.
        assert [a.minor for a in table.income] == [-320000, 0, -320000]
        assert [a.minor for a in table.expenses] == [14570, 4000, 18570]
        assert [a.minor for a in table.net] == [320000 - 14570, -4000, 320000 - 18570]
        # No balance-sheet accounts in here.
        assert not any(
            r.path.startswith(("Assets", "Liabilities", "Equity")) for r in table.rows
        )

    def test_agrees_with_the_income_statement(self, funded: Chart):
        from pymoney.core import period_table

        book = funded.book
        policy = book.default_policy(JAN31)
        statement = income_statement(book, policy, start=date(2026, 1, 1))
        table = period_table(book, policy, start=date(2026, 1, 1), interval="year")
        assert table.income[-1] == statement.income
        assert table.expenses[-1] == statement.expenses
        assert table.net[-1] == statement.net

    def test_closed_accounts_are_hidden_by_default(self, funded: Chart):
        from pymoney.core import SetAccountClosed, period_table

        book = funded.book
        book.execute(SetAccountClosed(account_id=funded.fees.id, closed=True))
        policy = book.default_policy(JAN31)
        hidden = period_table(book, policy, start=date(2026, 1, 1))
        shown = period_table(book, policy, start=date(2026, 1, 1), include_closed=True)
        assert "Expenses:Bank Fees" not in {r.path for r in hidden.rows}
        assert "Expenses:Bank Fees" in {r.path for r in shown.rows}
