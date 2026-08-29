"""Persistence for PyMoney: SQLAlchemy over SQLite and PostgreSQL.

The domain does not know this package exists. :class:`SqlStore` implements
:class:`pymoney.core.store.Store`, so the same
:class:`~pymoney.core.book.Book` -- the same invariants, the same commands, the
same test suite -- runs over memory or over a database.

    from pymoney.core import AccountType, Commodity
    from pymoney.store import create_engine, create_schema, open_book

    engine = create_engine("sqlite+pysqlite:///ledger.sqlite")
    create_schema(engine)                      # or: alembic upgrade head
    book = open_book(engine, name="Household",
                     base_currency=Commodity.currency("EUR"))
    book.add_account("Cash", AccountType.CASH)
"""

from __future__ import annotations

# Imported for its side effect: the identity tables must be attached to the same
# MetaData before Alembic autogenerates anything, or a migration would drop them.
from . import identity as identity
from .engine import (
    DEFAULT_URL_ENV,
    create_engine,
    create_schema,
    drop_schema,
    open_book,
    session,
    write_connection,
    write_transaction,
)
from .mapper import Registry, Rows
from .schema import ALL_TABLES, metadata
from .sql import SqlStore
from .types import JsonList, Uuid

__all__ = [
    "ALL_TABLES",
    "DEFAULT_URL_ENV",
    "JsonList",
    "Registry",
    "Rows",
    "SqlStore",
    "Uuid",
    "create_engine",
    "create_schema",
    "drop_schema",
    "identity",
    "metadata",
    "open_book",
    "session",
    "write_connection",
    "write_transaction",
]
