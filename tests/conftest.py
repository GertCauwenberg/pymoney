"""Shared fixtures: a small but realistic book to exercise the rules against."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from fractions import Fraction

import pytest

from pymoney.core import (
    Account,
    AccountType,
    Book,
    Commodity,
    LegSpec,
    ManualClock,
    PriceSource,
)

START = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(START)


@pytest.fixture
def eur() -> Commodity:
    return Commodity.currency("EUR", name="Euro", symbol="\u20ac")


@pytest.fixture
def book(clock: ManualClock, eur: Commodity) -> Book:
    """An empty book whose base currency is EUR."""
    return Book(name="Test Book", base_currency=eur, clock=clock)


@dataclass
class Chart:
    """A minimal chart of accounts, for readable tests."""

    book: Book
    eur: Commodity
    usd: Commodity
    jpy: Commodity
    aapl: Commodity
    assets: Account
    checking: Account
    savings: Account
    usd_account: Account
    brokerage: Account
    aapl_account: Account
    liabilities: Account
    card: Account
    income: Account
    salary: Account
    expenses: Account
    groceries: Account
    fuel: Account
    fees: Account
    equity: Account
    opening: Account
    opening_usd: Account
    imbalance: Account

    @property
    def clock(self) -> ManualClock:
        return self.book.clock  # type: ignore[return-value]


@pytest.fixture
def chart(book: Book) -> Chart:
    eur = book.base_currency
    usd = book.add_currency("USD", name="US Dollar", symbol="$")
    jpy = book.add_currency("JPY", name="Yen", scale=0)
    aapl = book.add_security("AAPL", name="Apple Inc.", isin="US0378331005")

    assets = book.add_account("Assets", AccountType.ASSET, placeholder=True)
    checking = book.add_account("Checking", AccountType.CHECKING, parent=assets)
    savings = book.add_account("Savings", AccountType.SAVINGS, parent=assets)
    usd_account = book.add_account(
        "USD Wallet", AccountType.CASH, parent=assets, commodity=usd
    )
    brokerage = book.add_account(
        "Brokerage", AccountType.INVESTMENT, parent=assets, commodity=usd
    )
    aapl_account = book.add_account(
        "AAPL", AccountType.STOCK, parent=brokerage, commodity=aapl
    )

    liabilities = book.add_account("Liabilities", AccountType.LIABILITY, placeholder=True)
    card = book.add_account("Visa", AccountType.CREDIT_CARD, parent=liabilities)

    income = book.add_account("Income", AccountType.INCOME, placeholder=True)
    salary = book.add_account("Salary", AccountType.INCOME, parent=income)

    expenses = book.add_account("Expenses", AccountType.EXPENSE, placeholder=True)
    groceries = book.add_account("Groceries", AccountType.EXPENSE, parent=expenses)
    fuel = book.add_account("Fuel", AccountType.EXPENSE, parent=expenses)
    fees = book.add_account("Bank Fees", AccountType.EXPENSE, parent=expenses)

    equity = book.add_account("Equity", AccountType.EQUITY, placeholder=True)
    opening = book.add_account("Opening Balances", AccountType.EQUITY, parent=equity)
    opening_usd = book.add_account(
        "Opening Balances USD", AccountType.EQUITY, parent=equity, commodity=usd
    )
    imbalance = book.add_account("Imbalance", AccountType.EQUITY, parent=equity)

    return Chart(
        book=book,
        eur=eur,
        usd=usd,
        jpy=jpy,
        aapl=aapl,
        assets=assets,
        checking=checking,
        savings=savings,
        usd_account=usd_account,
        brokerage=brokerage,
        aapl_account=aapl_account,
        liabilities=liabilities,
        card=card,
        income=income,
        salary=salary,
        expenses=expenses,
        groceries=groceries,
        fuel=fuel,
        fees=fees,
        equity=equity,
        opening=opening,
        opening_usd=opening_usd,
        imbalance=imbalance,
    )


@pytest.fixture
def funded(chart: Chart) -> Chart:
    """The chart with an opening balance and a few everyday transactions."""
    book = chart.book
    book.post(
        date(2026, 1, 1),
        [
            LegSpec.of(chart.checking.id, "10000.00"),
            LegSpec.of(chart.opening.id),
        ],
        memo="Opening balance",
    )
    book.post(
        date(2026, 1, 5),
        [LegSpec.of(chart.checking.id, "-84.20"), LegSpec.of(chart.groceries.id)],
        memo="Supermarket",
    )
    book.post(
        date(2026, 1, 12),
        [LegSpec.of(chart.card.id, "-61.50"), LegSpec.of(chart.fuel.id)],
        memo="Petrol",
    )
    book.post(
        date(2026, 1, 25),
        [LegSpec.of(chart.checking.id, "3200.00"), LegSpec.of(chart.salary.id)],
        memo="January salary",
    )
    return chart


@pytest.fixture
def priced(funded: Chart) -> Chart:
    """The funded chart plus an FX rate and a share price."""
    book = funded.book
    book.add_price(
        funded.usd,
        funded.eur,
        Fraction(9, 10),
        effective_date=date(2026, 1, 31),
        source=PriceSource.ECB,
    )
    book.add_price(
        funded.aapl,
        funded.usd,
        "190.50",
        effective_date=date(2026, 1, 31),
        source=PriceSource.YAHOO,
    )
    return funded


@pytest.fixture
def today() -> date:
    return date(2026, 1, 31)
