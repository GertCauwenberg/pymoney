"""Engine creation, and the per-dialect settings that make it behave.

SQLite's defaults are wrong for a ledger and have to be corrected explicitly:

``foreign_keys=ON``
    Off by default, per connection. Without it the schema's foreign keys are
    decoration.

``journal_mode=WAL``
    Readers no longer block the writer. A desktop app reporting while a sync
    writes is the normal case, not an edge case.

``synchronous=NORMAL``
    With WAL this is durable against process crashes (just not against power
    loss mid-write), and an order of magnitude faster on commit. Accounting data
    deserves a deliberate choice here rather than a default; this is the
    deliberate choice, and :func:`create_engine` takes ``synchronous=`` for
    anyone who wants ``FULL``.

``busy_timeout``
    Wait for a lock instead of failing instantly.

PostgreSQL needs none of this, which is the point of keeping it in one place.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, event, select
from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy.pool import StaticPool

from ..core.book import Book
from ..core.clock import Clock
from ..core.commodity import Commodity
from ..core.ids import new_id
from . import schema
from .connections import WRITE_OPTION, write_connection, write_transaction
from .sql import SqlStore

__all__ = [
    "DEFAULT_URL_ENV",
    "WRITE_OPTION",
    "create_engine",
    "create_schema",
    "drop_schema",
    "open_book",
    "session",
    "write_connection",
    "write_transaction",
]

DEFAULT_URL_ENV = "PYMONEY_DATABASE_URL"


def create_engine(
    url: str | None = None,
    *,
    synchronous: str = "NORMAL",
    echo: bool = False,
    **kwargs: Any,
) -> Engine:
    """Build an engine with the right settings for the dialect.

    :param url: defaults to ``$PYMONEY_DATABASE_URL``, then to an in-memory
        SQLite database.
    """
    url = url or os.environ.get(DEFAULT_URL_ENV) or "sqlite+pysqlite:///:memory:"
    is_sqlite = url.startswith("sqlite")
    is_memory = is_sqlite and (":memory:" in url or "mode=memory" in url)

    if is_memory:
        # Every connection to ":memory:" gets its own *database*, so a pool
        # would hand out a different (empty) one on reconnect.
        kwargs.setdefault("poolclass", StaticPool)
        kwargs.setdefault("connect_args", {"check_same_thread": False})

    engine = _sa_create_engine(url, echo=echo, future=True, **kwargs)

    if is_sqlite:
        _configure_sqlite(engine, synchronous=synchronous, is_memory=is_memory)
    return engine


def _configure_sqlite(engine: Engine, *, synchronous: str, is_memory: bool) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        # Take transaction control away from the driver. pysqlite decides on its
        # own when to emit BEGIN, and it does not do so for a SAVEPOINT -- so a
        # savepoint ends up outside any transaction and rolling back to it does
        # nothing at all. Silently. We rely on savepoints for per-command
        # isolation inside book.bulk(), so this is not optional.
        #
        # The recipe is SQLAlchemy's own: disable the implicit BEGIN here, emit
        # it ourselves below. It also restores transactional DDL.
        dbapi_connection.isolation_level = None

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        if not is_memory:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA synchronous={synchronous}")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection: Any) -> None:
        # A transaction that knows it will write says so. In WAL mode a
        # deferred transaction that reads first and writes later fails with
        # SQLITE_BUSY_SNAPSHOT the moment another connection has committed in
        # between -- and busy_timeout does not retry that. BEGIN IMMEDIATE takes
        # the writer's lock up front, so concurrent writers queue (for up to
        # busy_timeout) instead of failing. Readers stay deferred and never wait.
        if connection.get_execution_options().get(WRITE_OPTION):
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.exec_driver_sql("BEGIN")


def create_schema(engine: Engine) -> None:
    """Create every table and index. Alembic is the supported path; this is for
    tests and throwaway databases."""
    schema.metadata.create_all(engine)


def drop_schema(engine: Engine) -> None:
    schema.metadata.drop_all(engine)


def open_book(
    engine: Engine,
    *,
    book_id: UUID | None = None,
    name: str = "",
    base_currency: Commodity | None = None,
    clock: Clock | None = None,
) -> Book:
    """Open or create a book in ``engine``.

    With no ``book_id``, an existing single book is reopened and a new one is
    created if the database is empty. Opening the wrong book by accident is a
    worse failure than refusing to guess, so more than one is an error.
    """
    if book_id is None:
        existing: list[UUID]
        with engine.connect() as conn:
            existing = list(conn.execute(select(schema.books.c.id)).scalars())
        if len(existing) > 1:
            raise ValueError(
                f"this database holds {len(existing)} books; pass book_id to say "
                f"which one: {', '.join(str(i) for i in sorted(existing))}"
            )
        book_id = existing[0] if existing else new_id()

    store = SqlStore(engine, book_id, clock=clock)
    return Book(
        id=book_id,
        name=name,
        base_currency=base_currency,
        clock=clock,
        store=store,
    )


@contextmanager
def session(
    url: str | None = None,
    *,
    create: bool = False,
    book_id: UUID | None = None,
    name: str = "",
    base_currency: Commodity | None = None,
    clock: Clock | None = None,
    **engine_kwargs: Any,
) -> Iterator[Book]:
    """Open a book, yield it, and close everything afterwards.

    :param create: create the schema first. Convenient for scripts and tests;
        use Alembic for anything you intend to keep.
    """
    engine = create_engine(url, **engine_kwargs)
    try:
        if create:
            create_schema(engine)
        book = open_book(
            engine,
            book_id=book_id,
            name=name,
            base_currency=base_currency,
            clock=clock,
        )
        try:
            yield book
        finally:
            book.close()
    finally:
        engine.dispose()
