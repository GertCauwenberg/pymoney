"""Connections that know they will write.

In SQLite's WAL mode a deferred transaction that reads first and writes later
fails with ``SQLITE_BUSY_SNAPSHOT`` the moment another connection has committed
in between -- and ``busy_timeout`` does not retry that. A transaction that
declares itself a writer (``BEGIN IMMEDIATE``) takes the writer's lock up front,
so concurrent writers queue instead of failing. Readers stay deferred and never
wait. The engine's ``begin`` listener (see :mod:`pymoney.store.engine`) looks for
the execution option set here; on PostgreSQL the option changes nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine

__all__ = ["WRITE_OPTION", "write_connection", "write_transaction"]

WRITE_OPTION = "pymoney_write"


def write_connection(engine: Engine) -> Any:
    """A connection whose transactions begin as writers.

    ``with write_connection(engine) as conn, conn.begin(): ...``
    """
    return engine.connect().execution_options(**{WRITE_OPTION: True})


@contextmanager
def write_transaction(engine: Engine) -> Iterator[Any]:
    """``engine.begin()`` for a transaction that will write."""
    with write_connection(engine) as conn, conn.begin():
        yield conn
