"""The desktop's report windows, offscreen, against the real API."""

from __future__ import annotations

import os
from datetime import date
from uuid import UUID

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pymoney.api.testing import PASSWORD, Client
from PySide6.QtWidgets import QTreeWidgetItem

from pymoney_desktop import OfflineClient, RemoteClient
from pymoney_desktop.ui.main_window import MainWindow, SyncExecutor
from pymoney_desktop.ui.reports import (
    PERIOD_PRESETS,
    ReportWindow,
    period_for,
    rows_to_csv,
)
from tests.test_desktop_clients import EMAIL, InProcessTransport, leg

TODAY = date(2026, 3, 15)


@pytest.fixture(params=["remote", "offline"])
def book(api: Client, tmp_path, request):
    """A book with two currencies, a security, a closed account and a quarter of history."""
    api.register(EMAIL)
    book_id = api.make_book("Reports")
    b = f"/books/{book_id}"
    eur = api.get(b).json()["base_currency_id"]
    usd = api.post(f"{b}/commodities", json={"code": "USD"}).json()["id"]
    acme = api.post(
        f"{b}/commodities", json={"code": "ACME", "kind": "STOCK", "scale": 4}
    ).json()["id"]
    assets = api.account("Assets", "ASSET", placeholder=True)
    checking = api.account("Checking", "CHECKING", parent_id=assets["id"])
    wallet = api.account("Wallet", "CASH", parent_id=assets["id"], commodity_id=usd)
    shares = api.account("ACME", "STOCK", parent_id=assets["id"], commodity_id=acme)
    visa = api.account("Visa", "CREDIT_CARD")
    salary = api.account("Salary", "INCOME")
    food = api.account("Food", "EXPENSE")
    old = api.account("Old habit", "EXPENSE")
    api.post(
        f"{b}/prices",
        json={
            "base_id": usd,
            "quote_id": eur,
            "rate": "0.9",
            "effective_date": "2026-01-01",
        },
    )
    api.post(
        f"{b}/prices",
        json={
            "base_id": acme,
            "quote_id": usd,
            "rate": "120",
            "effective_date": "2026-03-01",
        },
    )
    posts = [
        api.post_transaction(
            "2026-01-25",
            [leg(checking["id"], "3000"), leg(salary["id"])],
            memo="January pay",
        ),
        api.post_transaction(
            "2026-02-25",
            [leg(checking["id"], "3000"), leg(salary["id"])],
            memo="February pay",
        ),
        api.post_transaction(
            "2026-03-02", [leg(visa["id"], "-60"), leg(food["id"])], memo="dinner"
        ),
        api.post_transaction(
            "2026-02-10", [leg(checking["id"], "-15"), leg(old["id"])], memo="old"
        ),
        api.post_transaction(
            "2026-01-10",
            [leg(checking["id"], "-900"), {"account_id": wallet["id"], "price": "0.9"}],
            memo="cash",
        ),
        api.post_transaction(
            "2026-01-11",
            [
                leg(checking["id"], "-900"),
                {"account_id": shares["id"], "quantity": "10", "price": "90"},
            ],
            memo="buy",
        ),
    ]
    assert [p.status_code for p in posts] == [201] * 6, [p.text for p in posts]
    api.post(f"{b}/accounts/{old['id']}/closed", json={"closed": True})
    api.logout()
    transport = InProcessTransport(api.http)
    client = (
        RemoteClient(transport)
        if request.param == "remote"
        else OfflineClient(transport, tmp_path / "r.sqlite")
    )
    client.login(EMAIL, PASSWORD)
    commodities = client.commodities(UUID(book_id))
    yield client, UUID(book_id), commodities, UUID(eur), UUID(usd), UUID(old["id"])
    client.close()


def texts(tree) -> list[tuple[str, ...]]:
    """Every row of the tree, depth-first, as its column texts."""
    out: list[tuple[str, ...]] = []

    def walk(item: QTreeWidgetItem) -> None:
        out.append(tuple(item.text(c) for c in range(tree.columnCount())))
        for i in range(item.childCount()):
            walk(item.child(i))

    root = tree.invisibleRootItem()
    for i in range(root.childCount()):
        walk(root.child(i))
    return out


def make(qtbot, client, book_id, kind, commodities, eur) -> ReportWindow:
    window = ReportWindow(
        client, book_id, kind, commodities, eur, executor=SyncExecutor(), today=TODAY
    )
    qtbot.addWidget(window)
    return window


class TestBalanceSheet:
    def test_rows_totals_and_rates(self, qtbot, book):
        client, book_id, commodities, eur, _usd, _old = book
        window = make(qtbot, client, book_id, "balance-sheet", commodities, eur)
        rows = texts(window.tree)
        # The tree: Assets with its children, Visa. Checking 6000-900-900-15 = 4185;
        # wallet 1000 USD at 0.9 = 900; 10 ACME at 120 USD * 0.9 = 1080.
        assert rows[0] == ("Assets", "", "6,165.00")
        assert ("Checking", "", "4,185.00") in rows
        assert ("Wallet", "", "900.00") in rows
        assert ("ACME", "", "1,080.00") in rows
        # A liability reads positive, as on the web.
        assert ("Visa", "", "60.00") in rows
        assert "Total assets <b>6,165.00 EUR</b>" in window.totals.text()
        assert "Total liabilities <b>60.00 EUR</b>" in window.totals.text()
        assert "Net worth <b>6,105.00 EUR</b>" in window.totals.text()
        provenance = window.provenance.text()
        assert "1 USD = 0.9 EUR" in provenance
        assert "1 ACME = 108 EUR" in provenance and "ACME → USD → EUR" in provenance
        assert window.status.text() == "Balance sheet in EUR"

    def test_as_of_currency_and_strict(self, qtbot, book):
        client, book_id, commodities, eur, usd, _old = book
        window = make(qtbot, client, book_id, "balance-sheet", commodities, eur)
        # Report in USD.
        window.target.setCurrentIndex(window.target.findData(str(usd)))
        assert window.report is not None and window.report.target == "USD"
        assert (
            "Net worth <b>" in window.totals.text() and "USD</b>" in window.totals.text()
        )
        assert window.status.text() == "Balance sheet in USD"
        # Before anything happened: zero everywhere, nothing to convert.
        window.target.setCurrentIndex(0)
        from PySide6.QtCore import QDate

        window.as_of.setDate(QDate(2025, 12, 31))
        assert window.report is not None and window.report.net_worth.minor == 0
        # Strict mode with a date before the ACME price: the error is shown, not hidden.
        window.as_of.setDate(QDate(2026, 2, 28))
        assert window.report is not None and window.report.unpriced == ["ACME"]
        assert "Could not be valued and left out" in window.provenance.text()
        window.strict.setChecked(True)
        assert window.report is None and window.last_error is not None
        assert (
            "NoPriceError" in window.status.text()
            or "no price" in window.status.text().lower()
        )
        window.strict.setChecked(False)
        assert window.report is not None

    def test_csv_matches_the_web_apps_columns(self, qtbot, book, tmp_path):
        client, book_id, commodities, eur, _usd, _old = book
        window = make(qtbot, client, book_id, "balance-sheet", commodities, eur)
        text = window.csv_text()
        lines = text.splitlines()
        assert lines[0] == "path,depth,value,own_value,commodity"
        assert "Assets:Checking,1,4185.00,4185.00,EUR" in lines
        assert (
            "Visa,0,60.00,-60.00,EUR" in lines
        )  # own value as stored, display value natural
        target = tmp_path / "out.csv"
        window.choose_path = lambda suggested: (str(target), "")  # type: ignore[method-assign]
        window.export_csv()
        assert target.read_bytes().decode() == text
        assert rows_to_csv([]) == "path,depth,value,own_value,commodity\r\n"


class TestIncomeStatement:
    def test_presets_and_period(self, qtbot, book):
        client, book_id, commodities, eur, _usd, _old = book
        window = make(qtbot, client, book_id, "income-statement", commodities, eur)
        # Opens on "this month" (March 2026): the dinner, no pay.
        assert window.preset.currentData() == "this-month"
        assert window.start.date().toString("yyyy-MM-dd") == "2026-03-01"
        assert window.as_of.date().toString("yyyy-MM-dd") == "2026-03-15"
        assert "Expenses <b>60.00 EUR</b>" in window.totals.text()
        assert "Income <b>0.00 EUR</b>" in window.totals.text()
        rows = texts(window.tree)
        assert ("Food", "", "60.00") in rows
        # Last month: February's pay, the closed account's expense hidden but counted.
        window.preset.setCurrentIndex(window.preset.findData("last-month"))
        assert window.start.date().toString("yyyy-MM-dd") == "2026-02-01"
        assert window.as_of.date().toString("yyyy-MM-dd") == "2026-02-28"
        assert "Income <b>3,000.00 EUR</b>" in window.totals.text()
        assert "Expenses <b>15.00 EUR</b>" in window.totals.text()
        assert "Net <b>2,985.00 EUR</b>" in window.totals.text()
        assert "Savings rate <b>99.5&nbsp;%</b>" in window.totals.text()
        assert not any(r[0] == "Old habit" for r in texts(window.tree))
        window.closed.setChecked(True)
        assert ("Old habit", "", "15.00") in texts(window.tree)
        # Year to date, then editing a date by hand makes it a custom period.
        window.preset.setCurrentIndex(window.preset.findData("year-to-date"))
        assert "Income <b>6,000.00 EUR</b>" in window.totals.text()
        from PySide6.QtCore import QDate

        window.start.setDate(QDate(2026, 2, 1))
        assert window.preset.currentData() == ""
        assert "Income <b>3,000.00 EUR</b>" in window.totals.text()
        # A period that ends before it starts runs nothing.
        window.start.setDate(QDate(2026, 4, 1))
        assert window.status.text() == "The period ends before it starts."

    def test_period_presets_match_the_web_app(self):
        today = date(2026, 3, 15)
        assert period_for("this-month", today) == (date(2026, 3, 1), today)
        assert period_for("last-month", today) == (date(2026, 2, 1), date(2026, 2, 28))
        assert period_for("this-quarter", today) == (date(2026, 1, 1), today)
        assert period_for("last-quarter", today) == (
            date(2025, 10, 1),
            date(2025, 12, 31),
        )
        assert period_for("year-to-date", today) == (date(2026, 1, 1), today)
        assert period_for("last-year", today) == (date(2025, 1, 1), date(2025, 12, 31))
        assert period_for("last-12-months", today) == (date(2025, 3, 15), today)
        assert period_for("last-month", date(2026, 1, 10)) == (
            date(2025, 12, 1),
            date(2025, 12, 31),
        )
        assert period_for("last-12-months", date(2028, 2, 29)) == (
            date(2027, 2, 28),
            date(2028, 2, 29),
        )
        assert next(k for k, _ in PERIOD_PRESETS) == "this-month"
        with pytest.raises(ValueError):
            period_for("fortnight", today)


class TestHoldings:
    def test_positions_and_empty(self, qtbot, book):
        client, book_id, commodities, eur, usd, _old = book
        window = make(qtbot, client, book_id, "holdings", commodities, eur)
        rows = texts(window.tree)
        assert rows == [
            ("Assets:ACME", "ACME", "10.0000", "108", "900.00", "1,080.00", "180.00")
        ]
        assert "Total value <b>1,080.00 EUR</b>" in window.totals.text()
        assert "Total cost <b>900.00 EUR</b>" in window.totals.text()
        # In USD the cost is still in EUR, so no gain is shown.
        window.target.setCurrentIndex(window.target.findData(str(usd)))
        assert texts(window.tree)[0][5:] == ("1,200.00", "")
        # Before the purchase there is no position, unless empty ones are asked for.
        from PySide6.QtCore import QDate

        window.target.setCurrentIndex(0)
        window.as_of.setDate(QDate(2026, 1, 10))
        assert texts(window.tree) == []
        window.empty.setChecked(True)
        assert len(texts(window.tree)) == 1
        csv = window.csv_text().splitlines()
        assert csv[0].startswith("path,commodity,quantity,unit_price,cost,market_value")


def test_the_main_window_opens_reports_from_its_menu(qtbot, api: Client, tmp_path):
    api.register(EMAIL)
    book_id = api.make_book("Menu")
    api.account("Cash", "CASH")
    api.logout()
    client = OfflineClient(InProcessTransport(api.http), tmp_path / "m.sqlite")
    client.login(EMAIL, PASSWORD)
    win = MainWindow(client, executor=SyncExecutor(), sync_every_ms=0)
    qtbot.addWidget(win)
    assert [a.text() for a in win.report_actions[:3]] == [
        "&Balance sheet…",
        "&Income statement…",
        "&Holdings…",
    ]
    assert not win.report_actions[0].isEnabled()  # no book yet
    win.load()
    assert win.report_actions[0].isEnabled()
    for action in win.report_actions[:3]:
        action.trigger()
    assert [w.windowTitle() for w in win.report_windows] == [
        "PyMoney — Balance sheet",
        "PyMoney — Income statement",
        "PyMoney — Holdings",
    ]
    assert all(w.report is not None for w in win.report_windows)
    assert win.report_windows[0].status.text() == "Balance sheet in EUR"
    assert UUID(str(book_id))  # the fixture's id is a real uuid
    client.close()


class TestCharts:
    """The graphical reports draw the same figures the tables show."""

    @staticmethod
    def chart(qtbot, client, book_id, kind, commodities, eur):
        from pymoney_desktop.ui.charts import ChartWindow

        window = ChartWindow(
            client, book_id, kind, commodities, eur, executor=SyncExecutor(), today=TODAY
        )
        qtbot.addWidget(window)
        return window

    def test_expense_and_income_pies(self, qtbot, book):
        client, book_id, commodities, eur, _usd, _old = book
        window = self.chart(qtbot, client, book_id, "expense-pie", commodities, eur)
        # Year to date: Food 60 and, on the closed account, 15. A pie slice is a
        # category's own figure, positive, largest first.
        assert window.slices == [("Food", 6000), ("Old habit", 1500)]
        assert window.chart.series()[0].count() == 2
        assert "75.00 EUR" in window.summary.text()
        assert window.status.text() == "Expenses by category in EUR"

        income = self.chart(qtbot, client, book_id, "income-pie", commodities, eur)
        assert income.slices == [("Salary", 600000)]
        assert "Salary" in income.chart.series()[0].slices()[0].label()
        assert income.csv_text().splitlines() == [
            "label,amount,currency",
            'Salary,"6,000.00",EUR',
        ]

    def test_holdings_pie_and_currency(self, qtbot, book):
        client, book_id, commodities, eur, usd, _old = book
        window = self.chart(qtbot, client, book_id, "holdings-pie", commodities, eur)
        assert window.slices == [("ACME", 108000)]
        window.target.setCurrentIndex(window.target.findData(str(usd)))
        assert window.slices == [("ACME", 120000)]
        assert window.status.text() == "Holdings by position in USD"

    def test_net_worth_line(self, qtbot, book):
        client, book_id, commodities, eur, _usd, _old = book
        window = self.chart(qtbot, client, book_id, "net-worth", commodities, eur)
        assert [p.label for p in window.report.points] == [
            "2026-01",
            "2026-02",
            "2026-03",
        ]
        names = [s.name() for s in window.chart.series()]
        # Net worth last: it is drawn on top when it coincides with the assets.
        assert names == ["Assets", "Liabilities", "Net worth"]
        net = window.chart.series()[-1]
        assert net.count() == 3
        # January: 3000 - 900 - 900 + 900 (wallet) + 900 (shares at cost, no
        # quote yet -> 10 * 90 USD... no: ACME has no price before March).
        points = [round(net.at(i).y(), 2) for i in range(net.count())]
        assert points[-1] == 6105.0
        assert "6,105.00 EUR" in window.summary.text()
        # Quarterly collapses the three months into one point.
        window.interval.setCurrentIndex(window.interval.findData("quarter"))
        assert [p.label for p in window.report.points] == ["2026-Q1"]

    def test_period_table(self, qtbot, book):
        client, book_id, commodities, eur, _usd, _old = book
        window = self.chart(qtbot, client, book_id, "period-table", commodities, eur)
        table = window.table
        headers = [
            table.horizontalHeaderItem(c).text() for c in range(table.columnCount())
        ]
        assert headers == ["Account", "2026-01", "2026-02", "2026-03", "Total"]
        rows = {
            table.item(r, 0).text().strip(): [
                table.item(r, c).text() for c in range(1, table.columnCount())
            ]
            for r in range(table.rowCount())
        }
        assert rows["Salary"] == ["3,000.00", "3,000.00", "", "6,000.00"]
        assert rows["Food"] == ["", "", "60.00", "60.00"]
        assert "Old habit" not in rows  # closed, hidden by default
        assert rows["Income"] == ["3,000.00", "3,000.00", "0.00", "6,000.00"]
        assert rows["Expenses"] == ["0.00", "15.00", "60.00", "75.00"]
        assert rows["Net"] == ["3,000.00", "2,985.00", "-60.00", "5,925.00"]
        window.closed.setChecked(True)
        assert any(
            table.item(r, 0).text().strip() == "Old habit"
            for r in range(table.rowCount())
        )
        csv_lines = window.csv_text().splitlines()
        assert csv_lines[0] == "account,2026-01,2026-02,2026-03,total"
        assert csv_lines[-1].startswith("Net,")

    def test_an_error_is_shown_not_raised(self, qtbot, book):
        client, book_id, commodities, eur, _usd, _old = book
        window = self.chart(qtbot, client, book_id, "holdings-pie", commodities, eur)
        window.strict.setChecked(True)
        window.as_of.setDate(_qdate_for(date(2026, 2, 1)))  # before any ACME quote
        assert window.last_error is not None
        assert window.slices == [] and "NoPrice" in window.status.text()


def _qdate_for(value: date):
    from PySide6.QtCore import QDate

    return QDate(value.year, value.month, value.day)


def test_the_main_window_opens_charts_from_its_menu(qtbot, api: Client, tmp_path):
    api.register(EMAIL)
    api.make_book("Menu")
    api.account("Cash", "CASH")
    api.logout()
    client = OfflineClient(InProcessTransport(api.http), tmp_path / "c.sqlite")
    client.login(EMAIL, PASSWORD)
    win = MainWindow(client, executor=SyncExecutor(), sync_every_ms=0)
    qtbot.addWidget(win)
    win.load()
    assert [a.text() for a in win.report_actions[3:8]] == [
        "&Income by category…",
        "&Expenses by category…",
        "&Net worth over time…",
        "&Income and expenses by period…",
        "&Holdings by position…",
    ]
    for action in win.report_actions[3:]:
        action.trigger()
    assert [w.windowTitle() for w in win.report_windows] == [
        "PyMoney — Income by category",
        "PyMoney — Expenses by category",
        "PyMoney — Net worth over time",
        "PyMoney — Income and expenses by period",
        "PyMoney — Holdings by position",
    ]
    assert all(w.report is not None for w in win.report_windows)
    client.close()
