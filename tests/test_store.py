"""The persistence layer itself.

The *behaviour* of a book on SQL is covered by running the whole existing suite
against each backend (see ``--stores`` in conftest). What is left for this module
is everything that is specific to storage: durability, migrations, schema drift,
type round-trips and the indexes the queries depend on.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text

from pymoney.core import (
    AccountType,
    Amount,
    Commodity,
    CommodityKind,
    LegSpec,
    ManualClock,
    PriceSource,
    ReconcileState,
    RoundingMode,
    new_id,
)
from pymoney.core import (
    commands as c,
)
from pymoney.store import (
    SqlStore,
    create_engine,
    create_schema,
    drop_schema,
    metadata,
    open_book,
    session,
)
from pymoney.store.types import fits_in_bigint
from tests.conftest import postgres_url

F = Fraction

# These tests drive the store directly, so they pick their own URL rather than
# going through the `--stores` parametrisation.
SQL_URLS = ["sqlite", "postgres"]


@pytest.fixture(params=SQL_URLS)
def sql_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    """A URL for each SQL dialect, isolated per test."""
    if request.param == "sqlite":
        return f"sqlite+pysqlite:///{tmp_path / 'store.sqlite'}"
    url = postgres_url()
    schema_name = f"s{uuid4().hex[:16]}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    engine.dispose()
    request.addfinalizer(lambda: _drop_schema(url, schema_name))
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options=-csearch_path%3D{schema_name}"


def _drop_schema(url: str, schema_name: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        # A lock timeout turns "a test leaked a connection" from a hung CI job
        # into a failure with a stack trace.
        conn.execute(text("SET lock_timeout = '10s'"))
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
    engine.dispose()


@pytest.fixture
def engine(sql_url: str):
    engine = create_engine(sql_url)
    create_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def book(engine):
    book = open_book(
        engine,
        name="Persisted",
        base_currency=Commodity.currency("EUR", symbol="\u20ac"),
        clock=ManualClock(),
    )
    yield book
    book.close()


class TestSchema:
    def test_every_table_is_created(self, engine):
        present = set(inspect(engine).get_table_names())
        assert {t.name for t in metadata.tables.values()} <= present

    def test_the_hot_index_exists(self, engine):
        """Register, balance and report all depend on this one."""
        names = {i["name"] for i in inspect(engine).get_indexes("splits")}
        assert "ix_splits_book_id_account_id_post_date" in names

    def test_every_declared_index_exists(self, engine):
        inspector = inspect(engine)
        for table in metadata.tables.values():
            declared = {i.name for i in table.indexes}
            if not declared:
                continue
            actual = {i["name"] for i in inspector.get_indexes(table.name)}
            assert declared <= actual, f"missing on {table.name}: {declared - actual}"

    def test_drop_schema(self, engine):
        drop_schema(engine)
        assert not set(inspect(engine).get_table_names()) & {
            t.name for t in metadata.tables.values()
        }

    def test_rates_must_be_positive(self, engine):
        """A check constraint, not just a domain rule."""
        from sqlalchemy.exc import IntegrityError

        from pymoney.store import schema

        book_id = new_id()
        with engine.begin() as conn:
            now = datetime.now(timezone.utc)
            conn.execute(
                schema.books.insert().values(
                    id=book_id,
                    name="x",
                    revision=0,
                    settings_revision=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            commodity = {
                "book_id": book_id,
                "kind": "CURRENCY",
                "minor_unit_factor": 100,
                "created_at": now,
                "updated_at": now,
                "revision": 1,
            }
            eur, usd = new_id(), new_id()
            conn.execute(
                schema.commodities.insert().values(
                    id=eur, code="EUR", code_key="EUR", name="EUR", **commodity
                )
            )
            conn.execute(
                schema.commodities.insert().values(
                    id=usd, code="USD", code_key="USD", name="USD", **commodity
                )
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    schema.prices.insert().values(
                        id=new_id(),
                        book_id=book_id,
                        base_id=eur,
                        quote_id=usd,
                        num=-1,
                        den=1,
                        effective_date=date(2026, 1, 1),
                        as_of=now,
                        source="user",
                        created_at=now,
                        updated_at=now,
                        revision=1,
                    )
                )


class TestDurability:
    def test_a_book_survives_reopening(self, sql_url):
        """The whole point: close the process, come back, find the ledger."""
        engine = create_engine(sql_url)
        create_schema(engine)
        book = open_book(
            engine, name="Household", base_currency=Commodity.currency("EUR")
        )
        book_id = book.id
        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        book.post(
            date(2026, 1, 5),
            [LegSpec.of(cash.id, "-84.20"), LegSpec.of(food.id)],
            memo="Shop",
        )
        revision = book.revision
        transaction_id = book.transactions()[0].id
        book.close()
        engine.dispose()

        engine = create_engine(sql_url)
        reopened = open_book(engine)
        assert reopened.id == book_id
        assert reopened.name == "Household"
        assert reopened.revision == revision
        assert reopened.base_currency.code == "EUR"
        assert reopened.balance(cash.id).minor == -8420
        assert reopened.path(food.id) == "Food"
        assert reopened.transaction(transaction_id).memo == "Shop"
        assert reopened.check_integrity() == ()
        reopened.close()
        engine.dispose()

    def test_a_rejected_command_leaves_nothing_behind(self, book):
        """Rollback is the database's, and it has to hold after reopening."""
        from pymoney.core import UnbalancedTransactionError

        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        revision = book.revision
        with pytest.raises(UnbalancedTransactionError):
            book.post(
                date(2026, 1, 5),
                [LegSpec.of(cash.id, "-10"), LegSpec.of(food.id, "9")],
            )
        assert book.revision == revision
        assert book.transactions() == ()
        assert book.balance(cash.id).is_zero
        assert book.store.transaction_count() == 0

    def test_rollback_restores_the_caches(self, book):
        """A failed command must not leave a phantom in the cached tables."""
        from pymoney.core import ConflictError

        book.add_currency("USD")
        before = {c.code for c in book.commodities()}
        with pytest.raises(ConflictError):
            book.add_currency("usd")
        assert {c.code for c in book.commodities()} == before

    def test_a_rolled_back_account_is_gone_from_the_tree(self, book):
        from pymoney.core import ConflictError

        book.add_account("Assets", AccountType.ASSET)
        with pytest.raises(ConflictError):
            book.add_account("assets", AccountType.ASSET)
        assert [a.name for a in book.roots()] == ["Assets"]
        assert book.store.children_ids(None) == (book.roots()[0].id,)

    def test_two_books_coexist_in_one_database(self, engine):
        first = open_book(engine, name="One", base_currency=Commodity.currency("EUR"))
        second = open_book(
            engine, book_id=new_id(), name="Two", base_currency=Commodity.currency("USD")
        )
        first.add_account("Cash", AccountType.CASH)
        second.add_account("Wallet", AccountType.CASH)
        # Strict tenant isolation: decision #7 says book_id on every row, and
        # this is the test that it actually filters.
        assert [a.name for a in first.accounts()] == ["Cash"]
        assert [a.name for a in second.accounts()] == ["Wallet"]
        assert first.base_currency.code == "EUR"
        assert second.base_currency.code == "USD"
        first.close()
        second.close()

    def test_opening_without_an_id_refuses_to_guess(self, engine):
        open_book(engine, name="One", base_currency=Commodity.currency("EUR")).close()
        open_book(
            engine,
            book_id=new_id(),
            name="Two",
            base_currency=Commodity.currency("USD"),
        ).close()
        with pytest.raises(ValueError, match="holds 2 books"):
            open_book(engine)

    def test_session_helper(self, tmp_path):
        url = f"sqlite+pysqlite:///{tmp_path / 'session.sqlite'}"
        with session(
            url, create=True, name="Scripted", base_currency=Commodity.currency("EUR")
        ) as book:
            book.add_account("Cash", AccountType.CASH)
            assert book.revision > 0
        with session(url) as book:
            assert book.name == "Scripted"
            assert [a.name for a in book.accounts()] == ["Cash"]


class TestRoundTrips:
    """Every field of every entity has to survive the database unchanged."""

    def test_commodity(self, book):
        original = book.add_commodity(
            "AAPL",
            kind=CommodityKind.STOCK,
            name="Apple Inc.",
            symbol="\u2318",
            minor_unit_factor=10**4,
            display_precision=3,
            isin="US0378331005",
            quote_source="yahoo",
        )
        book.store.reload()
        assert book.commodity(original.id) == original

    def test_account(self, book):
        parent = book.add_account("Assets", AccountType.ASSET, placeholder=True)
        original = book.add_account(
            "Checking",
            AccountType.CHECKING,
            parent=parent,
            description="everyday",
            code="1000",
            notes="a note",
            institution="Bank",
            iban="DE02120300000000202051",
        )
        book.store.reload()
        assert book.account(original.id) == original

    def test_payee(self, book):
        account = book.add_account("Food", AccountType.EXPENSE)
        original = book.add_payee(
            "Amazon",
            notes="n",
            address="a",
            match_keys=("AMZN", "Amazon EU"),
            default_account_id=account.id,
        )
        book.store.reload()
        assert book.payee(original.id) == original
        assert book.payee(original.id).match_keys == ("AMZN", "Amazon EU")

    def test_transaction_and_splits(self, book):
        usd = book.add_currency("USD")
        aapl = book.add_security("AAPL")
        cash = book.add_account("Cash", AccountType.CASH, commodity=usd)
        shares = book.add_account("AAPL", AccountType.STOCK, commodity=aapl)
        fees = book.add_account("Fees", AccountType.EXPENSE, commodity=usd)
        payee = book.add_payee("Broker")
        original = book.post(
            date(2026, 1, 5),
            [
                LegSpec.of(
                    shares.id,
                    quantity="12",
                    price="183.2433",
                    action="BUY",
                    memo="twelve",
                    number="T-1",
                ),
                LegSpec.of(fees.id, "4.95", price=1, payee_id=payee.id),
                LegSpec.of(cash.id),
            ],
            currency=usd,
            memo="Buy AAPL",
            payee_id=payee.id,
            number="0815",
            rounding=RoundingMode.CEILING,
            imported_id="FITID-42",
        )
        book.store.reload()
        loaded = book.transaction(original.id)
        assert loaded == original
        assert loaded.splits == original.splits
        assert loaded.rounding is RoundingMode.CEILING
        assert loaded.imported_id == "FITID-42"
        assert loaded.splits[0].price == F(1832433, 10000)
        assert loaded.splits[0].action == "BUY"
        assert loaded.splits[1].payee_id == payee.id

    def test_split_order_is_preserved(self, book):
        cash = book.add_account("Cash", AccountType.CASH)
        a = book.add_account("A", AccountType.EXPENSE)
        b = book.add_account("B", AccountType.EXPENSE)
        original = book.post(
            date(2026, 1, 5),
            [
                LegSpec.of(cash.id, "-100.00"),
                LegSpec.of(a.id, "60.00"),
                LegSpec.of(b.id),
            ],
        )
        book.store.reload()
        loaded = book.transaction(original.id)
        assert [s.value.minor for s in loaded.splits] == [-10000, 6000, 4000]
        assert [s.id for s in loaded.splits] == [s.id for s in original.splits]

    def test_reconciliation_state(self, book):
        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        transaction = book.post(
            date(2026, 1, 5), [LegSpec.of(cash.id, "-10"), LegSpec.of(food.id)]
        )
        book.execute(
            c.SetSplitReconcileState(
                transaction_id=transaction.id,
                split_id=transaction.splits[0].id,
                state=ReconcileState.RECONCILED,
                on=date(2026, 1, 31),
            )
        )
        book.store.reload()
        split = book.transaction(transaction.id).splits[0]
        assert split.reconcile_state is ReconcileState.RECONCILED
        assert split.reconcile_date == date(2026, 1, 31)

    def test_price_keeps_its_exact_rational(self, book):
        """A rate is num/den, not a decimal approximation (decision #3)."""
        usd = book.add_currency("USD")
        original = book.add_price(
            usd,
            book.base_currency,
            F(31415926535, 10000000000),
            effective_date=date(2026, 1, 31),
            source=PriceSource.ECB,
        )
        book.store.reload()
        loaded = book.prices.get(original.id)
        assert loaded == original
        assert loaded.rate == F(6283185307, 2000000000)
        assert (loaded.num, loaded.den) == (6283185307, 2000000000)

    def test_bitemporal_columns(self, book):
        usd = book.add_currency("USD")
        as_of = datetime(2026, 1, 31, 17, 30, tzinfo=timezone.utc)
        price = book.add_price(
            usd,
            book.base_currency,
            "0.92",
            effective_date=date(2026, 1, 31),
            as_of=as_of,
        )
        book.store.reload()
        loaded = book.prices.get(price.id)
        assert loaded.effective_date == date(2026, 1, 31)
        assert loaded.as_of == as_of
        assert loaded.as_of.tzinfo is not None

    def test_audit_stamps_stay_timezone_aware(self, book):
        """SQLite has no timezone type; the column type has to compensate."""
        account = book.add_account("Cash", AccountType.CASH)
        book.store.reload()
        loaded = book.account(account.id)
        assert loaded.created_at.tzinfo is not None
        assert loaded.created_at == account.created_at
        assert loaded.updated_at == account.updated_at
        assert loaded.revision == account.revision

    def test_change_log(self, book):
        account = book.add_account("Cash", AccountType.CASH, actor="alice")
        change = book.changelog.for_entity(account.id)[0]
        assert change.command == "CreateAccount"
        assert change.actor == "alice"
        assert change.at.tzinfo is not None
        assert change.entity_id == account.id

    def test_zero_decimal_currency(self, book):
        jpy = book.add_currency("JPY", scale=0)
        cash = book.add_account("Yen", AccountType.CASH, commodity=jpy)
        equity = book.add_account("Open", AccountType.EQUITY, commodity=jpy)
        book.post(
            date(2026, 1, 1),
            [LegSpec.of(cash.id, "500000"), LegSpec.of(equity.id)],
            currency=jpy,
        )
        book.store.reload()
        assert book.balance(cash.id).minor == 500000
        assert book.balance(cash.id).commodity.minor_unit_factor == 1

    def test_large_amounts(self, book):
        """BIGINT minor units: a trillion-unit balance must not overflow."""
        cash = book.add_account("Cash", AccountType.CASH)
        equity = book.add_account("Open", AccountType.EQUITY)
        book.post(
            date(2026, 1, 1),
            [LegSpec.of(cash.id, "99999999999999.99"), LegSpec.of(equity.id)],
        )
        book.store.reload()
        assert book.balance(cash.id).minor == 9999999999999999


class TestBigintGuards:
    def test_fits_in_bigint(self):
        assert fits_in_bigint(2**63 - 1)
        assert fits_in_bigint(-(2**63))
        assert not fits_in_bigint(2**63)
        assert not fits_in_bigint(-(2**63) - 1)

    def test_an_unstorable_rate_is_refused_loudly(self, book):
        """Better a clear error than a silently truncated exchange rate."""
        usd = book.add_currency("USD")
        with pytest.raises(ValueError, match="does not fit in a 64-bit column"):
            book.add_price(
                usd,
                book.base_currency,
                Fraction(2**70 + 1, 3),
                effective_date=date(2026, 1, 1),
            )

    def test_the_book_is_unchanged_afterwards(self, book):
        usd = book.add_currency("USD")
        revision = book.revision
        with pytest.raises(ValueError):
            book.add_price(
                usd,
                book.base_currency,
                Fraction(2**70 + 1, 3),
                effective_date=date(2026, 1, 1),
            )
        assert book.revision == revision
        assert len(book.prices) == 0


class TestQueriesArePushedDown:
    """The store must answer aggregates itself, not by loading the ledger."""

    @pytest.fixture
    def ledger(self, book):
        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        fuel = book.add_account("Fuel", AccountType.EXPENSE)
        for day, amount, account in (
            (1, "-10.00", food),
            (5, "-20.00", fuel),
            (20, "-30.00", food),
        ):
            book.post(
                date(2026, 1, day),
                [LegSpec.of(cash.id, amount), LegSpec.of(account.id)],
            )
        return book, cash, food, fuel

    def test_balances_by_account(self, ledger):
        book, cash, food, fuel = ledger
        totals = book.store.balances_by_account()
        assert totals[cash.id] == -6000
        assert totals[food.id] == 4000
        assert totals[fuel.id] == 2000

    def test_balances_by_account_respects_the_window(self, ledger):
        book, cash, food, fuel = ledger
        totals = book.store.balances_by_account(end=date(2026, 1, 10))
        assert totals[cash.id] == -3000
        assert food.id in totals
        totals = book.store.balances_by_account(start=date(2026, 1, 10))
        assert totals[cash.id] == -3000
        assert fuel.id not in totals

    def test_posting_count(self, ledger):
        book, cash, food, _ = ledger
        assert book.store.posting_count(cash.id) == 3
        assert book.store.posting_count(food.id) == 2
        assert book.store.posting_count(new_id()) == 0

    def test_transactions_filtered_by_account(self, ledger):
        book, _, food, _ = ledger
        found = book.store.transactions(account_ids=[food.id])
        assert len(found) == 2
        assert [t.date for t in found] == [date(2026, 1, 1), date(2026, 1, 20)]

    def test_transactions_for_no_accounts_is_empty(self, ledger):
        book, *_ = ledger
        assert book.store.transactions(account_ids=[]) == ()

    def test_find_imported(self, book):
        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        transaction = book.post(
            date(2026, 1, 1),
            [LegSpec.of(cash.id, "-1"), LegSpec.of(food.id)],
            imported_id="X-1",
        )
        assert book.store.find_imported("X-1") == transaction.id
        assert book.store.find_imported("nope") is None

    def test_imported_id_is_unique_in_the_database(self, book):
        """Belt and braces: the command checks, and so does the schema.

        Two concurrent imports of the same statement would each pass their own
        check and both insert, so the constraint has to be in the database.
        """
        from sqlalchemy.exc import IntegrityError

        from pymoney.store import schema

        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        book.post(
            date(2026, 1, 1),
            [LegSpec.of(cash.id, "-1"), LegSpec.of(food.id)],
            imported_id="X-1",
        )
        other = book.post(
            date(2026, 1, 2),
            [LegSpec.of(cash.id, "-2"), LegSpec.of(food.id)],
            imported_id="X-2",
        )
        with book.store.engine.connect() as conn, pytest.raises(IntegrityError):
            conn.execute(
                schema.transactions.update()
                .where(schema.transactions.c.id == other.id)
                .values(imported_id="X-1")
            )

    def test_transactions_for_payee(self, book):
        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        payee = book.add_payee("Shop")
        on_header = book.post(
            date(2026, 1, 1),
            [LegSpec.of(cash.id, "-1"), LegSpec.of(food.id)],
            payee_id=payee.id,
        )
        on_split = book.post(
            date(2026, 1, 2),
            [LegSpec.of(cash.id, "-2"), LegSpec.of(food.id, payee_id=payee.id)],
        )
        book.post(date(2026, 1, 3), [LegSpec.of(cash.id, "-3"), LegSpec.of(food.id)])
        found = {t.id for t in book.store.transactions_for_payee(payee.id)}
        assert found == {on_header.id, on_split.id}

    def test_splits_carry_the_denormalised_post_date(self, book):
        """The copy must match, or every date-filtered query lies."""
        from pymoney.store import schema

        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        transaction = book.post(
            date(2026, 1, 5), [LegSpec.of(cash.id, "-1"), LegSpec.of(food.id)]
        )
        book.execute(
            c.UpdateTransaction(transaction_id=transaction.id, date=date(2026, 2, 9))
        )
        with book.store.engine.connect() as conn:
            rows = set(
                conn.execute(
                    select(schema.splits.c.post_date).where(
                        schema.splits.c.transaction_id == transaction.id
                    )
                ).scalars()
            )
        assert rows == {date(2026, 2, 9)}

    def test_deleting_a_transaction_deletes_its_splits(self, book):
        from pymoney.store import schema

        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        transaction = book.post(
            date(2026, 1, 5), [LegSpec.of(cash.id, "-1"), LegSpec.of(food.id)]
        )
        book.execute(c.DeleteTransaction(transaction_id=transaction.id))
        with book.store.engine.connect() as conn:
            remaining = list(conn.execute(select(schema.splits.c.id)).scalars())
        assert remaining == []

    def test_updating_a_transaction_replaces_its_splits(self, book):
        from pymoney.store import schema

        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        fuel = book.add_account("Fuel", AccountType.EXPENSE)
        transaction = book.post(
            date(2026, 1, 5), [LegSpec.of(cash.id, "-1"), LegSpec.of(food.id)]
        )
        book.execute(
            c.UpdateTransaction(
                transaction_id=transaction.id,
                legs=(LegSpec.of(cash.id, "-2"), LegSpec.of(fuel.id)),
            )
        )
        with book.store.engine.connect() as conn:
            count = list(
                conn.execute(
                    select(schema.splits.c.id).where(
                        schema.splits.c.transaction_id == transaction.id
                    )
                ).scalars()
            )
        assert len(count) == 2
        assert book.balance(food.id).is_zero
        assert book.balance(fuel.id).minor == 200


class TestStoreDirectly:
    def test_repr_hides_the_password(self):
        engine = create_engine("postgresql+psycopg://user:secret@localhost/db")
        store = SqlStore(engine, new_id())
        assert "secret" not in repr(store)
        assert "***" in repr(store)
        engine.dispose()

    def test_deleting_a_missing_entity_returns_false(self, book):
        from pymoney.core import EntityKind

        book.store.begin()
        assert not book.store.delete(EntityKind.PAYEE, new_id())
        assert not book.store.delete(EntityKind.TRANSACTION, new_id())
        assert not book.store.delete(EntityKind.PRICE, new_id())
        book.store.rollback()

    def test_getting_a_missing_entity_returns_none(self, book):
        from pymoney.core import EntityKind

        assert book.store.get(EntityKind.TRANSACTION, new_id()) is None
        assert book.store.get(EntityKind.ACCOUNT, new_id()) is None

    def test_close_is_idempotent(self, book):
        book.close()
        book.close()

    def test_using_a_closed_store_is_an_error(self, book):
        """Silently reconnecting leaks connections and, on PostgreSQL, locks."""
        book.close()
        with pytest.raises(RuntimeError, match="closed"):
            book.accounts()

    def test_change_count_and_all_changes_agree(self, book):
        book.add_account("Cash", AccountType.CASH)
        assert book.store.change_count() == len(book.store.all_changes())


class TestSqliteSettings:
    """SQLite's defaults are wrong for a ledger; check we corrected them."""

    def test_foreign_keys_are_enforced(self, tmp_path):
        engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'fk.sqlite'}")
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        engine.dispose()

    def test_wal_is_enabled_for_file_databases(self, tmp_path):
        engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'wal.sqlite'}")
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        engine.dispose()

    def test_synchronous_is_configurable(self, tmp_path):
        engine = create_engine(
            f"sqlite+pysqlite:///{tmp_path / 's.sqlite'}", synchronous="FULL"
        )
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA synchronous")).scalar() == 2
        engine.dispose()

    def test_memory_databases_share_one_connection(self):
        """Otherwise each connection would get its own empty database."""
        engine = create_engine("sqlite+pysqlite:///:memory:")
        create_schema(engine)
        with engine.connect() as conn:
            assert "books" in inspect(conn).get_table_names()
        with engine.connect() as conn:
            assert "books" in inspect(conn).get_table_names()
        engine.dispose()

    def test_url_comes_from_the_environment(self, monkeypatch, tmp_path):
        url = f"sqlite+pysqlite:///{tmp_path / 'env.sqlite'}"
        monkeypatch.setenv("PYMONEY_DATABASE_URL", url)
        engine = create_engine()
        assert str(engine.url) == url
        engine.dispose()

    def test_default_is_in_memory_sqlite(self, monkeypatch):
        monkeypatch.delenv("PYMONEY_DATABASE_URL", raising=False)
        engine = create_engine()
        assert engine.url.database in (None, ":memory:")
        engine.dispose()


class TestTypeDecorators:
    def test_uuid_rejects_a_non_uuid(self, engine):
        from sqlalchemy.exc import StatementError

        from pymoney.store import schema

        now = datetime.now(timezone.utc)
        with (
            engine.begin() as conn,
            pytest.raises((TypeError, StatementError), match="expected a UUID"),
        ):
            conn.execute(
                schema.books.insert().values(
                    id="not-a-uuid",
                    name="x",
                    revision=0,
                    settings_revision=0,
                    created_at=now,
                    updated_at=now,
                )
            )

    def test_naive_datetimes_are_refused(self, engine):
        """Audit stamps are UTC instants; a naive one is a bug upstream."""
        from sqlalchemy.exc import StatementError

        from pymoney.store import schema

        with (
            engine.begin() as conn,
            pytest.raises((ValueError, StatementError), match="naive datetime"),
        ):
            conn.execute(
                schema.books.insert().values(
                    id=new_id(),
                    name="x",
                    revision=0,
                    settings_revision=0,
                    created_at=datetime(2026, 1, 1),
                    updated_at=datetime(2026, 1, 1),
                )
            )

    def test_timezones_are_normalised_to_utc(self, book):
        """An instant is an instant, whatever offset it arrived in."""
        other = timezone(__import__("datetime").timedelta(hours=5))
        usd = book.add_currency("USD")
        price = book.add_price(
            usd,
            book.base_currency,
            "0.9",
            effective_date=date(2026, 1, 1),
            as_of=datetime(2026, 1, 1, 5, tzinfo=other),
        )
        book.store.reload()
        assert book.prices.get(price.id).as_of == datetime(
            2026, 1, 1, tzinfo=timezone.utc
        )


class TestMigrations:
    """The migration scripts are the supported way to create the schema, so
    they must agree with the metadata exactly."""

    @staticmethod
    def _config(url: str):
        from alembic.config import Config

        config = Config("alembic.ini")
        # alembic.ini goes through configparser, which treats % as interpolation.
        # This is exactly why env.py prefers $PYMONEY_DATABASE_URL: a password or
        # a query parameter containing % would otherwise fail to parse.
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        return config

    def test_upgrade_head_creates_everything(self, sql_url):
        from alembic import command

        command.upgrade(self._config(sql_url), "head")
        engine = create_engine(sql_url)
        present = set(inspect(engine).get_table_names())
        assert {t.name for t in metadata.tables.values()} <= present
        assert "alembic_version" in present
        engine.dispose()

    def test_a_migrated_database_is_usable(self, sql_url):
        from alembic import command

        command.upgrade(self._config(sql_url), "head")
        engine = create_engine(sql_url)
        book = open_book(engine, name="Migrated", base_currency=Commodity.currency("EUR"))
        cash = book.add_account("Cash", AccountType.CASH)
        food = book.add_account("Food", AccountType.EXPENSE)
        book.post(date(2026, 1, 1), [LegSpec.of(cash.id, "-10.00"), LegSpec.of(food.id)])
        assert book.balance(cash.id).minor == -1000
        assert book.check_integrity() == ()
        book.close()
        engine.dispose()

    def test_no_schema_drift(self, sql_url):
        """The drift test: after `upgrade head`, autogenerate must find nothing.

        This is what stops the schema and the migrations quietly diverging --
        the failure mode where a fresh install works and an upgraded one does
        not.
        """
        from alembic import command
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        command.upgrade(self._config(sql_url), "head")
        engine = create_engine(sql_url)
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn,
                opts={"compare_type": True, "compare_server_default": True},
            )
            diff = compare_metadata(context, metadata)
        engine.dispose()
        # alembic_version is not in our metadata, so ignore it.
        diff = [
            d for d in diff if not (isinstance(d, tuple) and "alembic_version" in repr(d))
        ]
        assert diff == [], f"schema drift: {diff}"

    def test_downgrade_removes_everything(self, sql_url):
        from alembic import command

        config = self._config(sql_url)
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        engine = create_engine(sql_url)
        present = set(inspect(engine).get_table_names())
        assert not present & {t.name for t in metadata.tables.values()}
        engine.dispose()

    def test_env_requires_a_url(self, monkeypatch):
        """Silently migrating the wrong database would be much worse."""
        from alembic import command

        monkeypatch.delenv("PYMONEY_DATABASE_URL", raising=False)
        config = self._config("")
        config.set_main_option("sqlalchemy.url", "")
        with pytest.raises(SystemExit, match="no database URL"):
            command.upgrade(config, "head")

    def test_the_url_can_come_from_the_command_line(self, sql_url, monkeypatch):
        """`-x url=...` wins over the environment, for a one-off migration."""
        from alembic import command
        from alembic.config import Config

        monkeypatch.setenv("PYMONEY_DATABASE_URL", "sqlite+pysqlite:///:memory:")
        config = Config("alembic.ini")
        config.cmd_opts = type("Opts", (), {"x": [f"url={sql_url}"]})()
        command.upgrade(config, "head")
        engine = create_engine(sql_url)
        assert "books" in inspect(engine).get_table_names()
        engine.dispose()

    def test_offline_mode_emits_sql(self, sql_url, capsys):
        """For review, or for a DBA who will not run Python against production."""
        from alembic import command

        command.upgrade(self._config(sql_url), "head", sql=True)
        out = capsys.readouterr().out
        assert "CREATE TABLE books" in out
        assert "ix_splits_book_id_account_id_post_date" in out


class TestDecimalInput:
    def test_decimal_amounts_round_trip(self, book):
        cash = book.add_account("Cash", AccountType.CASH)
        equity = book.add_account("Open", AccountType.EQUITY)
        book.post(
            date(2026, 1, 1),
            [LegSpec.of(cash.id, Decimal("1234.56")), LegSpec.of(equity.id)],
        )
        book.store.reload()
        assert book.balance(cash.id) == Amount(book.base_currency, 123456)


def test_postgres_is_actually_exercised():
    """Guard against the PostgreSQL tests silently never running.

    The most likely failure of this whole arrangement is a misconfigured CI
    where everything quietly runs on SQLite and the PostgreSQL path rots.
    """
    if os.environ.get("PYMONEY_REQUIRE_POSTGRES") != "1":
        pytest.skip("set PYMONEY_REQUIRE_POSTGRES=1 in CI to enforce this")
    url = postgres_url()
    assert url.startswith("postgresql"), url
    engine = create_engine(url)
    with engine.connect() as conn:
        assert "PostgreSQL" in conn.execute(text("SELECT version()")).scalar_one()
    engine.dispose()
