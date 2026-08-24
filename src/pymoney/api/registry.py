"""Open books, and the one-writer rule (decision #14).

A :class:`~pymoney.store.sql.SqlStore` caches the small tables and assumes it is
the only writer. That is not a limitation to work around here -- it is the
architecture, and the API has to honour it:

* **One Book object per book id, for the life of the process.** Opening a fresh
  one per request would reload the caches every time, and would also lose the
  idempotency cache that makes ``Idempotency-Key`` work.
* **One lock per book, held across every access.** The store holds a single
  database connection, which is not safe to share between threads, and FastAPI
  runs synchronous endpoints in a thread pool. The lock covers reads as well as
  writes for that reason.

The cost is explicit: requests against the *same* book are serialised. Different
books run concurrently. For a personal finance server that is the right trade --
commands take single-digit milliseconds and correctness is not negotiable -- and
the place to revisit it is a read replica or a per-request read connection, not
a weaker lock.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Engine, select

from ..core.book import Book
from ..core.clock import Clock, SystemClock
from ..core.commodity import Commodity
from ..core.errors import NotFoundError
from ..store import schema
from ..store.sql import SqlStore

__all__ = ["BookRegistry"]


class BookRegistry:
    """Keeps one open :class:`Book` per book id, guarded by a per-book lock."""

    def __init__(self, engine: Engine, *, clock: Clock | None = None) -> None:
        self._engine = engine
        self._clock = clock or SystemClock()
        self._books: dict[UUID, Book] = {}
        self._locks: dict[UUID, threading.RLock] = {}
        self._guard = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        with self._guard:
            for book in self._books.values():
                book.close()
            self._books.clear()
            self._locks.clear()

    def _lock_for(self, book_id: UUID) -> threading.RLock:
        with self._guard:
            lock = self._locks.get(book_id)
            if lock is None:
                lock = self._locks[book_id] = threading.RLock()
            return lock

    # -- access ------------------------------------------------------------
    def known_ids(self) -> tuple[UUID, ...]:
        """Every book in the database, whether currently open or not."""
        with self._engine.connect() as conn:
            return tuple(conn.execute(select(schema.books.c.id)).scalars())

    def exists(self, book_id: UUID) -> bool:
        with self._engine.connect() as conn:
            found = conn.execute(
                select(schema.books.c.id).where(schema.books.c.id == book_id)
            ).scalar_one_or_none()
        return found is not None

    @contextmanager
    def open(self, book_id: UUID) -> Iterator[Book]:
        """Borrow a book, exclusively, for the duration of the block."""
        if not self.exists(book_id):
            raise NotFoundError(f"no book {book_id}")
        lock = self._lock_for(book_id)
        with lock:
            book = self._books.get(book_id)
            if book is None:
                book = Book(
                    id=book_id,
                    clock=self._clock,
                    store=SqlStore(self._engine, book_id, clock=self._clock),
                )
                self._books[book_id] = book
            yield book

    def create(
        self,
        *,
        name: str,
        base_currency: Commodity,
        book_id: UUID | None = None,
        actor: str = "",
    ) -> Book:
        """Create a new book and keep it open."""
        from ..core.ids import new_id

        book_id = book_id or new_id()
        lock = self._lock_for(book_id)
        with lock:
            if self.exists(book_id):
                from ..core.errors import ConflictError

                raise ConflictError(f"book {book_id} already exists")
            book = Book(
                id=book_id,
                name=name,
                base_currency=base_currency,
                clock=self._clock,
                actor=actor,
                store=SqlStore(self._engine, book_id, clock=self._clock),
            )
            self._books[book_id] = book
            return book

    def forget(self, book_id: UUID) -> None:
        """Drop a book from the cache, closing its connection."""
        with self._guard:
            book = self._books.pop(book_id, None)
            self._locks.pop(book_id, None)
        if book is not None:
            book.close()

    def __repr__(self) -> str:
        return f"BookRegistry({len(self._books)} open)"
