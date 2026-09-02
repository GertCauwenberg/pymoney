"""Quote providers, the fetching service and the scheduler."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

import pytest

from pymoney.api.testing import SCRIPTED, FakeProvider, no_http
from pymoney.core import (
    AccountType,
    Book,
    Commodity,
    CommodityKind,
    ManualClock,
    PriceSource,
    ValuationPolicy,
)
from pymoney.quotes import (
    EcbProvider,
    ProviderRegistry,
    Quote,
    QuoteError,
    QuoteProvider,
    QuoteRequest,
    QuoteScheduler,
    QuoteService,
    YahooProvider,
)
from pymoney.quotes.__main__ import main as quotes_main
from tests.conftest import Chart

F = Fraction
TODAY = date(2026, 1, 1)


# ==========================================================================
# helpers
# ==========================================================================
def quote(base: str, counter: str, on: date, rate: str) -> Quote:
    return Quote(
        base_code=base, quote_code=counter, effective_date=on, rate=F(rate), raw=rate
    )


class BrokenPlugin:
    """Not a provider at all."""


@pytest.fixture
def fake() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def service(fake: FakeProvider, clock: ManualClock) -> QuoteService:
    return QuoteService(ProviderRegistry([fake]), http=no_http, clock=clock)


@pytest.fixture
def priced_chart(chart: Chart) -> Chart:
    """USD and AAPL set to fetch from the fake provider."""
    from pymoney.core import commands as c

    chart.book.execute(c.UpdateCommodity(commodity_id=chart.usd.id, quote_source="fake"))
    return chart


# ==========================================================================
# providers
# ==========================================================================
ECB_CSV = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-01-02,1.0842,A
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-01-01,,H
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-01-05,1.09,A
"""


class TestEcb:
    def test_parses_exact_decimals_and_skips_holidays(self, eur):
        usd = Commodity.currency("USD")
        calls = []

        def http(url, headers):
            calls.append(url)
            return ECB_CSV.encode()

        quotes = EcbProvider().fetch(
            QuoteRequest(
                commodity=usd, currency=eur, start=date(2026, 1, 1), end=date(2026, 1, 5)
            ),
            http,
        )
        assert calls == [
            "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
            "?format=csvdata&startPeriod=2026-01-01&endPeriod=2026-01-05"
        ]
        assert [(q.effective_date, q.rate, q.raw) for q in quotes] == [
            (date(2026, 1, 2), F(10842, 10000), "1.0842"),
            (date(2026, 1, 5), F(109, 100), "1.09"),
        ]
        # 1 EUR = 1.0842 USD: the ECB's direction is kept, the engine inverts.
        assert (quotes[0].base_code, quotes[0].quote_code) == ("EUR", "USD")

    def test_the_euro_itself_is_priced_in_the_book_currency(self, eur):
        gbp = Commodity.currency("GBP")
        urls = []

        def http(url, headers):
            urls.append(url)
            return b""

        assert (
            EcbProvider().fetch(
                QuoteRequest(commodity=eur, currency=gbp, start=TODAY, end=TODAY), http
            )
            == []
        )
        assert "D.GBP.EUR.SP00.A" in urls[0]

    def test_refuses_what_it_cannot_quote(self, eur):
        with pytest.raises(QuoteError, match="ISO 4217"):
            EcbProvider().fetch(
                QuoteRequest(
                    commodity=Commodity.security("AAPL"),
                    currency=eur,
                    start=TODAY,
                    end=TODAY,
                ),
                no_http,
            )
        with pytest.raises(QuoteError, match="not a rate"):
            EcbProvider().fetch(
                QuoteRequest(
                    commodity=eur,
                    currency=Commodity.currency("EUR"),
                    start=TODAY,
                    end=TODAY,
                ),
                no_http,
            )

    def test_a_changed_csv_shape_is_an_error_not_silence(self):
        with pytest.raises(QuoteError, match="TIME_PERIOD"):
            EcbProvider._parse("A,B\n1,2\n", "USD")
        with pytest.raises(QuoteError, match="unreadable"):
            EcbProvider._parse("TIME_PERIOD,OBS_VALUE\n2026-01-01,abc\n", "USD")


def yahoo_body(**overrides):
    body = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "priceHint": 2,
                        "exchangeTimezoneName": "America/New_York",
                    },
                    # 2026-01-02 21:00 UTC is 16:00 in New York, the same day;
                    # 2026-01-06 02:00 UTC is still the 5th in New York.
                    "timestamp": [1767387600, 1767668400, 1767668401],
                    "indicators": {
                        "quote": [{"close": [417.8800048828125, None, 420.1]}]
                    },
                }
            ],
            "error": None,
        }
    }
    body["chart"].update(overrides)
    return json.dumps(body).encode()


class TestYahoo:
    def test_rounds_floats_to_the_declared_precision_and_uses_exchange_dates(self, eur):
        msft = Commodity.security("MSFT")
        urls = []

        def http(url, headers):
            urls.append(url)
            assert "User-Agent" in headers
            return yahoo_body()

        quotes = YahooProvider().fetch(
            QuoteRequest(
                commodity=msft, currency=eur, start=date(2026, 1, 1), end=date(2026, 1, 6)
            ),
            http,
        )
        assert urls[0].startswith(
            "https://query1.finance.yahoo.com/v8/finance/chart/MSFT?period1="
        )
        assert [(q.effective_date, q.raw, q.rate) for q in quotes] == [
            (date(2026, 1, 2), "417.88", F(41788, 100)),
            (date(2026, 1, 5), "420.10", F(4201, 10)),
        ]
        assert (quotes[0].base_code, quotes[0].quote_code) == ("MSFT", "USD")

    def test_yahoos_own_error_is_reported(self, eur):
        body = yahoo_body(
            result=None,
            error={
                "code": "Not Found",
                "description": "No data found, symbol may be delisted",
            },
        )
        with pytest.raises(QuoteError, match="delisted"):
            YahooProvider().fetch(
                QuoteRequest(
                    commodity=Commodity.security("NOPE"),
                    currency=eur,
                    start=TODAY,
                    end=TODAY,
                ),
                lambda url, headers: body,
            )

    def test_garbage_is_an_error(self, eur):
        request = QuoteRequest(
            commodity=Commodity.security("MSFT"), currency=eur, start=TODAY, end=TODAY
        )
        with pytest.raises(QuoteError, match="not JSON"):
            YahooProvider().fetch(request, lambda url, headers: b"<html>")
        with pytest.raises(QuoteError, match="no chart"):
            YahooProvider().fetch(request, lambda url, headers: b"{}")
        with pytest.raises(QuoteError, match="which currency"):
            YahooProvider._parse({"chart": {"result": [{"meta": {}}]}}, "MSFT")

    def test_an_empty_result_is_no_quotes(self):
        assert YahooProvider._parse({"chart": {"result": []}}, "MSFT") == []


# ==========================================================================
# registry
# ==========================================================================
class TestRegistry:
    def test_the_builtins(self):
        registry = ProviderRegistry.builtin()
        assert registry.names == ("ecb", "yahoo")
        assert isinstance(registry.get("ecb"), EcbProvider)
        assert "yahoo" in registry and "nasdaq" not in registry

    def test_plugins_by_dotted_reference(self):
        registry = ProviderRegistry.discover(["pymoney.api.testing:FakeProvider"])
        assert "fake" in registry.names
        assert registry.problems == []

    def test_a_broken_plugin_is_reported_not_fatal(self):
        registry = ProviderRegistry.discover(
            ["tests.test_quotes:BrokenPlugin", "no.such.module:X", "tests.test_quotes"]
        )
        assert registry.names == ("ecb", "yahoo")
        assert len(registry.problems) == 3
        assert "not a QuoteProvider" in registry.problems[0]

    def test_names_must_be_usable_as_sources(self):
        class Bad(QuoteProvider):
            name = "My Broker"

            def fetch(self, request, http):  # pragma: no cover
                return []

        with pytest.raises(ValueError, match="lower-case"):
            ProviderRegistry([Bad()])
        with pytest.raises(ValueError, match="already"):
            ProviderRegistry([FakeProvider(), FakeProvider()])


# ==========================================================================
# service
# ==========================================================================
class TestPlanning:
    def test_only_commodities_with_a_source_other_than_the_base(
        self, priced_chart: Chart, service, fake
    ):
        planned, up_to_date = service.plan(priced_chart.book)
        assert [p.request.commodity.code for p in planned] == ["USD"]
        assert up_to_date == 0
        request = planned[0].request
        assert request.currency.code == "EUR"
        # First run: a week back, up to today.
        assert (request.start, request.end) == (TODAY - timedelta(days=7), TODAY)

    def test_continues_from_the_newest_price_that_provider_gave(
        self, priced_chart: Chart, service, fake
    ):
        book = priced_chart.book
        book.add_price(
            priced_chart.usd,
            book.base_currency,
            "0.9",
            effective_date=date(2025, 12, 30),
            source="fake",
        )
        # A user price does not count: it says nothing about what the provider has.
        book.add_price(
            priced_chart.usd,
            book.base_currency,
            "0.91",
            effective_date=date(2025, 12, 31),
            source="user",
        )
        planned, _ = service.plan(book)
        assert planned[0].request.start == date(2025, 12, 31)

    def test_up_to_date_means_nothing_to_ask(self, priced_chart: Chart, service):
        book = priced_chart.book
        book.add_price(
            priced_chart.usd,
            book.base_currency,
            "0.9",
            effective_date=TODAY,
            source="fake",
        )
        planned, up_to_date = service.plan(book)
        assert planned == [] and up_to_date == 1

    def test_an_unknown_provider_is_a_problem(self, chart: Chart, service):
        from pymoney.core import commands as c

        chart.book.execute(
            c.UpdateCommodity(commodity_id=chart.usd.id, quote_source="bloomberg")
        )
        problems: list = []
        planned, _ = service.plan(chart.book, problems=problems)
        assert planned == []
        assert [str(p) for p in problems] == [
            "bloomberg / USD: no such quote provider is installed"
        ]

    def test_backfill_names_its_own_range(self, priced_chart: Chart, service):
        planned, _ = service.plan(
            priced_chart.book, start=date(2020, 1, 1), end=date(2020, 12, 31)
        )
        assert (planned[0].request.start, planned[0].request.end) == (
            date(2020, 1, 1),
            date(2020, 12, 31),
        )

    def test_a_changed_quote_source_is_honoured_at_the_next_run(
        self, priced_chart: Chart, clock
    ):
        """The plan is drawn from the commodities as they are *now*: a provider
        switch, or switching fetching off, takes effect at the next tick with no
        restart and no stale request to the old provider."""
        from pymoney.core import commands as c

        book = priced_chart.book
        first, second = FakeProvider(), FakeProvider()
        second.name = "other"
        first.table["USD"] = [quote("EUR", "USD", date(2025, 12, 30), "1.09")]
        second.table["USD"] = [quote("EUR", "USD", date(2025, 12, 31), "1.10")]
        service = QuoteService(
            ProviderRegistry([first, second]), http=no_http, clock=clock
        )

        service.run(book)
        assert len(first.requests) == 1 and second.requests == []

        # Switch USD to the other provider.
        book.execute(
            c.UpdateCommodity(commodity_id=priced_chart.usd.id, quote_source="other")
        )
        report = service.run(book)
        assert len(first.requests) == 1  # the old provider is not asked again
        assert len(second.requests) == 1
        # The new provider starts from its own history (none): the lookback,
        # not the day after the *old* provider's last quote.
        assert second.requests[0].start == clock.today() - timedelta(days=7)
        assert report.stored == 1 and report.providers == ["other"]
        stored = book.prices.history(
            book.base_currency, priced_chart.usd, include_inverse=False
        )
        assert [(p.effective_date, p.source) for p in stored] == [
            (date(2025, 12, 30), "fake"),
            (date(2025, 12, 31), "other"),
        ]

        # Switch fetching off: nothing is asked, nothing already stored is touched.
        book.execute(
            c.UpdateCommodity(commodity_id=priced_chart.usd.id, quote_source=None)
        )
        report = service.run(book)
        assert report.requested == 0 and report.up_to_date == 0
        assert len(second.requests) == 1
        assert (
            len(
                book.prices.history(
                    book.base_currency, priced_chart.usd, include_inverse=False
                )
            )
            == 2
        )

        # And on again, a week later: it resumes from that provider's newest quote.
        clock.advance(days=7)
        book.execute(
            c.UpdateCommodity(commodity_id=priced_chart.usd.id, quote_source="other")
        )
        service.run(book)
        assert second.requests[-1].start == date(2026, 1, 1)

    def test_filters_by_commodity_and_provider(self, priced_chart: Chart, service):
        book = priced_chart.book
        assert service.plan(book, commodity_ids=[priced_chart.aapl.id])[0] == []
        assert service.plan(book, providers=["yahoo"])[0] == []
        assert len(service.plan(book, providers=["fake"])[0]) == 1


class TestRunning:
    def test_stores_quotes_with_the_provider_as_source_and_fetched_at_as_as_of(
        self, priced_chart: Chart, service, fake, clock
    ):
        book = priced_chart.book
        fake.table["USD"] = [
            quote("EUR", "USD", date(2025, 12, 29), "1.0842"),
            quote("EUR", "USD", date(2025, 12, 30), "1.09"),
        ]
        before = book.revision
        report = service.run(book)
        assert report.ok, report.summary()
        assert (report.requested, report.fetched, report.stored, report.unchanged) == (
            1,
            2,
            2,
            0,
        )
        assert report.providers == ["fake"]
        assert book.revision == before + 2  # one revision per price, one unit of work
        stored = book.prices.history(
            book.base_currency, priced_chart.usd, include_inverse=False
        )
        assert [(p.effective_date, p.rate, p.source, p.as_of) for p in stored[-2:]] == [
            (date(2025, 12, 29), F(10842, 10000), "fake", clock.now()),
            (date(2025, 12, 30), F(109, 100), "fake", clock.now()),
        ]
        assert service.last_runs[book.id] is report
        assert "2 stored" in report.summary()

    def test_a_second_run_is_idempotent_and_a_correction_is_a_new_row(
        self, priced_chart: Chart, service, fake, clock
    ):
        book = priced_chart.book
        fake.table["USD"] = [quote("EUR", "USD", date(2025, 12, 30), "1.09")]
        service.run(book)
        count = len(book.prices.all())
        clock.advance(hours=1)
        again = service.run(book, start=date(2025, 12, 30))
        assert (again.stored, again.unchanged) == (0, 1)
        assert len(book.prices.all()) == count

        fake.table["USD"] = [quote("EUR", "USD", date(2025, 12, 30), "1.0901")]
        corrected = service.run(book, start=date(2025, 12, 30))
        assert corrected.stored == 1
        history = [
            p
            for p in book.prices.history(
                book.base_currency, priced_chart.usd, include_inverse=False
            )
            if p.effective_date == date(2025, 12, 30)
        ]
        assert [p.rate for p in history] == [F(109, 100), F(10901, 10000)]
        # Bitemporal: the old figure is still there for a report pinned before the correction.
        policy = ValuationPolicy(
            as_of=date(2025, 12, 30), knowledge_time=clock.now() - timedelta(minutes=30)
        )
        assert book.prices.direct(book.base_currency, priced_chart.usd, policy).rate == F(
            109, 100
        )

    def test_a_users_price_is_kept_and_wins_the_day(
        self, priced_chart: Chart, service, fake
    ):
        book = priced_chart.book
        book.add_price(
            book.base_currency,
            priced_chart.usd,
            "1.20",
            effective_date=date(2025, 12, 30),
        )
        fake.table["USD"] = [quote("EUR", "USD", date(2025, 12, 30), "1.09")]
        report = service.run(book)
        assert report.stored == 1
        policy = ValuationPolicy(as_of=date(2025, 12, 30))
        chosen = book.prices.direct(book.base_currency, priced_chart.usd, policy)
        assert (chosen.rate, chosen.source) == (F(120, 100), PriceSource.USER)

    def test_a_quote_in_a_currency_the_book_lacks_is_a_problem(
        self, priced_chart: Chart, service, fake
    ):
        fake.table["USD"] = [quote("USD", "CHF", date(2025, 12, 30), "0.9")]
        report = service.run(priced_chart.book)
        assert report.stored == 0
        assert [str(p) for p in report.problems] == [
            "fake / USD: quotes are in CHF, which this book has no commodity for; add CHF to store them"
        ]

    def test_one_provider_failing_does_not_stop_the_others(
        self, priced_chart: Chart, clock
    ):
        from pymoney.core import commands as c

        book = priced_chart.book
        good, bad = FakeProvider(), FakeProvider()
        bad.name = "bad"
        bad.fail_with = "HTTP 503 from upstream"
        good.table["USD"] = [quote("EUR", "USD", date(2025, 12, 30), "1.09")]
        book.execute(
            c.UpdateCommodity(commodity_id=priced_chart.aapl.id, quote_source="bad")
        )
        service = QuoteService(ProviderRegistry([good, bad]), http=no_http, clock=clock)
        report = service.run(book)
        assert report.stored == 1
        assert [str(p) for p in report.problems] == ["bad / AAPL: HTTP 503 from upstream"]
        assert not report.ok

    def test_a_plugins_bug_is_its_own_problem(self, priced_chart: Chart, clock):
        class Buggy(FakeProvider):
            name = "fake"

            def fetch(self, request, http):
                raise KeyError("oops")

        service = QuoteService(ProviderRegistry([Buggy()]), http=no_http, clock=clock)
        report = service.run(priced_chart.book)
        assert "provider failed: KeyError" in report.problems[0].message

    def test_an_unstorable_rate_is_a_problem_not_a_crash(
        self, priced_chart: Chart, service, fake
    ):
        """A rational too big for two BIGINTs is refused by the SQL store; the
        run records that and keeps the rest. Memory has no such limit."""
        from pymoney.core.store import MemoryStore

        if isinstance(priced_chart.book.store, MemoryStore):
            pytest.skip("only a SQL store has a 64-bit limit")
        fake.table["USD"] = [
            quote("EUR", "USD", date(2025, 12, 29), "1.09"),
            Quote(
                base_code="EUR",
                quote_code="USD",
                effective_date=date(2025, 12, 30),
                rate=F(10**30, 7),
                raw="huge",
            ),
        ]
        report = service.run(priced_chart.book)
        assert report.stored == 1
        assert "2025-12-30 huge" in report.problems[0].message

    def test_nothing_to_do_is_a_quiet_report(self, chart: Chart, service):
        report = service.run(chart.book)
        assert report.ok and report.requested == 0
        assert report.summary().startswith("nothing to fetch")


# ==========================================================================
# scheduler
# ==========================================================================
class TestScheduler:
    def test_a_tick_locks_the_book_to_plan_and_to_store_but_not_to_fetch(
        self, priced_chart: Chart, service, fake
    ):
        """The network is the slow part and touches nothing of the book's, so
        the lock is released around it: a user posting while the ECB is slow
        waits for the plan or the store, never for the ECB."""
        book = priced_chart.book
        fake.table["USD"] = [quote("EUR", "USD", date(2025, 12, 30), "1.09")]
        lock = threading.Lock()
        events: list[str] = []

        @contextmanager
        def open_book(book_id):
            assert book_id == book.id
            with lock:
                events.append("lock")
                yield book
                events.append("unlock")

        real_fetch = fake.fetch

        def observed_fetch(request, http):
            # If the scheduler still held the lock here, this would deadlock
            # (a plain Lock, as the registry's) -- so acquiring it proves the
            # book is free while the provider is being asked.
            with lock:
                events.append("fetch-with-book-free")
            return real_fetch(request, http)

        fake.fetch = observed_fetch
        scheduler = QuoteScheduler(
            service,
            book_ids=lambda: [book.id],
            open_book=open_book,
            interval=timedelta(hours=1),
        )
        reports = scheduler.tick()
        assert events == ["lock", "unlock", "fetch-with-book-free", "lock", "unlock"]
        assert reports[book.id].stored == 1
        assert scheduler.ticks == 1

    def test_a_failing_book_does_not_stop_the_round(
        self, priced_chart: Chart, service, caplog
    ):
        book = priced_chart.book

        @contextmanager
        def open_book(book_id):
            if book_id != book.id:
                raise RuntimeError("gone")
            yield book

        from uuid import uuid4

        scheduler = QuoteScheduler(
            service,
            book_ids=lambda: [uuid4(), book.id],
            open_book=open_book,
            interval=timedelta(hours=1),
        )
        reports = scheduler.tick()
        assert list(reports) == [book.id]
        assert "failed for book" in caplog.text

    def test_start_and_stop(self, chart: Chart, service):
        ran = threading.Event()

        @contextmanager
        def open_book(book_id):
            ran.set()
            yield chart.book

        scheduler = QuoteScheduler(
            service,
            book_ids=lambda: [chart.book.id],
            open_book=open_book,
            interval=timedelta(milliseconds=10),
            initial_delay=timedelta(0),
        )
        scheduler.start()
        assert scheduler.running
        assert ran.wait(5)
        scheduler.stop()
        assert not scheduler.running
        assert scheduler.ticks >= 1
        with pytest.raises(ValueError):
            QuoteScheduler(
                service, book_ids=list, open_book=open_book, interval=timedelta(0)
            )


# ==========================================================================
# command line
# ==========================================================================
class TestCli:
    def test_lists_providers_including_plugins(self, capsys):
        assert (
            quotes_main(
                ["--list-providers", "--plugin", "pymoney.api.testing:FakeProvider"]
            )
            == 0
        )
        out = capsys.readouterr().out
        assert out.splitlines()[0].startswith("ecb")
        assert "fake" in out

    def test_fetches_into_a_database(self, tmp_path, capsys, monkeypatch):
        from pymoney.core import commands as c
        from pymoney.store import create_engine, create_schema, open_book

        url = f"sqlite+pysqlite:///{tmp_path / 'q.sqlite'}"
        engine = create_engine(url)
        create_schema(engine)
        book = open_book(engine, name="Q", base_currency=Commodity.currency("EUR"))
        usd = book.add_currency("USD")
        book.execute(c.UpdateCommodity(commodity_id=usd.id, quote_source="fake"))
        book.add_account("Wallet", AccountType.CASH, commodity=usd)
        book_id = book.id
        book.close()
        engine.dispose()

        SCRIPTED["USD"] = [quote("EUR", "USD", date.today() - timedelta(days=1), "1.08")]
        try:
            monkeypatch.setenv("PYMONEY_QUOTE_PLUGINS", f"{__name__}:FakeProvider")
            assert quotes_main(["--url", url]) == 0
        finally:
            SCRIPTED.clear()
        out = capsys.readouterr().out
        assert f"Q ({book_id})" in out and "1 stored" in out

        engine = create_engine(url)
        reopened = open_book(engine, book_id=book_id)
        assert [p.source for p in reopened.prices.all()] == ["fake"]
        reopened.close()
        engine.dispose()

    def test_needs_a_url(self, monkeypatch):
        monkeypatch.delenv("PYMONEY_DATABASE_URL", raising=False)
        with pytest.raises(SystemExit, match="no database URL"):
            quotes_main([])


def test_a_book_without_base_currency_is_skipped_by_the_cli(tmp_path, capsys):
    from pymoney.store import create_engine, create_schema, open_book

    url = f"sqlite+pysqlite:///{tmp_path / 'nobase.sqlite'}"
    engine = create_engine(url)
    create_schema(engine)
    book: Book = open_book(engine, name="Empty")
    book.close()
    engine.dispose()
    assert quotes_main(["--url", url]) == 0
    assert capsys.readouterr().out == ""


def test_quote_request_validates_its_range(eur):
    with pytest.raises(ValueError):
        QuoteRequest(
            commodity=eur, currency=eur, start=TODAY, end=TODAY - timedelta(days=1)
        )
    with pytest.raises(ValueError):
        Quote(base_code="A", quote_code="B", effective_date=TODAY, rate=F(0), raw="0")
    assert CommodityKind.CURRENCY  # keeps the import honest
    assert datetime.now(timezone.utc).tzinfo is timezone.utc


class TestTransport:
    def test_a_timeout_is_retried_once_then_explained(self, monkeypatch):
        import urllib.request

        from pymoney.quotes import provider

        calls = []

        def slow(request, timeout):
            calls.append(timeout)
            raise TimeoutError("The read operation timed out")

        monkeypatch.setattr(urllib.request, "urlopen", slow)
        with pytest.raises(QuoteError, match=r"timed out.*PYMONEY_QUOTE_TIMEOUT"):
            provider.urllib_http("https://example.invalid/x", {})
        assert len(calls) == 2 and calls[0] == provider.DEFAULT_TIMEOUT

    def test_an_http_error_is_not_retried(self, monkeypatch):
        import urllib.error
        import urllib.request

        from pymoney.quotes import provider

        calls = []

        def refuse(request, timeout):
            calls.append(1)
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests", {}, None
            )  # type: ignore[arg-type]

        monkeypatch.setattr(urllib.request, "urlopen", refuse)
        with pytest.raises(QuoteError, match="HTTP 429"):
            provider.urllib_http("https://example.invalid/x", {})
        assert calls == [1]
