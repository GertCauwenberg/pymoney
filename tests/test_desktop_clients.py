"""The desktop's two clients against the real API, without a socket.

The transport wraps FastAPI's TestClient and can be told to "unplug", so the
offline client's whole story -- queue, replay, rejection, rebuild -- runs in
process against the same server code the web frontend talks to.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest

from pymoney.api.schemas import (
    AccountIn,
    PayeeIn,
    TransactionIn,
    TransactionUpdateIn,
)
from pymoney.desktop import (
    ApiError,
    LedgerClient,
    OfflineClient,
    OfflineError,
    RemoteClient,
    ReportOptions,
)
from tests.conftest_api import PASSWORD, Client

EMAIL = "desk@example.invalid"


class InProcessTransport:
    """FastAPI's TestClient as a Transport, with a plug to pull."""

    def __init__(self, http) -> None:
        from fastapi.testclient import TestClient

        # Its own client, and no cookie jar: the desktop authenticates with the
        # bearer token only, like a real UrllibTransport.
        self.http = TestClient(http.app)
        self.token: str | None = None
        self.plugged = True
        self.calls: list[tuple[str, str]] = []
        # Fail *after* the server has acted, once: the dropped-ack case.
        self.drop_ack_once = False

    def __call__(self, method: str, path: str, body: Any, headers: Mapping[str, str]):
        if not self.plugged:
            raise OfflineError("unplugged")
        sent = dict(headers)
        if self.token:
            sent["Authorization"] = f"Bearer {self.token}"
        response = self.http.request(method, path, json=body, headers=sent)
        self.http.cookies.clear()
        self.calls.append((method, path))
        if self.drop_ack_once and method != "GET":
            self.drop_ack_once = False
            raise OfflineError("connection reset after the request was sent")
        raw = response.content
        return (
            response.status_code,
            dict(response.headers),
            response.json() if raw else None,
        )


@pytest.fixture
def server(api: Client):
    """A registered user with one book and two accounts, made through the API."""
    api.register(EMAIL)
    book_id = api.make_book("Desk")
    checking = api.account("Checking", "CHECKING")
    food = api.account("Food", "EXPENSE")
    api.logout()
    return api, UUID(book_id), UUID(checking["id"]), UUID(food["id"])


def make_client(kind: str, http, tmp_path) -> tuple[LedgerClient, InProcessTransport]:
    transport = InProcessTransport(http)
    if kind == "remote":
        return RemoteClient(transport), transport
    return OfflineClient(transport, tmp_path / "replica.sqlite"), transport


def leg(account_id: UUID, value: str | None = None) -> dict:
    return {"account_id": str(account_id), "value": value}


def txn(checking: UUID, food: UUID, value: str = "-12.50", **extra) -> TransactionIn:
    return TransactionIn.model_validate(
        {
            "date": "2026-02-03",
            "legs": [leg(checking, value), leg(food)],
            "memo": "Lunch",
            **extra,
        }
    )


@pytest.mark.parametrize("kind", ["remote", "offline"])
class TestBothClients:
    def test_the_whole_round_trip(self, server, tmp_path, kind):
        api, book_id, checking, food = server
        client, _transport = make_client(kind, api.http, tmp_path)
        try:
            assert client.me() is None
            user = client.login(EMAIL, PASSWORD)
            assert user.email == EMAIL
            assert client.me() is not None

            books = client.books()
            assert [b.name for b in books] == ["Desk"]
            assert {a.name for a in client.accounts(book_id)} == {"Checking", "Food"}
            assert [c.code for c in client.commodities(book_id)] == ["EUR"]

            posted = client.post_transaction(book_id, txn(checking, food))
            assert posted.memo == "Lunch"
            assert [s.value.minor for s in posted.splits] == [-1250, 1250]

            page = client.register(book_id, checking)
            assert [r.memo for r in page.items] == ["Lunch"]
            assert page.items[0].running_balance.minor == -1250
            assert page.totals.closing.minor == -1250
            assert client.balance(book_id, checking).balance.minor == -1250

            edited = client.update_transaction(
                book_id, posted.id, TransactionUpdateIn(memo="Lunch with Sam")
            )
            assert edited.memo == "Lunch with Sam"
            assert client.transaction(book_id, posted.id).memo == "Lunch with Sam"

            payee = client.create_payee(book_id, PayeeIn(name="Deli"))
            assert {p.name for p in client.payees(book_id)} == {"Deli"}
            savings = client.create_account(
                book_id, AccountIn(name="Savings", type="SAVINGS")
            )
            assert savings.path == "Savings"

            sheet = client.balance_sheet(book_id, ReportOptions(as_of=date(2026, 2, 28)))
            assert sheet.assets.minor == -1250
            balances = client.account_balances(book_id, as_of=date(2026, 2, 28))
            assert balances[checking].minor == -1250 and balances[food].minor == 1250

            client.delete_transaction(book_id, posted.id)
            assert client.register(book_id, checking).items == []
            with pytest.raises(ApiError) as refused:
                client.transaction(book_id, posted.id)
            assert refused.value.status == 404

            status = client.sync()
            assert status.online and status.mode == kind
            assert status.queued == 0
            assert payee.name == "Deli"

            # Whatever the path, the server ends up with the same books.
            api.login(EMAIL)
            server_names = {
                a["name"] for a in api.get(f"/books/{book_id}/accounts").json()
            }
            assert server_names == {"Checking", "Food", "Savings"}
            assert (
                api.get(f"/books/{book_id}/accounts/{checking}/register").json()["items"]
                == []
            )
            client.logout()
            assert client.me() is None
        finally:
            client.close()

    def test_domain_errors_come_back_as_the_server_phrases_them(
        self, server, tmp_path, kind
    ):
        api, book_id, checking, food = server
        client, _ = make_client(kind, api.http, tmp_path)
        try:
            client.login(EMAIL, PASSWORD)
            unbalanced = TransactionIn.model_validate(
                {"date": "2026-02-03", "legs": [leg(checking, "-1"), leg(food, "2")]}
            )
            with pytest.raises(ApiError) as refused:
                client.post_transaction(book_id, unbalanced)
            assert refused.value.status == 422
            assert refused.value.type == "UnbalancedTransactionError"
            assert client.register(book_id, checking).items == []
        finally:
            client.close()

    def test_a_wrong_password(self, server, tmp_path, kind):
        api, *_ = server
        client, _ = make_client(kind, api.http, tmp_path)
        with pytest.raises(ApiError) as refused:
            client.login(EMAIL, "nope")
        assert refused.value.status == 401
        client.close()


class TestRemoteCache:
    def test_reads_are_cached_until_the_revision_moves(self, server, tmp_path):
        api, book_id, checking, _food = server
        client, transport = make_client("remote", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        client.accounts(book_id)
        before = len(transport.calls)
        client.accounts(book_id)
        client.accounts(book_id)
        assert len(transport.calls) == before  # served from the cache
        # Someone else writes; the next response's revision header drops the cache.
        api.login(EMAIL)
        api.account("Savings", "SAVINGS")
        client.register(book_id, checking)  # any call sees the new revision
        assert {a.name for a in client.accounts(book_id)} == {
            "Checking",
            "Food",
            "Savings",
        }

    def test_unplugged_is_offline_error_and_shows_in_status(self, server, tmp_path):
        api, _book_id, *_ = server
        client, transport = make_client("remote", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        transport.plugged = False
        with pytest.raises(OfflineError):
            client.books()
        status = client.status()
        assert not status.online and "unplugged" in status.last_error
        transport.plugged = True
        assert client.sync().online


class TestOffline:
    def test_work_offline_then_replay(self, server, tmp_path):
        api, book_id, checking, food = server
        client, transport = make_client("offline", api.http, tmp_path)
        assert isinstance(client, OfflineClient)
        client.login(EMAIL, PASSWORD)
        client.accounts(book_id)  # first touch pulls the replica
        assert client.status().watermarks[book_id] > 0

        transport.plugged = False
        # Reads work from the replica; writes land locally and queue.
        assert {a.name for a in client.accounts(book_id)} == {"Checking", "Food"}
        posted = client.post_transaction(book_id, txn(checking, food))
        payee = client.create_payee(book_id, PayeeIn(name="Cafe"))
        client.update_transaction(
            book_id, posted.id, TransactionUpdateIn(payee_id=payee.id)
        )
        page = client.register(book_id, checking)
        assert [r.payee_id for r in page.items] == [payee.id]
        assert client.status().queued == 3
        assert next(q.summary for q in client.queued()).startswith("post 2026-02-03")
        # Syncing while unplugged changes nothing and loses nothing.
        status = client.sync()
        assert status.queued == 3 and not status.online

        transport.plugged = True
        status = client.sync()
        assert status.online and status.queued == 0 and status.rejections == ()
        # The server has exactly what the replica showed, same ids.
        api.login(EMAIL)
        server_rows = api.get(f"/books/{book_id}/accounts/{checking}/register").json()[
            "items"
        ]
        assert [(r["transaction_id"], r["payee_id"]) for r in server_rows] == [
            (str(posted.id), str(payee.id))
        ]
        client.close()

    def test_a_dropped_acknowledgement_does_not_post_twice(self, server, tmp_path):
        api, book_id, checking, food = server
        client, transport = make_client("offline", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        client.accounts(book_id)
        transport.plugged = False
        client.post_transaction(book_id, txn(checking, food))
        transport.plugged = True
        transport.drop_ack_once = True
        status = client.sync()
        assert (
            status.queued == 1 and not status.online
        )  # the server acted, we never heard
        status = client.sync()
        assert status.queued == 0
        api.login(EMAIL)
        assert (
            len(api.get(f"/books/{book_id}/accounts/{checking}/register").json()["items"])
            == 1
        )
        client.close()

    def test_a_rejection_rebuilds_the_replica_and_keeps_the_rest(self, server, tmp_path):
        api, book_id, checking, food = server
        client, transport = make_client("offline", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        client.accounts(book_id)

        transport.plugged = False
        doomed = client.post_transaction(book_id, txn(checking, food, memo="to Food"))
        # Meanwhile, on the server, Food is deleted and a payee is added.
        api.login(EMAIL)
        assert api.delete(f"/books/{book_id}/accounts/{food}").status_code == 204
        api.post(f"/books/{book_id}/payees", json={"name": "From the server"})
        # And locally a second, independent write.
        kept = client.create_payee(book_id, PayeeIn(name="From the train"))

        transport.plugged = True
        status = client.sync()
        assert status.queued == 0
        assert len(status.rejections) == 1
        rejection = status.rejections[0]
        assert (
            rejection.status == 404
            and rejection.command.summary == "post 2026-02-03 to Food"
        )
        # The replica is the server's truth again: Food is gone, the doomed
        # transaction with it, the independent payee made it, and so did the
        # server-side payee.
        assert {a.name for a in client.accounts(book_id)} == {"Checking"}
        with pytest.raises(ApiError):
            client.transaction(book_id, doomed.id)
        assert {p.name for p in client.payees(book_id)} == {
            "From the train",
            "From the server",
        }
        assert {p["name"] for p in api.get(f"/books/{book_id}/payees").json()} == {
            "From the train",
            "From the server",
        }
        assert kept.name == "From the train"
        client.dismiss_rejections()
        assert client.status().rejections == ()
        client.close()

    def test_a_pull_brings_what_others_did(self, server, tmp_path):
        api, book_id, checking, food = server
        client, _ = make_client("offline", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        assert client.register(book_id, checking).items == []
        api.login(EMAIL)
        api.post_transaction(
            "2026-02-04", [leg(checking, "-3.00"), leg(food)], memo="Coffee"
        )
        api.post(f"/books/{book_id}/accounts/{checking}/rename", json={"name": "Current"})
        api.patch(
            f"/books/{book_id}", json={"name": "Renamed", "base_currency_code": "EUR"}
        )
        client.sync()
        assert [r.memo for r in client.register(book_id, checking).items] == ["Coffee"]
        assert {a.name for a in client.accounts(book_id)} == {"Current", "Food"}
        assert (
            client.balance_sheet(
                book_id, ReportOptions(as_of=date(2026, 2, 28))
            ).assets.minor
            == -300
        )
        client.close()

    def test_a_cold_start_offline_shows_what_it_has(self, server, tmp_path):
        api, book_id, _checking, _food = server
        client, transport = make_client("offline", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        client.books()
        client.accounts(book_id)
        client.close()

        transport.plugged = False
        again = OfflineClient(transport, tmp_path / "replica.sqlite")
        assert again.me() is not None and again.me().email == EMAIL
        assert [b.name for b in again.books()] == ["Desk"]
        assert {a.name for a in again.accounts(book_id)} == {"Checking", "Food"}
        status = again.status()
        assert status.mode == "offline" and not status.online
        again.close()

    def test_a_book_never_synced_cannot_be_read_offline(self, server, tmp_path):
        api, book_id, *_ = server
        client, transport = make_client("offline", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        transport.plugged = False
        with pytest.raises(OfflineError, match="never been synced"):
            client.accounts(book_id)
        client.close()

    def test_an_expired_session_is_forgotten(self, server, tmp_path):
        api, _book_id, *_ = server
        client, transport = make_client("offline", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        transport.token = "garbage"
        client._kv_set("token", "garbage")
        assert client.me() is None
        assert client.sync().last_error == "not logged in"
        client.close()

    def test_logging_out_keeps_the_replica_and_the_queue(self, server, tmp_path):
        api, book_id, checking, food = server
        client, transport = make_client("offline", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        client.accounts(book_id)
        transport.plugged = False
        client.post_transaction(book_id, txn(checking, food))
        client.logout()
        assert client.me() is None
        assert client.status().queued == 1
        transport.plugged = True
        client.login(EMAIL, PASSWORD)
        assert client.sync().queued == 0
        client.close()


def test_new_ids_are_client_chosen_and_stable(server, tmp_path):
    """The replica's row and the server's are the same row."""
    api, book_id, checking, food = server
    client, _ = make_client("offline", api.http, tmp_path)
    client.login(EMAIL, PASSWORD)
    chosen = uuid4()
    posted = client.post_transaction(book_id, txn(checking, food, id=str(chosen)))
    assert posted.id == chosen
    client.sync()
    api.login(EMAIL)
    assert api.get(f"/books/{book_id}/transactions/{chosen}").status_code == 200
    assert client.transaction(book_id, chosen).date == date(2026, 2, 3)
    client.close()


class TestUrllibTransport:
    """The real transport, against a tiny local HTTP server."""

    @pytest.fixture
    def server_url(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        seen: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(
                    {"path": self.path, "auth": self.headers.get("Authorization")}
                )
                if self.path == "/missing":
                    body = json.dumps(
                        {
                            "error": {
                                "type": "NotFoundError",
                                "message": "no such thing",
                                "status": 404,
                            }
                        }
                    ).encode()
                    self.send_response(404)
                else:
                    body = json.dumps({"ok": True}).encode()
                    self.send_response(200)
                    self.send_header("X-PyMoney-Revision", "7")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                seen.append(
                    {
                        "path": self.path,
                        "body": json.loads(self.rfile.read(length) or b"null"),
                    }
                )
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):  # quiet
                pass

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{httpd.server_port}", seen
        httpd.shutdown()
        httpd.server_close()

    def test_get_post_and_errors(self, server_url):
        from pymoney.desktop.transport import UrllibTransport

        url, seen = server_url
        transport = UrllibTransport(url)
        transport.token = "t0k"
        status, headers, body = transport("GET", "/health", None, {})
        assert (status, body) == (200, {"ok": True})
        assert {k.lower(): v for k, v in headers.items()}["x-pymoney-revision"] == "7"
        assert seen[-1]["auth"] == "Bearer t0k"
        status, _, body = transport(
            "POST", "/things", {"a": "1.50"}, {"Idempotency-Key": "k"}
        )
        assert status == 204 and body is None
        assert seen[-1]["body"] == {"a": "1.50"}
        status, _, body = transport("GET", "/missing", None, {})
        assert status == 404 and body["error"]["type"] == "NotFoundError"

    def test_an_unreachable_server_is_offline(self):
        from pymoney.desktop.transport import UrllibTransport

        transport = UrllibTransport("http://127.0.0.1:9", timeout=1)
        with pytest.raises(OfflineError):
            transport("GET", "/health", None, {})


def test_connect_picks_the_client(tmp_path):
    pytest.importorskip("PySide6")
    from pymoney.desktop.ui.app import connect

    remote = connect("http://127.0.0.1:9", offline=False)
    assert isinstance(remote, RemoteClient)
    offline = connect(
        "http://127.0.0.1:9", offline=True, replica=tmp_path / "deep" / "replica.sqlite"
    )
    assert isinstance(offline, OfflineClient)
    assert (tmp_path / "deep" / "replica.sqlite").exists()
    offline.close()


@pytest.mark.parametrize("kind", ["remote", "offline"])
class TestReports:
    """The three reports, with every option the web app exposes, give the same
    numbers through either client -- the offline one runs the very same view
    code on its replica."""

    @pytest.fixture
    def rich(self, server, tmp_path, kind):
        api, book_id, checking, food = server
        client, _ = make_client(kind, api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        # A USD wallet, a security, a price for each, a closed account, and some history.
        api.login(EMAIL)
        b = f"/books/{book_id}"
        usd_id = api.post(f"{b}/commodities", json={"code": "USD"}).json()["id"]
        acme_id = api.post(
            f"{b}/commodities", json={"code": "ACME", "kind": "STOCK", "scale": 4}
        ).json()["id"]
        wallet = api.account("Wallet", "CASH", commodity_id=usd_id)
        shares = api.account("ACME shares", "STOCK", commodity_id=acme_id)
        salary = api.account("Salary", "INCOME")
        old = api.account("Old", "EXPENSE")
        eur = api.get(f"{b}").json()["base_currency_id"]
        api.post(
            f"{b}/prices",
            json={
                "base_id": usd_id,
                "quote_id": eur,
                "rate": "0.9",
                "effective_date": "2026-01-01",
            },
        )
        api.post(
            f"{b}/prices",
            json={
                "base_id": acme_id,
                "quote_id": usd_id,
                "rate": "100",
                "effective_date": "2026-01-01",
            },
        )
        posted = [
            api.post_transaction(
                "2026-01-05", [leg(checking, "3000"), leg(salary["id"])], memo="pay"
            ),
            api.post_transaction(
                "2026-01-06", [leg(checking, "-50"), leg(food)], memo="lunch"
            ),
            api.post_transaction(
                "2026-02-06", [leg(checking, "-20"), leg(old["id"])], memo="old thing"
            ),
            # 900 EUR of cash at 0.9 EUR/USD: the residual leg gets 1000 USD.
            api.post_transaction(
                "2026-01-07",
                [leg(checking, "-900"), {"account_id": wallet["id"], "price": "0.9"}],
                memo="cash",
            ),
            # 10 shares at 90 EUR each (100 USD * 0.9).
            api.post_transaction(
                "2026-01-08",
                [
                    leg(checking, "-900"),
                    {"account_id": shares["id"], "quantity": "10", "price": "90"},
                ],
                memo="buy",
            ),
        ]
        api.post(f"{b}/accounts/{old['id']}/closed", json={"closed": True})
        assert [p.status_code for p in posted] == [201] * 5, [p.text for p in posted]
        yield client, book_id, UUID(usd_id), UUID(eur), UUID(old["id"])
        client.close()

    def test_balance_sheet_options(self, rich, kind):
        client, book_id, usd, _eur, _old = rich
        sheet = client.balance_sheet(book_id, ReportOptions(as_of=date(2026, 1, 31)))
        assert sheet.target == "EUR"
        # checking 3000-50-900-900 = 1150; wallet 1000 USD * 0.9 = 900; 10 ACME * 100 USD * 0.9 = 900
        assert sheet.assets.minor == 115000 + 90000 + 90000
        assert {c.base for c in sheet.conversions} == {"USD", "ACME"}
        in_usd = client.balance_sheet(
            book_id, ReportOptions(as_of=date(2026, 1, 31), target_id=usd)
        )
        assert in_usd.target == "USD" and in_usd.assets.minor == round(
            (115000 / 0.9) + 100000 + 100000
        )
        # Pinned to a knowledge time before the prices were entered: strict
        # fails, lenient lists what it could not value and leaves it out.
        from datetime import datetime, timezone

        early = datetime(2025, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(ApiError) as refused:
            client.balance_sheet(
                book_id,
                ReportOptions(as_of=date(2026, 1, 31), known_before=early, strict=True),
            )
        assert refused.value.status == 422 and refused.value.type == "NoPriceError"
        lenient = client.balance_sheet(
            book_id,
            ReportOptions(as_of=date(2026, 1, 31), known_before=early, strict=False),
        )
        assert lenient.unpriced == ["ACME", "USD"]
        assert lenient.assets.minor == 115000

    def test_income_statement_period_and_closed(self, rich, kind):
        client, book_id, _usd, _eur, old = rich
        january = client.income_statement(
            book_id,
            start=date(2026, 1, 1),
            options=ReportOptions(as_of=date(2026, 1, 31)),
        )
        assert (
            january.income_earned.minor,
            january.expenses.minor,
            january.net.minor,
        ) == (300000, 5000, 295000)
        assert (
            january.savings_rate is not None
            and january.savings_rate.num * 100 // january.savings_rate.den == 98
        )
        # February's expense sits on a closed account: hidden by default, shown on request; the total always has it.
        february = client.income_statement(
            book_id,
            start=date(2026, 2, 1),
            options=ReportOptions(as_of=date(2026, 2, 28)),
        )
        assert february.expenses.minor == 2000
        assert all(r.account_id != old for r in february.rows)
        shown = client.income_statement(
            book_id,
            start=date(2026, 2, 1),
            options=ReportOptions(as_of=date(2026, 2, 28), include_closed=True),
        )
        assert any(r.account_id == old for r in shown.rows)

    def test_holdings(self, rich, kind):
        client, book_id, usd, _eur, _old = rich
        report = client.holdings(book_id, ReportOptions(as_of=date(2026, 1, 31)))
        assert [p.commodity for p in report.positions] == ["ACME"]
        position = report.positions[0]
        assert position.quantity.minor == 100000  # 10.0000 shares
        assert position.market_value.minor == 90000  # 10 * 100 USD * 0.9
        assert position.cost.minor == 90000 and position.unrealised_gain is not None
        in_usd = client.holdings(
            book_id, ReportOptions(as_of=date(2026, 1, 31), target_id=usd)
        )
        assert in_usd.total_value.minor == 100000
        # Empty positions only on request.
        before = client.holdings(book_id, ReportOptions(as_of=date(2026, 1, 7)))
        assert before.positions == []
        assert (
            len(
                client.holdings(
                    book_id, ReportOptions(as_of=date(2026, 1, 7)), include_empty=True
                ).positions
            )
            == 1
        )
