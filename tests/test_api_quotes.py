"""Quotes over HTTP: providers, fetch now, backfill, the last run."""

from __future__ import annotations

from datetime import date, timedelta
from fractions import Fraction

from pymoney.quotes import Quote
from tests.conftest_api import Client, Ledger


def q(on: date, rate: str) -> Quote:
    return Quote(
        base_code="EUR",
        quote_code="USD",
        effective_date=on,
        rate=Fraction(rate),
        raw=rate,
    )


class TestProviders:
    def test_listed_for_any_user(self, user: Client):
        response = user.get("/quote-providers")
        assert response.status_code == 200
        names = [p["name"] for p in response.json()]
        assert names == ["ecb", "yahoo", "fake"]
        assert all(p["description"] for p in response.json())

    def test_needs_a_session(self, api: Client):
        assert api.get("/quote-providers").status_code == 401

    def test_a_commoditys_quote_source_must_name_one(self, ledger: Ledger):
        client = ledger.client
        refused = client.post(
            f"/books/{ledger.book_id}/commodities",
            json={"code": "USD", "quote_source": "bloomberg"},
        )
        assert refused.status_code == 422
        assert "installed: ecb, yahoo, fake" in refused.json()["detail"]
        ok = client.post(
            f"/books/{ledger.book_id}/commodities",
            json={"code": "USD", "quote_source": "ecb"},
        )
        assert ok.status_code == 201
        usd = ok.json()["id"]
        assert (
            client.patch(
                f"/books/{ledger.book_id}/commodities/{usd}",
                json={"quote_source": "nope"},
            ).status_code
            == 422
        )
        cleared = client.patch(
            f"/books/{ledger.book_id}/commodities/{usd}", json={"quote_source": None}
        )
        assert cleared.status_code == 200 and cleared.json()["quote_source"] is None


class TestFetch:
    def _usd(self, ledger: Ledger) -> str:
        response = ledger.client.post(
            f"/books/{ledger.book_id}/commodities",
            json={"code": "USD", "quote_source": "fake"},
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def test_fetch_now_stores_prices_and_reports(self, ledger: Ledger):
        client = ledger.client
        usd = self._usd(ledger)
        fake = client.http.app.state.fake_provider
        today = date(2026, 1, 1)  # the API fixture's clock
        fake.table["USD"] = [
            q(today - timedelta(days=2), "1.0842"),
            q(today - timedelta(days=1), "1.09"),
        ]

        before = client.get(f"/books/{ledger.book_id}").json()["revision"]
        response = client.post(f"/books/{ledger.book_id}/quotes/fetch", json={})
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["ok"] is True
        assert (
            report["requested"],
            report["fetched"],
            report["stored"],
            report["unchanged"],
        ) == (1, 2, 2, 0)
        assert report["providers"] == ["fake"]
        assert report["start"] == str(today - timedelta(days=7)) and report["end"] == str(
            today
        )
        assert int(response.headers["X-PyMoney-Revision"]) == before + 2

        prices = client.get(
            f"/books/{ledger.book_id}/prices", params={"quote_id": usd}
        ).json()["items"]
        assert [
            (p["effective_date"], p["rate"]["num"], p["rate"]["den"], p["source"])
            for p in prices
        ] == [
            ("2025-12-30", 5421, 5000, "fake"),
            ("2025-12-31", 109, 100, "fake"),
        ]
        # as_of is the fetch instant, from the server's clock.
        assert prices[0]["as_of"].startswith("2026-01-01T12:00:00")

        last = client.get(f"/books/{ledger.book_id}/quotes/last").json()
        assert last["stored"] == 2 and "2 stored" in last["summary"]

    def test_a_run_again_changes_nothing(self, ledger: Ledger):
        client = ledger.client
        self._usd(ledger)
        fake = client.http.app.state.fake_provider
        fake.table["USD"] = [q(date(2025, 12, 31), "1.09")]
        client.post(f"/books/{ledger.book_id}/quotes/fetch", json={})
        again = client.post(
            f"/books/{ledger.book_id}/quotes/fetch",
            json={"start": "2025-12-31", "end": "2026-01-01"},
        ).json()
        assert (again["stored"], again["unchanged"]) == (0, 1)

    def test_backfill_and_filters(self, ledger: Ledger):
        client = ledger.client
        usd = self._usd(ledger)
        fake = client.http.app.state.fake_provider
        fake.table["USD"] = [q(date(2020, 3, d), "1.1") for d in range(1, 6)]
        report = client.post(
            f"/books/{ledger.book_id}/quotes/fetch",
            json={
                "start": "2020-03-01",
                "end": "2020-03-03",
                "commodity_ids": [usd],
                "providers": ["fake"],
            },
        ).json()
        assert report["stored"] == 3
        assert fake.requests[-1].start == date(2020, 3, 1) and fake.requests[
            -1
        ].end == date(2020, 3, 3)
        nothing = client.post(
            f"/books/{ledger.book_id}/quotes/fetch", json={"providers": ["yahoo"]}
        ).json()
        assert nothing["requested"] == 0

    def test_a_provider_failing_is_in_the_report_not_a_500(self, ledger: Ledger):
        client = ledger.client
        self._usd(ledger)
        fake = client.http.app.state.fake_provider
        fake.fail_with = "upstream is down"
        try:
            response = client.post(f"/books/{ledger.book_id}/quotes/fetch", json={})
        finally:
            fake.fail_with = None
        assert response.status_code == 200
        report = response.json()
        assert report["ok"] is False
        assert report["problems"] == [
            {"provider": "fake", "commodity": "USD", "message": "upstream is down"}
        ]

    def test_no_run_yet_is_null(self, ledger: Ledger):
        assert ledger.client.get(f"/books/{ledger.book_id}/quotes/last").json() is None

    def test_fetching_needs_editor(self, ledger: Ledger):
        client = ledger.client
        client.put(
            f"/books/{ledger.book_id}/grants",
            json={"subject": "guest@example.invalid", "role": "viewer"},
        )
        client.register("guest@example.invalid")
        assert (
            client.post(f"/books/{ledger.book_id}/quotes/fetch", json={}).status_code
            == 403
        )
        assert client.get(f"/books/{ledger.book_id}/quotes/last").status_code == 200


class TestSchedulerSeesChanges:
    def test_a_quote_source_changed_over_the_api_is_used_by_the_next_tick(
        self, ledger: Ledger
    ):
        """The scheduler opens the same Book object the API writes to, so a
        PATCH to quote_source takes effect at the next tick -- no restart."""
        from datetime import timedelta

        from pymoney.quotes import QuoteScheduler

        client = ledger.client
        usd = client.post(
            f"/books/{ledger.book_id}/commodities",
            json={"code": "USD", "quote_source": "fake"},
        ).json()["id"]
        app = client.http.app
        fake = app.state.fake_provider
        fake.table["USD"] = [q(date(2025, 12, 31), "1.09")]
        registry = app.state.registry
        scheduler = QuoteScheduler(
            app.state.quotes,
            book_ids=registry.known_ids,
            open_book=registry.open,
            interval=timedelta(hours=1),
        )
        from uuid import UUID

        book_id = UUID(ledger.book_id)
        assert scheduler.tick()[book_id].requested == 1

        client.patch(
            f"/books/{ledger.book_id}/commodities/{usd}", json={"quote_source": None}
        )
        assert scheduler.tick()[book_id].requested == 0

        client.patch(
            f"/books/{ledger.book_id}/commodities/{usd}", json={"quote_source": "fake"}
        )
        report = scheduler.tick()[book_id]
        assert report.requested == 1
        # It resumes after the newest quote this provider gave, so nothing is re-asked.
        assert fake.requests[-1].start == date(2026, 1, 1) and report.fetched == 0
