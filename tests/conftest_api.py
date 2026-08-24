"""Fixtures for the HTTP tests.

A real file-backed SQLite database, not ``:memory:``: the API needs more than one
connection at a time (the session lookup and the book's store), and an in-memory
SQLite database is a single connection. ``create_app`` refuses it outright for
that reason, so testing against it would be testing something we do not ship.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pymoney.api import create_app
from pymoney.api.settings import Settings
from pymoney.core.clock import ManualClock

PASSWORD = "correct horse battery staple"


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

    def delete(self, url: str, **kw: Any):
        return self.http.delete(
            url, headers={**self.headers, **kw.pop("headers", {})}, **kw
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


@pytest.fixture
def api(tmp_path: Path):
    """An application on a fresh database, with a fixed clock."""
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'api.sqlite'}",
        create_schema=True,
        cookie_secure=False,
        allow_registration=True,
    )
    app = create_app(settings=settings, clock=ManualClock())
    with TestClient(app) as http:
        yield Client(http=http)


@pytest.fixture
def user(api: Client) -> Client:
    api.register("owner@example.invalid")
    return api


@dataclass
class Ledger:
    """A small chart of accounts, over HTTP."""

    client: Client
    book_id: str
    checking: str
    savings: str
    groceries: str
    fuel: str
    salary: str
    opening: str
    eur: str


@pytest.fixture
def ledger(user: Client) -> Ledger:
    book_id = user.make_book()
    assets = user.account("Assets", "ASSET", placeholder=True)
    checking = user.account("Checking", "CHECKING", parent_id=assets["id"])
    savings = user.account("Savings", "SAVINGS", parent_id=assets["id"])
    groceries = user.account("Groceries", "EXPENSE")
    fuel = user.account("Fuel", "EXPENSE")
    salary = user.account("Salary", "INCOME")
    opening = user.account("Opening Balances", "EQUITY")

    user.post_transaction(
        "2026-01-01",
        [
            {"account_id": checking["id"], "value": "1000.00"},
            {"account_id": opening["id"]},
        ],
        memo="Opening balance",
    )
    user.post_transaction(
        "2026-01-05",
        [
            {"account_id": checking["id"], "value": "-84.20"},
            {"account_id": groceries["id"]},
        ],
        memo="Supermarket",
    )
    user.post_transaction(
        "2026-01-25",
        [
            {"account_id": checking["id"], "value": "3200.00"},
            {"account_id": salary["id"]},
        ],
        memo="Salary",
    )
    return Ledger(
        client=user,
        book_id=book_id,
        checking=checking["id"],
        savings=savings["id"],
        groceries=groceries["id"],
        fuel=fuel["id"],
        salary=salary["id"],
        opening=opening["id"],
        eur=checking["commodity_id"],
    )


@pytest.fixture
def today() -> date:
    return date(2026, 1, 31)
