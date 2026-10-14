"""HTTP: budgets and the budget report."""

from __future__ import annotations

from tests.conftest_api import Ledger


def _spend(ledger: Ledger, when: str, amount: str, account: str) -> None:
    ledger.client.post_transaction(
        when,
        [
            {"account_id": ledger.checking, "value": f"-{amount}"},
            {"account_id": account},
        ],
    )


def year(figure: str) -> dict[str, str]:
    """`figure` for each month of 2026 -- a plan is the months it names."""
    return {f"2026-{m:02d}": figure for m in range(1, 13)}


class TestBudgets:
    def test_put_get_patch_delete(self, ledger: Ledger):
        base = f"/books/{ledger.book_id}"
        put = ledger.client.put(
            f"{base}/accounts/{ledger.groceries}/budget", json={"months": year("400.00")}
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["months"]["2026-01"]["minor"] == 40000
        assert body["months"]["2026-12"]["minor"] == 40000

        assert [b["account_id"] for b in ledger.client.get(f"{base}/budgets").json()] == [
            ledger.groceries
        ]

        # One month differs.  A PATCH writes the months named and no others.
        patched = ledger.client.patch(
            f"{base}/accounts/{ledger.groceries}/budget",
            json={"months": {"2026-12": "700.00"}},
        )
        assert patched.json()["months"]["2026-12"]["minor"] == 70000
        assert patched.json()["months"]["2026-01"]["minor"] == 40000

        # Filling the months on screen is one request, and it leaves a month
        # that is not on screen exactly as it was.
        filled = ledger.client.patch(
            f"{base}/accounts/{ledger.groceries}/budget",
            json={"months": {f"2026-{m:02d}": "450.00" for m in range(1, 12)}},
        )
        assert filled.json()["months"]["2026-11"]["minor"] == 45000
        assert filled.json()["months"]["2026-12"]["minor"] == 70000

        # Clearing one month removes that month and nothing else.
        cleared = ledger.client.patch(
            f"{base}/accounts/{ledger.groceries}/budget",
            json={"months": {"2026-12": None}},
        )
        assert "2026-12" not in cleared.json()["months"]
        assert cleared.json()["months"]["2026-11"]["minor"] == 45000

        assert (
            ledger.client.delete(f"{base}/accounts/{ledger.groceries}/budget").status_code
            == 204
        )
        assert (
            ledger.client.get(f"{base}/accounts/{ledger.groceries}/budget").status_code
            == 404
        )

    def test_patch_creates_the_plan_on_first_figure(self, ledger: Ledger):
        base = f"/books/{ledger.book_id}"
        made = ledger.client.patch(
            f"{base}/accounts/{ledger.fuel}/budget", json={"months": {"2026-05": "80.00"}}
        )
        assert made.status_code == 200
        assert list(made.json()["months"]) == ["2026-05"]
        assert made.json()["months"]["2026-05"]["minor"] == 8000
        # A month nobody planned is unanswered, not zero and not the figure
        # above: there is no default that would apply to it.
        assert "2026-06" not in made.json()["months"]

    def test_clearing_the_last_month_removes_the_plan(self, ledger: Ledger):
        base = f"/books/{ledger.book_id}"
        ledger.client.patch(
            f"{base}/accounts/{ledger.groceries}/budget",
            json={"months": {"2026-01": "400.00"}},
        )
        gone = ledger.client.patch(
            f"{base}/accounts/{ledger.groceries}/budget",
            json={"months": {"2026-01": None}},
        )
        # A plan left with nothing in it is no plan at all.
        assert gone.status_code == 200, gone.text
        assert (
            ledger.client.get(f"{base}/accounts/{ledger.groceries}/budget").status_code
            == 404
        )

    def test_a_placeholder_cannot_be_budgeted(self, ledger: Ledger):
        base = f"/books/{ledger.book_id}"
        shelf = ledger.client.account("Shelf", "EXPENSE", placeholder=True)
        refused = ledger.client.put(
            f"{base}/accounts/{shelf['id']}/budget", json={"months": year("10.00")}
        )
        assert refused.status_code == 422

    def test_the_budget_report(self, ledger: Ledger):
        base = f"/books/{ledger.book_id}"
        _spend(ledger, "2026-01-05", "120.00", ledger.groceries)
        _spend(ledger, "2026-02-06", "450.00", ledger.groceries)
        _spend(ledger, "2026-01-09", "50.00", ledger.fuel)
        ledger.client.put(
            f"{base}/accounts/{ledger.groceries}/budget",
            json={"months": {**year("400.00"), "2026-02": "600.00"}},
        )
        ledger.client.put(
            f"{base}/accounts/{ledger.fuel}/budget", json={"months": year("80.00")}
        )

        report = ledger.client.get(
            f"{base}/reports/budget",
            params={"start": "2026-01-01", "end": "2026-02-28"},
        )
        assert report.status_code == 200, report.text
        body = report.json()
        assert [c["label"] for c in body["period"]["columns"]] == ["2026-01", "2026-02"]
        rows = {r["account_id"]: r for r in body["rows"]}
        moved = {r["account_id"]: r["display_cells"] for r in body["period"]["rows"]}
        groceries = rows[ledger.groceries]
        assert [c["minor"] for c in groceries["budget"]] == [40000, 60000]
        # What is left is derived: budget minus the row's own movement.
        jan_groceries = (
            groceries["budget"][0]["minor"] - moved[ledger.groceries][0]["minor"]
        )
        feb_groceries = (
            groceries["budget"][1]["minor"] - moved[ledger.groceries][1]["minor"]
        )
        assert jan_groceries == 40000 - 20420  # the fixture's 84.20 shop + 120.00
        assert feb_groceries == 60000 - 45000  # 450.00 spent in February

        # The footer is one figure per column and no total entry: the client
        # sums and subtracts the same way.
        assert [c["minor"] for c in body["expenses"]["budget"]] == [48000, 68000]
        assert [c["minor"] for c in body["expenses"]["spent"]] == [20420 + 5000, 45000]
        # Nothing is planned as income, so the grand total is the expenses.
        assert body["income"]["budget"] == [None, None]
        assert [c["minor"] for c in body["income"]["spent"]] == [0, 0]
        # The period part is the ordinary period table.
        assert body["period"]["target"] == "EUR"

    def test_the_report_says_which_rows_can_be_edited(self, ledger: Ledger):
        """A placeholder and a closed account take no plan, so the report says
        so on the row the editor would sit on."""
        base = f"/books/{ledger.book_id}"
        shelf = ledger.client.account("Shelf", "EXPENSE", placeholder=True)
        plates = ledger.client.account("Plates", "EXPENSE", parent_id=shelf["id"])
        ledger.client.put(
            f"{base}/accounts/{plates['id']}/budget", json={"months": year("10.00")}
        )
        fees = ledger.client.account("Fees", "EXPENSE")
        ledger.client.post(f"{base}/accounts/{fees['id']}/closed", json={"closed": True})

        report = ledger.client.get(
            f"{base}/reports/budget",
            params={"start": "2026-01-01", "end": "2026-02-28", "include_closed": True},
        )
        assert report.status_code == 200, report.text
        rows = {r["account_id"]: r for r in report.json()["rows"]}
        assert rows[shelf["id"]]["placeholder"] is True
        assert rows[plates["id"]]["placeholder"] is False
        assert rows[fees["id"]]["closed"] is True
        assert rows[ledger.groceries]["closed"] is False

    def test_the_report_splits_the_plan_by_class(self, ledger: Ledger):
        """The footer of the budget page: income beside expenses, each with
        its plan and what was moved.  Both read as positive magnitudes, so a
        client subtracts them into savings rather than the server carrying it.
        """
        base = f"/books/{ledger.book_id}"
        ledger.client.put(
            f"{base}/accounts/{ledger.salary}/budget", json={"months": year("3000.00")}
        )
        ledger.client.put(
            f"{base}/accounts/{ledger.groceries}/budget", json={"months": year("400.00")}
        )

        report = ledger.client.get(
            f"{base}/reports/budget",
            params={"start": "2026-01-01", "end": "2026-01-31"},
        )
        assert report.status_code == 200, report.text
        body = report.json()
        # One column (January) and no total entry: the client adds up.
        assert [c["minor"] for c in body["income"]["budget"]] == [300000]
        # 3,200.00 earned against a 3,000.00 plan leaves -200.00.
        assert [c["minor"] for c in body["income"]["spent"]] == [320000]
        assert (
            body["income"]["budget"][0]["minor"] - body["income"]["spent"][0]["minor"]
            == -20000
        )
        assert [c["minor"] for c in body["expenses"]["budget"]] == [40000]
        # 84.20 spent against a 400.00 plan leaves 315.80.
        assert [c["minor"] for c in body["expenses"]["spent"]] == [8420]
        assert (
            body["expenses"]["budget"][0]["minor"] - body["expenses"]["spent"][0]["minor"]
            == 31580
        )
        # Savings is what the two plans leave over: income minus expenses.
        assert (
            body["income"]["budget"][0]["minor"] - body["expenses"]["budget"][0]["minor"]
            == 260000
        )
        assert (
            body["income"]["budget"][0]["minor"] - body["income"]["spent"][0]["minor"]
        ) - (
            body["expenses"]["budget"][0]["minor"] - body["expenses"]["spent"][0]["minor"]
        ) == -51580

    def test_budgets_travel_in_the_sync_feed(self, ledger: Ledger):
        base = f"/books/{ledger.book_id}"
        before = ledger.client.get(f"{base}/changes", params={"since": 0}).json()["head"]
        ledger.client.put(
            f"{base}/accounts/{ledger.groceries}/budget", json={"months": year("400.00")}
        )
        page = ledger.client.get(f"{base}/changes", params={"since": before}).json()
        budgets = [b for batch in page["batches"] for b in batch["budgets"]]
        assert [b["account_id"] for b in budgets] == [ledger.groceries]
