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

import pytest

from pymoney.api.testing import (  # noqa: F401 - re-exported for the tests
    PASSWORD,
    Client,
    test_app,
)


@pytest.fixture
def api(tmp_path: Path):
    """An application on a fresh database, with a fixed clock."""
    with test_app(tmp_path) as client:
        yield client


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
