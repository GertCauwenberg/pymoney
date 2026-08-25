"""Seed a small, fully priced demo book through the HTTP API.

    python scripts/seed_demo.py --url http://127.0.0.1:8000 \
        --email demo@example.com --password demo-password-123

Registers the user if needed (or logs in), creates "Demo Household" with a
EUR base currency, a USD cash account, a brokerage position in a security,
salary and expenses across the current and previous year, and the prices
needed to value all of it. Idempotent enough for development: it refuses to
run if a book of that name already exists for the user.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date


class Api:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.token: str | None = None

    def call(self, method: str, path: str, body: object | None = None) -> object:
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            sys.exit(f"{method} {path} -> {e.code}: {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default="demo-password-123")
    parser.add_argument("--name", default="Demo Household")
    args = parser.parse_args()

    api = Api(args.url)
    creds = {"email": args.email, "password": args.password}
    try:
        session = api.call("POST", "/auth/login", creds)
    except SystemExit:
        session = api.call("POST", "/auth/register", {**creds, "display_name": "Demo"})
    assert isinstance(session, dict)
    api.token = session["token"]

    books = api.call("GET", "/books")
    assert isinstance(books, list)
    if any(b["name"] == args.name for b in books):
        sys.exit(f"a book called {args.name!r} already exists; nothing to do")

    book = api.call("POST", "/books", {"name": args.name, "base_currency_code": "EUR"})
    assert isinstance(book, dict)
    b = f"/books/{book['id']}"
    eur = book["base_currency_id"]

    def commodity(**fields: object) -> str:
        out = api.call("POST", f"{b}/commodities", fields)
        assert isinstance(out, dict)
        return out["id"]

    usd = commodity(code="USD", name="US Dollar", kind="CURRENCY", symbol="$")
    acme = commodity(code="ACME", name="Acme Corp", kind="STOCK", scale=4)
    fund = commodity(code="WRLD", name="World Index Fund", kind="FUND", scale=4)

    def account(
        name: str, type_: str, parent: str | None = None, **fields: object
    ) -> str:
        body: dict[str, object] = {"name": name, "type": type_, **fields}
        if parent:
            body["parent_id"] = parent
        out = api.call("POST", f"{b}/accounts", body)
        assert isinstance(out, dict)
        return out["id"]

    assets = account("Assets", "ASSET", placeholder=True)
    checking = account("Checking", "CHECKING", assets, institution="Demo Bank")
    savings = account("Savings", "SAVINGS", assets)
    wallet_usd = account("Dollar Wallet", "CASH", assets, commodity_id=usd)
    brokerage = account("Brokerage", "INVESTMENT", assets, placeholder=True)
    brokerage_cash = account("Cash", "INVESTMENT", brokerage)
    acme_acct = account("Acme Corp", "STOCK", brokerage, commodity_id=acme)
    fund_acct = account("World Index", "STOCK", brokerage, commodity_id=fund)

    liabilities = account("Liabilities", "LIABILITY", placeholder=True)
    card = account("Credit Card", "CREDIT_CARD", liabilities)
    mortgage = account("Mortgage", "LOAN", liabilities)

    equity = account("Equity", "EQUITY", placeholder=True)
    opening = account("Opening Balances", "EQUITY", equity)

    income = account("Income", "INCOME", placeholder=True)
    salary = account("Salary", "INCOME", income)
    interest = account("Interest", "INCOME", income)
    dividends = account("Dividends", "INCOME", income)

    expenses = account("Expenses", "EXPENSE", placeholder=True)
    housing = account("Housing", "EXPENSE", expenses, placeholder=True)
    rent = account("Mortgage Interest", "EXPENSE", housing)
    utilities = account("Utilities", "EXPENSE", housing)
    food = account("Food", "EXPENSE", expenses, placeholder=True)
    groceries = account("Groceries", "EXPENSE", food)
    dining = account("Dining Out", "EXPENSE", food)
    transport = account("Transport", "EXPENSE", expenses)
    fees = account("Bank Fees", "EXPENSE", expenses)

    def post(
        day: date, memo: str, legs: list[dict[str, object]], **fields: object
    ) -> None:
        api.call(
            "POST",
            f"{b}/transactions",
            {"date": day.isoformat(), "memo": memo, "legs": legs, **fields},
        )

    def leg(acct: str, value: str, **fields: object) -> dict[str, object]:
        return {"account_id": acct, "value": value, **fields}

    today = date.today()
    y = today.year
    start = date(y - 1, 1, 1)

    post(
        start,
        "Opening balances",
        [
            leg(checking, "4200.00"),
            leg(savings, "15000.00"),
            leg(brokerage_cash, "500.00"),
            leg(mortgage, "-180000.00"),
            leg(opening, "160300.00"),
        ],
    )
    # A USD wallet: the transaction is in EUR, the wallet leg carries quantity.
    post(
        start,
        "Cash for the trip",
        [leg(checking, "-270.00"), leg(wallet_usd, "270.00", quantity="300.00")],
    )

    # Two years of monthly life, up to last month.
    month = start
    while month < date(y, today.month, 1):
        salary_amount = "3850.00" if month.year == y - 1 else "3975.00"
        post(
            date(month.year, month.month, 25),
            "Salary",
            [leg(checking, salary_amount), leg(salary, f"-{salary_amount}")],
        )
        post(
            date(month.year, month.month, 1),
            "Mortgage",
            [leg(checking, "-1100.00"), leg(mortgage, "650.00"), leg(rent, "450.00")],
        )
        post(
            date(month.year, month.month, 3),
            "Electricity and water",
            [leg(checking, "-185.40"), leg(utilities, "185.40")],
        )
        post(
            date(month.year, month.month, 8),
            "Supermarket",
            [leg(card, "-312.55"), leg(groceries, "312.55")],
        )
        post(
            date(month.year, month.month, 22),
            "Supermarket",
            [leg(card, "-287.10"), leg(groceries, "287.10")],
        )
        post(
            date(month.year, month.month, 14),
            "Restaurant",
            [leg(card, "-86.00"), leg(dining, "86.00")],
        )
        post(
            date(month.year, month.month, 18),
            "Train pass",
            [leg(checking, "-74.00"), leg(transport, "74.00")],
        )
        post(
            date(month.year, month.month, 28),
            "Card statement",
            [leg(checking, "-685.65"), leg(card, "685.65")],
        )
        post(
            date(month.year, month.month, 28),
            "Account fee",
            [leg(checking, "-4.90"), leg(fees, "4.90")],
        )
        if month.month in (3, 6, 9, 12):
            post(
                date(month.year, month.month, 30 if month.month != 12 else 31),
                "Savings interest",
                [leg(savings, "61.25"), leg(interest, "-61.25")],
            )
            post(
                date(month.year, month.month, 15),
                "Transfer to savings",
                [leg(checking, "-500.00"), leg(savings, "500.00")],
            )
        month = date(month.year + (month.month // 12), month.month % 12 + 1, 1)

    # Securities: bought in EUR through the brokerage cash account.
    post(
        date(y - 1, 2, 10),
        "Buy 25 ACME",
        [leg(brokerage_cash, "-2125.00"), leg(acme_acct, "2125.00", quantity="25")],
    )
    post(
        date(y - 1, 2, 10),
        "Fund the brokerage",
        [leg(savings, "-5000.00"), leg(brokerage_cash, "5000.00")],
    )
    post(
        date(y - 1, 3, 1),
        "Buy 30.5 WRLD",
        [leg(brokerage_cash, "-3050.00"), leg(fund_acct, "3050.00", quantity="30.5")],
    )
    post(
        date(y, 1, 15),
        "Buy 10 ACME",
        [leg(brokerage_cash, "-960.00"), leg(acme_acct, "960.00", quantity="10")],
    )
    post(
        date(y, 2, 1),
        "ACME dividend",
        [leg(brokerage_cash, "21.00"), leg(dividends, "-21.00")],
    )

    def price(base: str, quote: str, rate: str, day: date, source: str = "demo") -> None:
        api.call(
            "POST",
            f"{b}/prices",
            {
                "base_id": base,
                "quote_id": quote,
                "rate": rate,
                "effective_date": day.isoformat(),
                "source": source,
            },
        )

    price(usd, eur, "0.90", start, "ecb")
    price(usd, eur, "0.92", date(y, 1, 2), "ecb")
    price(usd, eur, "0.8650", today, "ecb")
    price(acme, eur, "85.00", date(y - 1, 2, 10))
    price(acme, eur, "96.00", date(y, 1, 15))
    price(acme, eur, "104.20", today)
    price(fund, eur, "100.00", date(y - 1, 3, 1))
    price(fund, eur, "118.75", today)

    print(f"created {args.name!r} as book {book['id']}")


if __name__ == "__main__":
    main()
