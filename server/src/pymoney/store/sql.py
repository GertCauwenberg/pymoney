"""A :class:`~pymoney.core.store.Store` over SQLAlchemy.

The caching policy is the heart of this module, so it is worth stating plainly:

**Small tables are cached in memory; large tables are queried.**

Commodities, accounts, payees and prices are cached. A personal ledger has tens
to hundreds of each, they are read constantly -- every amount needs its
commodity, every report walks the account tree -- and caching them turns
thousands of round trips into none. Transactions, splits and the change log are
never cached: they grow without bound, and every access to them is a filtered
query that an index can answer.

The consequence is that this store assumes it is the **only writer** (decision
#14). Another process writing the same book would invalidate the caches without
telling us. That is exactly the constraint the architecture already imposes, and
:meth:`SqlStore.reload` exists for the cases where it has to be re-established.

Atomicity comes from the database: ``begin``/``commit``/``rollback`` map onto a
real transaction, and a failed command rolls back the caches by reloading them.
Rollback is rare (a rejected command) and the tables are small, so paying O(small
table) there keeps the cache-maintenance code out of the happy path entirely.

**A connection is held only for the duration of a unit of work.** Between
commands the store owns no connection at all: a read borrows one from the pool
and returns it -- transaction ended -- as soon as the rows are fetched. The
store used to pin one connection for its whole life, and every read on it
auto-began a transaction that nothing closed. On SQLite that left a stale read
snapshot open between requests, so the next write failed with ``database is
locked`` (``SQLITE_BUSY_SNAPSHOT``, which ``busy_timeout`` does not cover) as
soon as any other connection -- the session table, another book -- had written
in the meantime. On PostgreSQL it left the connection idle in transaction
indefinitely, and either way each open book cost a pool slot for good.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Engine,
    Table,
    and_,
    bindparam,
    delete,
    exists,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.engine import Connection
from sqlalchemy.sql import Executable

from ..core.account import Account
from ..core.changelog import Change, EntityKind
from ..core.clock import Clock, SystemClock
from ..core.commodity import Commodity
from ..core.entity import Entity
from ..core.payee import Payee
from ..core.price import PriceDB
from ..core.store import CommandRecord, Store, StoredBook, TransactionSearch
from ..core.transaction import Transaction
from . import schema
from .connections import write_connection
from .mapper import Registry, Rows

__all__ = ["SqlStore"]

_TABLES = {
    EntityKind.COMMODITY: schema.commodities,
    EntityKind.ACCOUNT: schema.accounts,
    EntityKind.PAYEE: schema.payees,
    EntityKind.PRICE: schema.prices,
}

# Statements are built once and executed with parameters, never rebuilt per row.
#
# `insert(table).values(**row)` looks equivalent but is not: embedding the values
# in the statement produces a different object every time, so SQLAlchemy's
# compiled-statement cache misses on every call. Profiling a bulk import showed
# more time spent constructing SQL than executing it.
_UPDATES: dict[str, Executable] = {}

# Columns an UPDATE must never touch.
#
# ``id``: SQLite checks foreign keys immediately, so assigning a parent key its
# own value still counts as changing it and fails against child rows.
# ``book_id``: a row never moves between tenants.
# ``created_at``: immutable by definition -- and the book row would otherwise
# have its creation time reset on every single command.
_IMMUTABLE = frozenset({"id", "book_id", "created_at"})


def _update_by_id(table: Table) -> Executable:
    """A cached ``UPDATE ... WHERE id = :pk`` covering every mutable column."""
    cached = _UPDATES.get(table.name)
    if cached is None:
        mutable = [c.name for c in table.columns if c.name not in _IMMUTABLE]
        cached = (
            table.update()
            .where(table.c.id == bindparam("pk"))
            .values({name: bindparam(name) for name in mutable})
        )
        _UPDATES[table.name] = cached
    return cached


def _update_params(values: dict[str, Any], entity_id: UUID) -> dict[str, Any]:
    params = {k: v for k, v in values.items() if k not in _IMMUTABLE}
    params["pk"] = entity_id
    return params


class SqlStore(Store):
    """Persistence for one book in one database."""

    def __init__(
        self, engine: Engine, book_id: UUID, *, clock: Clock | None = None
    ) -> None:
        self._engine = engine
        self._book_id = book_id
        # The book row is the one entity whose audit stamps the domain does not
        # supply, so the store needs a clock of its own.
        self._clock: Clock = clock or SystemClock()
        self._connection: Connection | None = None
        self._book = StoredBook(id=book_id)
        self._commodities: dict[UUID, Commodity] = {}
        self._accounts: dict[UUID, Account] = {}
        self._payees: dict[UUID, Payee] = {}
        self._prices = PriceDB()
        self._loaded = False
        self._closed = False
        self._book_row_exists = False

    # ====================================================================
    # connection and unit of work
    # ====================================================================
    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def in_unit_of_work(self) -> bool:
        return self._connection is not None

    def _check_open(self) -> None:
        # Using a closed store used to silently open a fresh connection, which
        # leaks one per call and -- on PostgreSQL -- holds locks that make a
        # later DROP block forever. Reuse after close is a bug in the caller.
        if self._closed:
            raise RuntimeError("this store is closed; open the book again to use it")

    @property
    def _conn(self) -> Connection:
        """The unit-of-work connection. Only valid between begin and commit."""
        self._check_open()
        if self._connection is None:
            raise RuntimeError("no unit of work is open; reads go through _read()")
        return self._connection

    @contextmanager
    def _read(self) -> Iterator[Connection]:
        """A connection to read on.

        Inside a unit of work this is *the* connection, so a command sees its
        own writes. Outside one it is borrowed from the pool and handed back --
        which ends the transaction SQLAlchemy began for it -- before the caller
        continues. Results must therefore be fetched inside the block.
        """
        self._check_open()
        if self._connection is not None:
            yield self._connection
            return
        conn = self._engine.connect()
        try:
            yield conn
        finally:
            # close() rolls back whatever was auto-begun and returns the
            # connection to the pool.
            conn.close()

    def _rows(self, query: Executable) -> list[Any]:
        with self._read() as conn:
            return list(conn.execute(query))

    def _scalar(self, query: Executable) -> Any:
        with self._read() as conn:
            return conn.execute(query).scalar_one_or_none()

    def begin(self) -> None:
        self._check_open()
        self._ensure_loaded()
        if self._connection is None:
            # A unit of work reads (is the id taken?) and then writes; it must
            # begin as a writer or a concurrent commit elsewhere would fail it.
            self._connection = write_connection(self._engine)
        if not self._connection.in_transaction():
            self._connection.begin()
        # Everything else has a foreign key to `books`, so the row has to be
        # there before the first command inserts anything -- including the
        # InitialiseBook command that creates the base currency.
        self._ensure_book_row()

    def _ensure_book_row(self) -> None:
        # Checked once per store: this process is the only writer (decision #14),
        # so once the row is there it stays there.
        if self._book_row_exists:
            return
        present = self._conn.execute(
            select(func.count())
            .select_from(schema.books)
            .where(schema.books.c.id == self._book_id)
        ).scalar_one()
        if not present:
            self._conn.execute(
                schema.books.insert(),
                Rows.book_to_row(self._book, at=self._clock.now()),
            )
        self._book_row_exists = True

    def _release(self) -> None:
        """Hand the unit-of-work connection back to the pool."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def commit(self) -> None:
        if self._connection is not None:
            if self._connection.in_transaction():
                self._connection.commit()
            self._release()

    def rollback(self) -> None:
        if self._connection is not None:
            if self._connection.in_transaction():
                self._connection.rollback()
            self._release()
        # The caches may hold writes the database has just discarded. Reloading
        # is O(small tables) and obviously correct, which beats maintaining an
        # undo log that has to agree with the database's idea of the truth.
        self.reload()

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """A real SQL savepoint, supported by both dialects."""
        nested = self._conn.begin_nested()
        try:
            yield
        except Exception:
            nested.rollback()
            # The caches may hold writes the savepoint just discarded. Reloading
            # inside the open transaction sees everything the batch has written
            # so far and nothing it has rolled back.
            self.reload()
            raise
        else:
            nested.commit()

    def close(self) -> None:
        if self._connection is not None and self._connection.in_transaction():
            self._connection.rollback()
        self._release()
        self._loaded = False
        self._closed = True

    # ====================================================================
    # loading
    # ====================================================================
    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()

    def reload(self) -> None:
        """Re-read the cached tables from the database."""
        with self._read() as conn:
            self._reload_from(conn)
        self._loaded = True

    def _reload_from(self, conn: Connection) -> None:
        row = conn.execute(
            select(schema.books).where(schema.books.c.id == self._book_id)
        ).one_or_none()
        self._book = (
            Rows.book_from_row(row) if row is not None else StoredBook(id=self._book_id)
        )

        self._commodities = {
            r.id: Rows.commodity_from_row(r)
            for r in conn.execute(
                select(schema.commodities).where(
                    schema.commodities.c.book_id == self._book_id
                )
            )
        }
        registry = self._registry()
        self._accounts = {
            r.id: Rows.account_from_row(r, registry)
            for r in conn.execute(
                select(schema.accounts).where(schema.accounts.c.book_id == self._book_id)
            )
        }
        self._prices = PriceDB(
            [
                Rows.price_from_row(r, registry)
                for r in conn.execute(
                    select(schema.prices).where(schema.prices.c.book_id == self._book_id)
                )
            ]
        )
        self._payees = {
            r.id: Rows.payee_from_row(r)
            for r in conn.execute(
                select(schema.payees).where(schema.payees.c.book_id == self._book_id)
            )
        }

    def _registry(self) -> Registry:
        return Registry(commodities=self._commodities, accounts=self._accounts)

    # ====================================================================
    # the book row
    # ====================================================================
    def load_book(self) -> StoredBook:
        self._ensure_loaded()
        return self._book

    def save_book(self, book: StoredBook) -> None:
        values = Rows.book_to_row(book, at=self._clock.now())
        self._conn.execute(_update_by_id(schema.books), _update_params(values, book.id))
        self._book = book

    # ====================================================================
    # entities
    # ====================================================================
    def get(self, kind: EntityKind, entity_id: UUID) -> Any | None:
        self._ensure_loaded()
        if kind is EntityKind.COMMODITY:
            return self._commodities.get(entity_id)
        if kind is EntityKind.ACCOUNT:
            return self._accounts.get(entity_id)
        if kind is EntityKind.PAYEE:
            return self._payees.get(entity_id)
        if kind is EntityKind.PRICE:
            return self._prices.get(entity_id)
        return self._load_transaction(entity_id)

    def exists(self, kind: EntityKind, entity_id: UUID) -> bool:
        if kind is not EntityKind.TRANSACTION:
            return self.get(kind, entity_id) is not None
        return (
            self._scalar(
                select(schema.transactions.c.id).where(
                    schema.transactions.c.id == entity_id,
                    schema.transactions.c.book_id == self._book_id,
                )
            )
            is not None
        )

    def put(self, kind: EntityKind, entity: Entity, *, created: bool = False) -> None:
        self._ensure_loaded()
        if kind is EntityKind.TRANSACTION:
            self._put_transaction(entity, created=created)  # type: ignore[arg-type]
            return

        table = _TABLES[kind]
        values = self._entity_to_row(kind, entity)
        if created:
            self._conn.execute(table.insert(), values)
        else:
            self._conn.execute(_update_by_id(table), _update_params(values, entity.id))
        self._cache_put(kind, entity)

    def delete(self, kind: EntityKind, entity_id: UUID) -> bool:
        self._ensure_loaded()
        if kind is EntityKind.TRANSACTION:
            # Splits go with it: ON DELETE CASCADE covers PostgreSQL, and the
            # explicit delete covers SQLite builds without foreign_keys on.
            self._conn.execute(
                delete(schema.splits).where(schema.splits.c.transaction_id == entity_id)
            )
            result = self._conn.execute(
                delete(schema.transactions).where(schema.transactions.c.id == entity_id)
            )
            return bool(result.rowcount)

        if self.get(kind, entity_id) is None:
            return False
        table = _TABLES[kind]
        self._conn.execute(delete(table).where(table.c.id == entity_id))
        self._cache_delete(kind, entity_id)
        return True

    def _entity_to_row(self, kind: EntityKind, entity: Any) -> dict[str, Any]:
        if kind is EntityKind.COMMODITY:
            return Rows.commodity_to_row(entity, self._book_id)
        if kind is EntityKind.ACCOUNT:
            return Rows.account_to_row(entity, self._book_id)
        if kind is EntityKind.PAYEE:
            return Rows.payee_to_row(entity, self._book_id)
        return Rows.price_to_row(entity, self._book_id)

    def _cache_put(self, kind: EntityKind, entity: Any) -> None:
        if kind is EntityKind.COMMODITY:
            self._commodities[entity.id] = entity
        elif kind is EntityKind.ACCOUNT:
            self._accounts[entity.id] = entity
        elif kind is EntityKind.PAYEE:
            self._payees[entity.id] = entity
        else:
            self._prices.add(entity)

    def _cache_delete(self, kind: EntityKind, entity_id: UUID) -> None:
        if kind is EntityKind.COMMODITY:
            del self._commodities[entity_id]
        elif kind is EntityKind.ACCOUNT:
            del self._accounts[entity_id]
        elif kind is EntityKind.PAYEE:
            del self._payees[entity_id]
        else:
            self._prices.remove(entity_id)

    def commodities(self) -> tuple[Commodity, ...]:
        self._ensure_loaded()
        return tuple(self._commodities.values())

    def accounts(self) -> tuple[Account, ...]:
        self._ensure_loaded()
        return tuple(self._accounts.values())

    def payees(self) -> tuple[Payee, ...]:
        self._ensure_loaded()
        return tuple(self._payees.values())

    def children_ids(self, parent_id: UUID | None) -> tuple[UUID, ...]:
        self._ensure_loaded()
        return tuple(a.id for a in self._accounts.values() if a.parent_id == parent_id)

    # ====================================================================
    # transactions
    # ====================================================================
    def _put_transaction(self, transaction: Transaction, *, created: bool) -> None:
        conn = self._conn
        values = Rows.transaction_to_row(transaction, self._book_id)
        if created:
            conn.execute(schema.transactions.insert(), values)
        else:
            conn.execute(
                _update_by_id(schema.transactions),
                _update_params(values, transaction.id),
            )
            # Splits are replaced wholesale, exactly as the domain does: an
            # edited transaction is a new set of legs, not a patch.
            conn.execute(
                delete(schema.splits).where(
                    schema.splits.c.transaction_id == transaction.id
                )
            )
        if transaction.splits:
            conn.execute(
                schema.splits.insert(),
                [
                    Rows.split_to_row(split, transaction, self._book_id, position)
                    for position, split in enumerate(transaction.splits)
                ],
            )

    def _load_transaction(self, transaction_id: UUID) -> Transaction | None:
        with self._read() as conn:
            row = conn.execute(
                select(schema.transactions).where(
                    schema.transactions.c.id == transaction_id,
                    schema.transactions.c.book_id == self._book_id,
                )
            ).one_or_none()
            if row is None:
                return None
            split_rows = list(
                conn.execute(
                    select(schema.splits).where(
                        schema.splits.c.transaction_id == transaction_id
                    )
                )
            )
        return Rows.transaction_from_rows(row, split_rows, self._registry())

    def transaction_count(self) -> int:
        return int(
            self._scalar(
                select(func.count())
                .select_from(schema.transactions)
                .where(schema.transactions.c.book_id == self._book_id)
            )
        )

    def transaction_ids(self) -> tuple[UUID, ...]:
        return tuple(
            r.id
            for r in self._rows(
                select(schema.transactions.c.id).where(
                    schema.transactions.c.book_id == self._book_id
                )
            )
        )

    def transactions(
        self,
        *,
        account_ids: Iterable[UUID] | None = None,
        start: date | None = None,
        end: date | None = None,
        from_key: tuple[date, UUID] | None = None,
        limit: int | None = None,
    ) -> tuple[Transaction, ...]:
        self._ensure_loaded()
        t, s = schema.transactions, schema.splits

        # The cursor's date is a plain lower bound the indexes can use; the id
        # only disambiguates rows on that date.
        if from_key is not None and (start is None or from_key[0] > start):
            start = from_key[0]

        query = select(t).where(t.c.book_id == self._book_id)
        if start is not None:
            query = query.where(t.c.post_date >= start)
        if end is not None:
            query = query.where(t.c.post_date <= end)
        if from_key is not None:
            # (post_date, id) >= (d, i), spelled out because row-value
            # comparison is not portable. UUIDs compare bytewise on both
            # dialects, which is the same order as the v7 timestamp.
            query = query.where(
                or_(
                    t.c.post_date > from_key[0],
                    and_(t.c.post_date == from_key[0], t.c.id >= from_key[1]),
                )
            )
        if account_ids is not None:
            wanted = list(account_ids)
            if not wanted:
                return ()
            # The account filter runs against splits, where the covering index
            # lives; the date predicate is repeated there so it can be used.
            matching = select(s.c.transaction_id).where(
                s.c.book_id == self._book_id, s.c.account_id.in_(wanted)
            )
            if start is not None:
                matching = matching.where(s.c.post_date >= start)
            if end is not None:
                matching = matching.where(s.c.post_date <= end)
            query = query.where(t.c.id.in_(matching))

        query = query.order_by(t.c.post_date, t.c.id)
        if limit is not None:
            query = query.limit(limit)
        with self._read() as conn:
            return self._hydrate(conn, list(conn.execute(query)), query)

    def transactions_by_ids(self, ids: Iterable[UUID]) -> tuple[Transaction, ...]:
        self._ensure_loaded()
        wanted = list(ids)
        if not wanted:
            return ()
        t = schema.transactions
        query = (
            select(t)
            .where(t.c.book_id == self._book_id, t.c.id.in_(wanted))
            .order_by(t.c.post_date, t.c.id)
        )
        with self._read() as conn:
            return self._hydrate(conn, list(conn.execute(query)), query)

    def _hydrate(
        self, conn: Connection, rows: list[Any], source: Any = None
    ) -> tuple[Transaction, ...]:
        """Attach splits to a page of transactions with one extra query.

        Given the query that produced the rows, the splits are fetched with
        ``transaction_id IN (<that query>)`` rather than an ``IN`` list of
        thousands of literal ids. The database plans it as a join; a long bind
        list defeats statement caching and, past a few hundred entries, the
        planner too.
        """
        if not rows:
            return ()
        splits_query = select(schema.splits)
        # A LIMIT without its ORDER BY is a different query: the subquery would
        # pick *some* N transactions, not the N just returned. A limited page is
        # small, so its ids go in as a list; the subquery form is for the
        # unbounded queries, where the list could run to thousands.
        if source is not None and source._limit_clause is not None:
            source = None
        if source is not None:
            ids_query = source.with_only_columns(schema.transactions.c.id).order_by(None)
            splits_query = splits_query.where(
                schema.splits.c.transaction_id.in_(ids_query)
            )
        else:
            splits_query = splits_query.where(
                schema.splits.c.transaction_id.in_([r.id for r in rows])
            )
        by_transaction: dict[UUID, list[Any]] = {r.id: [] for r in rows}
        for split in conn.execute(splits_query):
            by_transaction[split.transaction_id].append(split)
        registry = self._registry()
        return tuple(
            Rows.transaction_from_rows(row, by_transaction[row.id], registry)
            for row in rows
        )

    def posting_count(self, account_id: UUID) -> int:
        return int(
            self._scalar(
                select(func.count(func.distinct(schema.splits.c.transaction_id))).where(
                    schema.splits.c.book_id == self._book_id,
                    schema.splits.c.account_id == account_id,
                )
            )
        )

    def balances_by_account(
        self,
        *,
        account_ids: Iterable[UUID] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[UUID, int]:
        """One indexed aggregate, no transactions materialised.

        This is why ``post_date`` is denormalised onto ``splits``: account, date
        and the summed column are all in
        ``ix_splits_book_id_account_id_post_date``, so the query never touches
        the table or ``transactions`` at all.
        """
        s = schema.splits
        query = select(s.c.account_id, func.sum(s.c.quantity_minor)).where(
            s.c.book_id == self._book_id
        )
        if account_ids is not None:
            wanted = list(account_ids)
            if not wanted:
                return {}
            query = query.where(s.c.account_id.in_(wanted))
        if start is not None:
            query = query.where(s.c.post_date >= start)
        if end is not None:
            query = query.where(s.c.post_date <= end)
        rows = self._rows(query.group_by(s.c.account_id))
        return {account_id: int(total or 0) for account_id, total in rows}

    def find_imported(self, imported_id: str) -> UUID | None:
        return self._scalar(
            select(schema.transactions.c.id).where(
                schema.transactions.c.book_id == self._book_id,
                schema.transactions.c.imported_id == imported_id,
            )
        )

    def find_imported_prefix(self, prefix: str) -> str | None:
        # A range scan on the (book_id, imported_id) unique index.
        return self._scalar(
            select(schema.transactions.c.imported_id)
            .where(
                schema.transactions.c.book_id == self._book_id,
                schema.transactions.c.imported_id.like(f"{prefix}%"),
            )
            .order_by(schema.transactions.c.imported_id)
            .limit(1)
        )

    def transactions_for_payee(self, payee_id: UUID) -> tuple[Transaction, ...]:
        self._ensure_loaded()
        t, s = schema.transactions, schema.splits
        on_splits = select(s.c.transaction_id).where(
            s.c.book_id == self._book_id, s.c.payee_id == payee_id
        )
        query = (
            select(t)
            .where(
                t.c.book_id == self._book_id,
                (t.c.payee_id == payee_id) | t.c.id.in_(on_splits),
            )
            .order_by(t.c.post_date, t.c.id)
        )
        with self._read() as conn:
            return self._hydrate(conn, list(conn.execute(query)), query)

    def search(self, query: TransactionSearch) -> tuple[Transaction, ...]:
        """The predicate of :meth:`TransactionSearch.matches`, in SQL.

        Each criterion is one ``EXISTS`` over the splits (or a header test),
        so the planner can start from whichever is most selective -- the
        account index, usually -- and the transactions are hydrated as one
        page. Amounts are compared in integer arithmetic against the
        commodity's ``minor_unit_factor``: ``|minor| * den >= num * factor``
        is exact, where a float comparison would not be.
        """
        self._ensure_loaded()
        t, s, c = schema.transactions, schema.splits, schema.commodities
        conditions: list[Any] = [t.c.book_id == self._book_id]
        if query.start is not None:
            conditions.append(t.c.post_date >= query.start)
        if query.end is not None:
            conditions.append(t.c.post_date <= query.end)
        if query.account_ids is not None:
            wanted = list(query.account_ids)
            if not wanted:
                return ()
            conditions.append(
                exists().where(s.c.transaction_id == t.c.id, s.c.account_id.in_(wanted))
            )
        if query.payee_id is not None:
            conditions.append(
                or_(
                    t.c.payee_id == query.payee_id,
                    exists().where(
                        s.c.transaction_id == t.c.id, s.c.payee_id == query.payee_id
                    ),
                )
            )
        needle = (query.text or "").strip()
        if needle:
            # LIKE is case-insensitive on SQLite and case-sensitive on
            # PostgreSQL; lowering both sides makes the dialects agree. The
            # user's wildcards are literal characters, not patterns.
            escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped.casefold()}%"

            def like(column: Any) -> Any:
                return func.lower(column).like(pattern, escape="\\")

            conditions.append(
                or_(
                    like(t.c.memo),
                    like(t.c.number),
                    exists().where(
                        s.c.transaction_id == t.c.id,
                        or_(like(s.c.memo), like(s.c.number)),
                    ),
                )
            )
        if query.amount_min is not None or query.amount_max is not None:
            lo = query.amount_min if query.amount_min is not None else 0
            hi = query.amount_max
            # The two commodities whose minor_unit_factor scales the comparison
            # are *joined* inside the EXISTS, under aliases, rather than looked
            # up by scalar subqueries. A scalar subquery here would correlate
            # only against the EXISTS's own FROM (splits), not against the outer
            # `transactions` row two levels up -- so a subquery on the
            # transaction's currency came out as `FROM commodities, transactions`,
            # one row per transaction in the book. PostgreSQL refuses that
            # (CardinalityViolation); SQLite quietly takes the first row, which
            # is why only the PostgreSQL suite caught it.
            quantity_commodity = c.alias("quantity_commodity")
            currency = c.alias("transaction_currency")

            def within(minor: Any, factor: Any) -> Any:
                size = func.abs(minor)
                parts = [size * literal(lo.denominator) >= literal(lo.numerator) * factor]
                if hi is not None:
                    parts.append(
                        size * literal(hi.denominator) <= literal(hi.numerator) * factor
                    )
                return and_(*parts)

            conditions.append(
                exists().where(
                    s.c.transaction_id == t.c.id,
                    quantity_commodity.c.id == s.c.quantity_commodity_id,
                    currency.c.id == t.c.currency_id,
                    or_(
                        within(
                            s.c.quantity_minor, quantity_commodity.c.minor_unit_factor
                        ),
                        within(s.c.value_minor, currency.c.minor_unit_factor),
                    ),
                )
            )
        stmt = select(t).where(*conditions).order_by(t.c.post_date.desc(), t.c.id.desc())
        if query.limit is not None:
            stmt = stmt.limit(query.limit)
        with self._read() as conn:
            return self._hydrate(conn, list(conn.execute(stmt)), stmt)

    # ====================================================================
    # prices
    # ====================================================================
    @property
    def price_db(self) -> PriceDB:
        self._ensure_loaded()
        return self._prices

    # ====================================================================
    # change log
    # ====================================================================
    def append_changes(self, changes: Iterable[Change]) -> None:
        rows = [
            Rows.change_to_row(change, self._book_id, seq)
            for seq, change in enumerate(changes)
        ]
        if rows:
            self._conn.execute(schema.changes.insert(), rows)

    def _changes(self, *conditions: Any) -> tuple[Change, ...]:
        c = schema.changes
        query = (
            select(c)
            .where(c.c.book_id == self._book_id, *conditions)
            .order_by(c.c.revision, c.c.seq)
        )
        return tuple(Rows.change_from_row(r) for r in self._rows(query))

    def changes_since(
        self,
        revision: int,
        *,
        max_revisions: int | None = None,
        max_rows: int | None = None,
    ) -> tuple[Change, ...]:
        c = schema.changes
        if max_revisions is not None:
            # Two steps rather than one, because "the next N *revisions*" is not
            # a row limit: one revision can be any number of rows and must never
            # be returned half.
            wanted = [
                r.revision
                for r in self._rows(
                    select(c.c.revision)
                    .where(c.c.book_id == self._book_id, c.c.revision > revision)
                    .distinct()
                    .order_by(c.c.revision)
                    .limit(max_revisions)
                )
            ]
            if not wanted:
                return ()
            found = self._changes(c.c.revision.in_(wanted))
            if max_rows is not None and len(found) > max_rows:
                last = found[max_rows - 1].revision
                found = tuple(ch for ch in found if ch.revision <= last)
            return found
        if max_rows is None:
            return self._changes(c.c.revision > revision)
        # A row cap, then finish the revision it landed in: a page may overshoot
        # the cap, but it never ends in the middle of a command.
        query = (
            select(c)
            .where(c.c.book_id == self._book_id, c.c.revision > revision)
            .order_by(c.c.revision, c.c.seq)
            .limit(max_rows)
        )
        with self._read() as conn:
            rows = list(conn.execute(query))
            if len(rows) == max_rows:
                tail = rows[-1]
                rows.extend(
                    conn.execute(
                        select(c)
                        .where(
                            c.c.book_id == self._book_id,
                            c.c.revision == tail.revision,
                            c.c.seq > tail.seq,
                        )
                        .order_by(c.c.seq)
                    )
                )
        return tuple(Rows.change_from_row(r) for r in rows)

    def changes_at(self, revision: int) -> tuple[Change, ...]:
        return self._changes(schema.changes.c.revision == revision)

    def record_command(self, record: CommandRecord) -> None:
        self._conn.execute(
            schema.command_results.insert(),
            Rows.command_to_row(record, self._book_id, at=self._clock.now()),
        )

    def find_command(self, command_id: UUID) -> CommandRecord | None:
        c = schema.command_results
        rows = self._rows(
            select(c).where(c.c.book_id == self._book_id, c.c.command_id == command_id)
        )
        return Rows.command_from_row(rows[0]) if rows else None

    def changes_for_entity(self, entity_id: UUID) -> tuple[Change, ...]:
        return self._changes(schema.changes.c.entity_id == entity_id)

    def all_changes(self) -> tuple[Change, ...]:
        return self._changes()

    def change_count(self) -> int:
        return int(
            self._scalar(
                select(func.count())
                .select_from(schema.changes)
                .where(schema.changes.c.book_id == self._book_id)
            )
        )

    def __repr__(self) -> str:
        return (
            f"SqlStore({self._engine.url.render_as_string(hide_password=True)}, "
            f"book={self._book_id})"
        )
