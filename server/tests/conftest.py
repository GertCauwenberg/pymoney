"""Shared fixtures: a small but realistic book to exercise the rules against."""

from __future__ import annotations

import atexit
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, settings

from pymoney.core import (
    Account,
    AccountType,
    Book,
    Commodity,
    LegSpec,
    ManualClock,
    PriceSource,
)

# Property-based tests are the only real check on the rounding invariants, so CI
# runs them much harder than a local edit-test loop would tolerate.  Select with
# ``pytest --hypothesis-profile=ci``.
_HEALTH = [HealthCheck.function_scoped_fixture]
settings.register_profile(
    "dev", max_examples=50, deadline=None, suppress_health_check=_HEALTH
)
settings.register_profile(
    "ci", max_examples=500, deadline=None, derandomize=True, suppress_health_check=_HEALTH
)
settings.register_profile(
    "thorough", max_examples=5000, deadline=None, suppress_health_check=_HEALTH
)
settings.load_profile("dev")

START = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)


# ==========================================================================
# storage backends
# ==========================================================================
class StoreFactory:
    """Opens books on one backend, and cleans up after itself."""

    name = "memory"

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self._closeables: list[Any] = []

    def open(self, **kwargs: Any) -> Book:
        return Book(**kwargs)

    def teardown(self) -> None:
        # Registration order *is* teardown order: the book's connection has to
        # go back to the pool before the engine disposes of it.
        for item in self._closeables:
            item()
        self._closeables.clear()


class SqlFactory(StoreFactory):
    """Shared by every SQL backend; only the URL differs."""

    def url(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def open(self, **kwargs: Any) -> Book:
        from pymoney.store import create_engine, create_schema, open_book

        engine = create_engine(self.url())
        create_schema(engine)
        book = open_book(engine, **kwargs)
        self._closeables.append(book.close)
        self._closeables.append(engine.dispose)
        return book


class SqliteMemoryFactory(SqlFactory):
    name = "sqlite"

    def url(self) -> str:
        return "sqlite+pysqlite:///:memory:"


class SqliteFileFactory(SqlFactory):
    """A real file, so WAL, pragmas and durability are exercised too."""

    name = "sqlite-file"

    def url(self) -> str:
        return f"sqlite+pysqlite:///{self.tmp_path / 'ledger.sqlite'}"


class PostgresFactory(SqlFactory):
    """One throwaway schema per test, so tests cannot see each other's rows."""

    name = "postgres"

    def url(self) -> str:
        return postgres_url()

    def open(self, **kwargs: Any) -> Book:
        from sqlalchemy import text

        from pymoney.store import create_engine, create_schema, open_book

        schema_name = f"t{uuid4().hex[:16]}"
        engine = create_engine(
            self.url(),
            connect_args={"options": f"-csearch_path={schema_name}"},
        )
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
        create_schema(engine)

        def drop() -> None:
            with engine.begin() as conn:
                # Fail fast rather than hang if a connection was leaked.
                conn.execute(text("SET lock_timeout = '10s'"))
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))

        book = open_book(engine, **kwargs)
        self._closeables.append(book.close)
        self._closeables.append(drop)
        self._closeables.append(engine.dispose)
        return book


BACKENDS: dict[str, type[StoreFactory]] = {
    "memory": StoreFactory,
    "sqlite": SqliteMemoryFactory,
    "sqlite-file": SqliteFileFactory,
    "postgres": PostgresFactory,
}

_PG_URL_ENV = "PYMONEY_TEST_DATABASE_URL"


def postgres_url() -> str:
    """Where the PostgreSQL tests point.

    ``$PYMONEY_TEST_DATABASE_URL`` in CI and in the compose stack. Failing that,
    ``pgserver`` is used to start a throwaway local server, so contributors can
    run the PostgreSQL suite without Docker.
    """
    url = os.environ.get(_PG_URL_ENV)
    if url:
        return url
    return _pgserver_url()


@lru_cache(maxsize=1)
def _pgserver_url() -> str:
    try:
        import pgserver
    except ImportError:  # pragma: no cover - depends on the environment
        pytest.skip(
            f"PostgreSQL tests need ${_PG_URL_ENV} or the optional 'pgserver' "
            f"package (pip install -e '.[postgres]')"
        )
    data_dir = Path(tempfile.mkdtemp(prefix="pymoney-pg-"))
    server = pgserver.get_server(str(data_dir))
    atexit.register(server.cleanup)
    return server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture
def store_backend(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    factory = BACKENDS[request.param](tmp_path)
    yield factory
    factory.teardown()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--stores",
        default="memory,sqlite",
        help=(
            "comma-separated storage backends to run the whole suite against: "
            "memory, sqlite, sqlite-file, postgres. postgres needs "
            "PYMONEY_TEST_DATABASE_URL."
        ),
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrise every test that takes a book over each selected backend.

    The promise in the Phase 1 README was that the SQL layer would reproduce the
    in-memory behaviour "test for test". This is that promise, mechanised: the
    same 700 tests, the same assertions, a different store underneath.
    """
    if "store_backend" not in metafunc.fixturenames:
        return
    selected = [s.strip() for s in metafunc.config.getoption("--stores").split(",")]
    unknown = set(selected) - set(BACKENDS)
    if unknown:
        raise pytest.UsageError(f"unknown --stores value(s): {sorted(unknown)}")
    metafunc.parametrize("store_backend", selected, ids=selected, indirect=True)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(START)


@pytest.fixture
def eur() -> Commodity:
    return Commodity.currency("EUR", name="Euro", symbol="\u20ac")


@pytest.fixture
def book(store_backend: StoreFactory, clock: ManualClock, eur: Commodity) -> Book:
    """An empty book whose base currency is EUR, on the backend under test."""
    return store_backend.open(name="Test Book", base_currency=eur, clock=clock)


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


def corrupt(book: Book, kind, entity) -> None:
    """Write straight into the store, bypassing every command and invariant.

    ``check_integrity`` exists because data *can* be wrong -- a buggy importer, a
    bad migration -- so testing it needs a way to make it wrong. Going through
    the store rather than a private dict keeps these tests backend-agnostic.
    """
    book.store.begin()
    book.store.put(kind, entity)
    book.store.commit()


@pytest.fixture
def lax_store(store_backend: StoreFactory) -> None:
    """Skip a test that needs to write referentially broken rows.

    ``check_integrity`` looks for several classes of damage. A relational store
    makes some of them *unrepresentable* -- a dangling parent or a split pointing
    at no account is a foreign key violation, and the database refuses it. That
    is a feature, so the tests for those cases only run where corruption is
    possible at all.
    """
    if store_backend.name != "memory":
        pytest.skip(
            f"{store_backend.name} enforces referential integrity: this "
            f"corruption cannot be written"
        )


@pytest.fixture
def today() -> date:
    return date(2026, 1, 31)


# The HTTP fixtures live in their own module so the ledger fixtures above stay
# readable; importing them here makes them available without a plugin entry.
from tests.conftest_api import (  # noqa: E402,F401
    Client,
    Ledger,
    api,
    ledger,
    user,
)
