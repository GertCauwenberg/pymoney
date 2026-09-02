"""Test support shared by the server's own suite and the desktop's.

Not a test module: nothing here runs on its own. It is the reusable half of
testing the API -- an HTTP client that knows how to log in and how to write,
a scripted quote provider so no test ever reaches the network, and the
application built the way the tests want it (fresh SQLite file, registration
open, activation off, fixed clock). The desktop package has its own test suite
in its own repository directory and drives the same in-process API through
these, which is why they live in the package rather than in ``tests/``.

``pytest`` is deliberately not imported here; the fixtures that hand these out
live in each suite's ``conftest``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ..core.clock import ManualClock
from ..quotes import ProviderRegistry, QuoteService
from ..quotes.provider import Quote, QuoteError, QuoteProvider, QuoteRequest
from .app import create_app
from .settings import Settings

__all__ = [
    "PASSWORD",
    "SCRIPTED",
    "Client",
    "FakeProvider",
    "no_http",
    "test_app",
    "test_settings",
]

PASSWORD = "correct horse battery staple"


SCRIPTED: dict[str, list[Quote]] = {}


class FakeProvider(QuoteProvider):
    """A scripted provider: answers from a table, or fails on demand."""

    name = "fake"
    description = "scripted quotes for tests"

    def __init__(self) -> None:
        # A fresh instance (the CLI builds its own) starts from the module's
        # script, so a test can feed a provider it never gets to hold.
        self.table: dict[str, list[Quote]] = dict(SCRIPTED)
        self.requests: list[QuoteRequest] = []
        self.fail_with: str | None = None
        self.quote_code = "EUR"

    def fetch(self, request: QuoteRequest, http) -> list[Quote]:
        self.requests.append(request)
        if self.fail_with:
            raise QuoteError(self.fail_with)
        return [
            q
            for q in self.table.get(request.commodity.code, [])
            if request.start <= q.effective_date <= request.end
        ]


def no_http(url, headers):  # pragma: no cover - a test that hits the network is a bug
    raise AssertionError(f"unexpected HTTP request to {url}")


@dataclass
class Client:
    """A TestClient that knows how to authenticate and how to write.

    Write helpers add the CSRF header automatically, because forgetting it is a
    test bug rather than an interesting assertion.
    """

    http: TestClient
    csrf: str = ""
    token: str = ""
    book_id: str = ""
    email: str = ""

    # -- plumbing ----------------------------------------------------------
    @property
    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    def post(self, url: str, json: Any = None, **kw: Any):
        return self.http.post(
            url, json=json, headers={**self.headers, **kw.pop("headers", {})}, **kw
        )

    def patch(self, url: str, json: Any = None, **kw: Any):
        return self.http.patch(
            url, json=json, headers={**self.headers, **kw.pop("headers", {})}, **kw
        )

    def put(self, url: str, json: Any = None, **kw: Any):
        return self.http.put(
            url, json=json, headers={**self.headers, **kw.pop("headers", {})}, **kw
        )

    def delete(self, url: str, json: Any = None, **kw: Any):
        # httpx's delete() takes no body; DELETE /books/{id} and DELETE /auth/me
        # need one (the confirmation), so go through request().
        return self.http.request(
            "DELETE",
            url,
            json=json,
            headers={**self.headers, **kw.pop("headers", {})},
            **kw,
        )

    def get(self, url: str, **kw: Any):
        return self.http.get(url, **kw)

    # -- convenience -------------------------------------------------------
    def register(self, email: str, password: str = PASSWORD) -> dict[str, Any]:
        response = self.http.post(
            "/auth/register",
            json={"email": email, "password": password, "display_name": email},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        self.csrf = body["csrf_token"]
        self.token = body["token"]
        self.email = email
        return body

    def login(self, email: str, password: str = PASSWORD) -> dict[str, Any]:
        response = self.http.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        self.csrf = body["csrf_token"]
        self.token = body["token"]
        self.email = email
        return body

    def logout(self) -> None:
        self.post("/auth/logout")
        self.csrf = self.token = ""

    def make_book(
        self, name: str = "Test Book", code: str = "EUR", scale: int = 2
    ) -> str:
        response = self.post(
            "/books",
            json={
                "name": name,
                "base_currency_code": code,
                "base_currency_name": code,
                "base_currency_scale": scale,
                "base_currency_symbol": "\u20ac" if code == "EUR" else None,
            },
        )
        assert response.status_code == 201, response.text
        self.book_id = response.json()["id"]
        return self.book_id

    def account(self, name: str, type_: str, **kw: Any) -> dict[str, Any]:
        response = self.post(
            f"/books/{self.book_id}/accounts",
            json={"name": name, "type": type_, **kw},
        )
        assert response.status_code == 201, response.text
        return response.json()

    def post_transaction(self, when: str, legs: list[dict[str, Any]], **kw: Any):
        return self.post(
            f"/books/{self.book_id}/transactions",
            json={"date": when, "legs": legs, **kw},
        )

    def balance(self, account_id: str, **params: Any) -> dict[str, Any]:
        response = self.get(
            f"/books/{self.book_id}/accounts/{account_id}/balance", params=params
        )
        assert response.status_code == 200, response.text
        return response.json()["balance"]


def test_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """The settings the suites run under: a fresh SQLite file, registration
    open, activation off (the activation flow has tests of its own), no
    schedulers, mail kept in memory."""
    values: dict[str, Any] = {
        "database_url": f"sqlite+pysqlite:///{tmp_path / 'api.sqlite'}",
        "create_schema": True,
        "cookie_secure": False,
        "allow_registration": True,
        "quote_interval_minutes": 0,
        "maintenance_interval_minutes": 0,
        "require_activation": False,
        "mail_backend": "memory",
    }
    values.update(overrides)
    return Settings(**values)


@contextmanager
def test_app(tmp_path: Path, **overrides: Any) -> Iterator[Client]:
    """An application on a fresh database with a fixed clock and a scripted
    quote provider (``app.state.fake_provider``), wrapped in a :class:`Client`.

    A real file-backed SQLite database, not ``:memory:``: the API needs more
    than one connection at a time, and an in-memory SQLite database is one.
    """
    settings = test_settings(tmp_path, **overrides)
    clock = ManualClock()
    fake = FakeProvider()
    quotes = QuoteService(ProviderRegistry.builtin(), http=no_http, clock=clock)
    quotes.registry.add(fake)
    app = create_app(settings=settings, clock=clock, quotes_service=quotes)
    app.state.fake_provider = fake
    with TestClient(app) as http:
        yield Client(http=http)
