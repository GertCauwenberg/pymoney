"""Fetching quotes into a book: planning, de-duplication, storage, reporting.

The service is what the scheduler, the API's "fetch now" and the command line
all call. One run, for one book:

1. **Plan.** Every commodity with a ``quote_source`` (other than the base
   currency itself) becomes one request to that provider, for the days not yet
   covered: from the day after the newest price that provider has given for the
   commodity, to today. A first run looks back a week; a backfill names its own
   range. A commodity naming a provider that is not installed is reported, not
   skipped silently.
2. **Fetch.** Each provider is asked once per commodity. A provider failing is
   one problem in the report; the other providers still run.
3. **Store.** Each quote becomes a :class:`~pymoney.core.price.Price` with
   ``source`` = the provider's name and ``as_of`` = the instant it was fetched
   (decision #9: ``as_of`` is knowledge time, so it *is* ``fetched_at``), rate
   kept as the exact rational the provider built from its decimal text. A quote
   identical to one already stored for that day and source is skipped; a
   different one -- a correction -- is a new row, never an edit. A price the
   user typed in is never touched: a fetched quote lands beside it, and the
   valuation policy ranks the user's above it for that day.

Everything happens inside one ``book.bulk()`` unit of work, so a run is
all-or-nothing against the database while each price still gets its own
revision, and under whatever lock the caller holds on the book -- the scheduler
takes the registry's per-book lock, exactly as a request would.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID

from ..core import commands as cmd
from ..core.book import Book
from ..core.clock import Clock, SystemClock
from ..core.commodity import Commodity
from ..core.errors import PyMoneyError
from .provider import Http, Quote, QuoteError, QuoteProvider, QuoteRequest, urllib_http
from .registry import ProviderRegistry

__all__ = ["FetchProblem", "FetchReport", "PlannedFetch", "QuoteService"]

DEFAULT_LOOKBACK_DAYS = 7


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedFetch:
    provider: QuoteProvider
    request: QuoteRequest


@dataclass(frozen=True, slots=True, kw_only=True)
class FetchProblem:
    provider: str
    commodity: str
    message: str

    def __str__(self) -> str:
        return f"{self.provider} / {self.commodity}: {self.message}"


@dataclass(slots=True, kw_only=True)
class FetchReport:
    """What one run did. Counts are of quotes, problems are per request."""

    book_id: UUID
    started_at: datetime
    finished_at: datetime | None = None
    start: date | None = None
    end: date | None = None
    requested: int = 0
    """Commodities a provider was asked about."""
    up_to_date: int = 0
    """Commodities with nothing new to ask for."""
    fetched: int = 0
    stored: int = 0
    unchanged: int = 0
    problems: list[FetchProblem] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        when = (
            f"{self.start}..{self.end}" if self.start and self.end else "nothing to fetch"
        )
        line = (
            f"{when}: asked about {self.requested} commodit{'y' if self.requested == 1 else 'ies'}, "
            f"{self.fetched} quote(s) received, {self.stored} stored, {self.unchanged} already known"
        )
        if self.up_to_date:
            line += f", {self.up_to_date} up to date"
        if self.problems:
            line += f"; {len(self.problems)} problem(s):\n" + "\n".join(
                f"  {p}" for p in self.problems
            )
        return line


class QuoteService:
    """Fetches quotes into books. One instance per server; holds the last report per book."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        http: Http = urllib_http,
        clock: Clock | None = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self.registry = registry
        self.http = http
        self.clock: Clock = clock or SystemClock()
        self.lookback_days = lookback_days
        self.last_runs: dict[UUID, FetchReport] = {}

    # -- planning ----------------------------------------------------------
    def plan(
        self,
        book: Book,
        *,
        start: date | None = None,
        end: date | None = None,
        commodity_ids: Iterable[UUID] | None = None,
        providers: Iterable[str] | None = None,
        problems: list[FetchProblem] | None = None,
    ) -> tuple[list[PlannedFetch], int]:
        """What to ask whom. Returns the requests and how many commodities were
        already up to date (nothing to ask for)."""
        wanted_ids = None if commodity_ids is None else set(commodity_ids)
        wanted_providers = None if providers is None else set(providers)
        today = self.clock.today()
        end = end or today
        base = book.base_currency
        planned: list[PlannedFetch] = []
        up_to_date = 0

        for commodity in book.commodities():
            if not commodity.quote_source or commodity.id == base.id:
                continue
            if wanted_ids is not None and commodity.id not in wanted_ids:
                continue
            if (
                wanted_providers is not None
                and commodity.quote_source not in wanted_providers
            ):
                continue
            provider = self.registry.get(commodity.quote_source)
            if provider is None:
                if problems is not None:
                    problems.append(
                        FetchProblem(
                            provider=commodity.quote_source,
                            commodity=commodity.code,
                            message="no such quote provider is installed",
                        )
                    )
                continue
            first = start
            if first is None:
                newest = self._newest(book, commodity, provider.name)
                first = (
                    newest + timedelta(days=1)
                    if newest
                    else today - timedelta(days=self.lookback_days)
                )
            if first > end:
                up_to_date += 1
                continue
            planned.append(
                PlannedFetch(
                    provider=provider,
                    request=QuoteRequest(
                        commodity=commodity, currency=base, start=first, end=end
                    ),
                )
            )
        return planned, up_to_date

    @staticmethod
    def _newest(book: Book, commodity: Commodity, source: str) -> date | None:
        dates = [
            p.effective_date
            for p in book.prices.all()
            if p.source == source and commodity.id in (p.base.id, p.quote.id)
        ]
        return max(dates) if dates else None

    # -- running -----------------------------------------------------------
    def run(
        self,
        book: Book,
        *,
        start: date | None = None,
        end: date | None = None,
        commodity_ids: Iterable[UUID] | None = None,
        providers: Iterable[str] | None = None,
        fetch: Callable[[QuoteProvider, QuoteRequest], list[Quote]] | None = None,
    ) -> FetchReport:
        """Fetch and store everything the plan asks for. Never raises for a
        provider's failure; the report says what went wrong."""
        report = FetchReport(book_id=book.id, started_at=self.clock.now())
        planned, report.up_to_date = self.plan(
            book,
            start=start,
            end=end,
            commodity_ids=commodity_ids,
            providers=providers,
            problems=report.problems,
        )
        report.requested = len(planned)
        if planned:
            report.start = min(p.request.start for p in planned)
            report.end = max(p.request.end for p in planned)
        report.providers = sorted({p.provider.name for p in planned})
        do_fetch = fetch or (lambda provider, request: provider.fetch(request, self.http))

        results: list[tuple[PlannedFetch, list[Quote]]] = []
        for item in planned:
            try:
                quotes = do_fetch(item.provider, item.request)
            except QuoteError as exc:
                report.problems.append(
                    FetchProblem(
                        provider=item.provider.name,
                        commodity=item.request.commodity.code,
                        message=str(exc),
                    )
                )
                continue
            except Exception as exc:
                report.problems.append(
                    FetchProblem(
                        provider=item.provider.name,
                        commodity=item.request.commodity.code,
                        message=f"provider failed: {type(exc).__name__}: {exc}",
                    )
                )
                continue
            report.fetched += len(quotes)
            results.append((item, quotes))

        with book.bulk():
            for item, quotes in results:
                self._store(book, item, quotes, report)

        report.finished_at = self.clock.now()
        self.last_runs[book.id] = report
        return report

    def _store(
        self, book: Book, item: PlannedFetch, quotes: list[Quote], report: FetchReport
    ) -> None:
        provider = item.provider.name
        commodity_code = item.request.commodity.code
        unplaceable: set[str] = set()
        known: dict[tuple[UUID, UUID], dict[date, Quote | None]] = {}

        for quote in quotes:
            base = book.find_commodity(quote.base_code)
            counter = book.find_commodity(quote.quote_code)
            if base is None or counter is None:
                missing = quote.base_code if base is None else quote.quote_code
                if missing not in unplaceable:
                    unplaceable.add(missing)
                    report.problems.append(
                        FetchProblem(
                            provider=provider,
                            commodity=commodity_code,
                            message=(
                                f"quotes are in {missing}, which this book has no commodity for; "
                                f"add {missing} to store them"
                            ),
                        )
                    )
                continue
            if base.id == counter.id:
                continue

            pair = (base.id, counter.id)
            if pair not in known:
                stored = book.prices.history(base, counter, include_inverse=False)
                latest: dict[date, Quote | None] = {}
                for price in stored:
                    if price.source == provider:
                        # history() is ordered by as_of within a date, so the
                        # last one seen is the newest statement for that day.
                        latest[price.effective_date] = Quote(
                            base_code=base.code,
                            quote_code=counter.code,
                            effective_date=price.effective_date,
                            rate=price.rate,
                            raw="",
                        )
                known[pair] = latest
            previous = known[pair].get(quote.effective_date)
            if previous is not None and previous.rate == quote.rate:
                report.unchanged += 1
                continue
            try:
                book.execute(
                    cmd.AddPrice(
                        base_id=base.id,
                        quote_id=counter.id,
                        rate=quote.rate,
                        effective_date=quote.effective_date,
                        as_of=self.clock.now(),
                        source=provider,
                    )
                )
            except (PyMoneyError, ValueError) as exc:
                report.problems.append(
                    FetchProblem(
                        provider=provider,
                        commodity=commodity_code,
                        message=f"{quote.effective_date} {quote.raw or quote.rate}: {exc}",
                    )
                )
                continue
            known[pair][quote.effective_date] = quote
            report.stored += 1
