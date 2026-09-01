"""Periodic fetching, inside the API process.

A daemon thread that wakes up every ``interval`` and runs the
:class:`~pymoney.quotes.service.QuoteService` over every book in the database.
Each book is taken through the :class:`~pymoney.api.registry.BookRegistry`, so
the run holds the same per-book lock a request would (decision #14: one writer)
-- but only for the two short steps that touch the book: planning, and storing
what came back. The network calls in between, which can take a minute per
provider or time out altogether, happen with the book free, so a user posting
a transaction at that moment waits a few milliseconds behind the plan or the
store, never behind the ECB.

Why a thread and not a separate process: the store's caches live in the API
process and assume they are the only writer. A cron job against the same
database would work -- ``python -m pymoney.quotes`` exists for exactly that,
when the API is *not* running -- but while it is, the fetch has to happen where
the caches are.

A tick never raises. A provider that is down is a problem in that book's report
(the API shows the last one); a bug is logged and the next tick still happens.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import timedelta
from typing import Any
from uuid import UUID

from ..core.book import Book
from .service import FetchReport, QuoteService

__all__ = ["QuoteScheduler"]

log = logging.getLogger("pymoney.quotes")


class QuoteScheduler:
    """Runs the quote service over every book, every ``interval``."""

    def __init__(
        self,
        service: QuoteService,
        *,
        book_ids: Callable[[], Iterable[UUID]],
        open_book: Callable[[UUID], AbstractContextManager[Book]],
        interval: timedelta,
        initial_delay: timedelta = timedelta(seconds=30),
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("the interval must be positive")
        self.service = service
        self._book_ids = book_ids
        self._open_book = open_book
        self.interval = interval
        self.initial_delay = initial_delay
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticks = 0

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="pymoney-quotes", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        # The first tick waits a little: the server has just started, and the
        # first thing it should do is serve requests, not call the ECB.
        if self._stop.wait(self.initial_delay.total_seconds()):
            return
        while not self._stop.is_set():
            self.tick()
            if self._stop.wait(self.interval.total_seconds()):
                return

    # -- one round -----------------------------------------------------------
    def tick(self) -> dict[UUID, FetchReport]:
        """Fetch for every book once. Returns the reports; never raises."""
        reports: dict[UUID, FetchReport] = {}
        try:
            ids = list(self._book_ids())
        except Exception:
            log.exception("quote scheduler could not list the books")
            return reports
        for book_id in ids:
            if self._stop.is_set():
                break
            try:
                with self._open_book(book_id) as book:
                    if book.base_currency_id is None:
                        continue
                    prepared = self.service.prepare(book)
                # The lock is released here: the slow part runs against nobody.
                self.service.fetch(prepared)
                with self._open_book(book_id) as book:
                    report = self.service.store(book, prepared)
                reports[book_id] = report
                if report.problems:
                    log.warning("quotes for book %s: %s", book_id, report.summary())
                elif report.stored:
                    log.info("quotes for book %s: %s", book_id, report.summary())
            except Exception:
                log.exception("quote scheduler failed for book %s", book_id)
        self.ticks += 1
        return reports

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        state: Any = "running" if self.running else "stopped"
        return f"QuoteScheduler({state}, every {self.interval})"
