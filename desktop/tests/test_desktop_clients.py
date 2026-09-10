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
    ImportDecisionIn,
    PayeeIn,
    PostOccurrenceIn,
    ScheduleIn,
    SkipOccurrenceIn,
    TransactionIn,
    TransactionUpdateIn,
)
from pymoney.api.testing import PASSWORD, Client

from pymoney_desktop import (
    ApiError,
    LedgerClient,
    OfflineClient,
    OfflineError,
    RemoteClient,
    ReportOptions,
)

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

    def __call__(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: Mapping[str, str] = {},
        *,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        raw: bool = False,
    ):
        if not self.plugged:
            raise OfflineError("unplugged")
        sent = dict(headers)
        if self.token:
            sent["Authorization"] = f"Bearer {self.token}"
        if files is not None:
            response = self.http.request(
                method, path, data=dict(body or {}), files=dict(files), headers=sent
            )
        else:
            response = self.http.request(method, path, json=body, headers=sent)
        self.http.cookies.clear()
        self.calls.append((method, path))
        if self.drop_ack_once and method != "GET":
            self.drop_ack_once = False
            raise OfflineError("connection reset after the request was sent")
        content = response.content
        if raw:
            return response.status_code, dict(response.headers), content
        return (
            response.status_code,
            dict(response.headers),
            response.json() if content else None,
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

    def test_an_expired_session_keeps_the_queue(self, server, tmp_path):
        """A 401 on replay is about the session, not the queued work.

        The replay must stop, the outbox must survive, and logging in again
        must drain it -- losing a train ride's entries because the token aged
        out in the meantime would be silent data loss.
        """
        api, book_id, checking, food = server
        client, transport = make_client("offline", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        client.accounts(book_id)
        transport.plugged = False
        client.post_transaction(book_id, txn(checking, food))
        client.post_transaction(book_id, txn(checking, food, value="-3.00"))
        assert client.status().queued == 2

        # The session dies while the client is offline (revoked, expired, ...).
        transport.token = "expired"
        transport.plugged = True
        status = client.sync()  # must not raise
        assert status.queued == 2
        assert status.rejections == ()
        assert "log in" in (status.last_error or "")
        # The stored session is forgotten, so the UI asks for credentials.
        assert client.me() is None

        client.login(EMAIL, PASSWORD)
        status = client.sync()
        assert status.queued == 0 and status.rejections == ()
        api.login(EMAIL)
        listed = api.get(f"/books/{book_id}/transactions").json()["items"]
        assert {t["memo"] for t in listed} == {"Lunch"} and len(listed) == 2
        client.close()

    def test_a_throttled_or_forbidden_replay_keeps_the_queue(self, server, tmp_path):
        """403 and 429 do not judge the command either: stop, keep, retry later."""
        api, book_id, checking, food = server
        client, transport = make_client("offline", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        client.accounts(book_id)
        transport.plugged = False
        client.post_transaction(book_id, txn(checking, food))

        real, calls = client._transport, []
        for code, detail in ((429, "slow down"), (403, "you are a viewer here")):

            def refusing(method, path, body, headers, _code=code, _detail=detail):
                calls.append((method, path))
                return _code, {}, {"detail": _detail}

            client._transport = refusing
            transport.plugged = True
            status = client.sync()  # must not raise
            assert status.queued == 1
            assert status.rejections == ()
            assert str(code) in (status.last_error or "")
            # Still logged in: only a 401 forgets the session.
            assert client._kv_get("token") is not None

        client._transport = real
        assert client.sync().queued == 0
        client.close()


CSV = (
    b"Date,Details,Amount\n"
    b"2026-02-03,TESCO STORES 7 LEEDS,-42.11\n"
    b"2026-02-04,A NEW CAFE,-3.80\n"
)


@pytest.mark.parametrize("kind", ["remote", "offline"])
class TestCsvImport:
    """The statement import always runs against the server -- the duplicate
    and transfer checks need the book as the server holds it -- so both
    clients go the same way, and offline it surfaces as an OfflineError."""

    def _ready(self, server, tmp_path, kind):
        api, book_id, checking, food = server
        client, transport = make_client(kind, api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        client.accounts(book_id)  # the offline client pulls its replica here
        return client, transport, book_id, checking, food

    def test_inspect_preview_commit(self, server, tmp_path, kind):
        client, _transport, book_id, checking, food = self._ready(server, tmp_path, kind)
        try:
            inspect = client.inspect_csv(book_id, CSV)
            assert inspect.columns == ["Date", "Details", "Amount"]
            mapping = inspect.suggested_mapping
            assert mapping.date == "Date" and mapping.amount == "Amount"

            preview = client.preview_csv(book_id, checking, CSV, mapping)
            assert [p.status for p in preview.proposals] == ["new", "new"]

            decisions = [
                ImportDecisionIn(row_key=p.row_key, category_id=food)
                for p in preview.proposals
            ]
            report = client.commit_csv(
                book_id,
                checking,
                CSV,
                mapping,
                decisions,
                uncategorised_account_id=food,
            )
            assert report.posted == 2 and report.ok
            assert client.balance(book_id, checking).balance.minor == -4591
            # The replica (offline) caught up without a manual sync.
            register = client.register(book_id, checking)
            assert len(register.items) == 2

            # Re-importing the same file adds nothing.
            again = client.commit_csv(book_id, checking, CSV, mapping, decisions)
            assert again.posted == 0 and again.skipped_duplicates == 2
        finally:
            client.close()

    def test_the_mapping_is_remembered(self, server, tmp_path, kind):
        client, _transport, book_id, checking, _food = self._ready(server, tmp_path, kind)
        try:
            assert client.import_mapping(book_id, checking) is None
            mine = client.inspect_csv(book_id, CSV).suggested_mapping
            saved = client.save_import_mapping(book_id, checking, mine)
            assert saved.date == "Date"
            assert client.import_mapping(book_id, checking) == saved
        finally:
            client.close()

    def test_offline_it_needs_the_server(self, server, tmp_path, kind):
        if kind != "offline":
            pytest.skip("the remote client is always online or nothing")
        client, transport, book_id, checking, _food = self._ready(server, tmp_path, kind)
        try:
            transport.plugged = False
            with pytest.raises(OfflineError):
                client.inspect_csv(book_id, CSV)
            with pytest.raises(OfflineError):
                client.import_mapping(book_id, checking)
            assert client.status().online is False
        finally:
            client.close()


@pytest.mark.parametrize("kind", ["remote", "offline"])
class TestSchedules:
    def _rent(self, checking, rent):
        return ScheduleIn.model_validate(
            {
                "name": "Rent",
                "recurrence": {"frequency": "MONTHLY", "start": "2026-01-01"},
                "template": {
                    "legs": [
                        {"account_id": str(checking), "value": "-1200.00"},
                        {"account_id": str(rent)},
                    ],
                    "memo": "Monthly rent",
                },
            }
        )

    def test_create_due_post_and_skip(self, server, tmp_path, kind):
        api, book_id, checking, food = server
        client, _t = make_client(kind, api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        client.accounts(book_id)
        try:
            s = client.create_schedule(book_id, self._rent(checking, food))
            assert s.recurrence_text == "every month on day 1"
            panel = client.due_schedules(book_id)
            assert panel.occurrences >= 1 and panel.due[0].schedule.name == "Rent"
            assert panel.due[0].amount.text == "1200.00"

            txn = client.post_occurrence(book_id, s.id, PostOccurrenceIn())
            assert txn.imported_id == f"sched:{s.id}:2026-01-01"
            assert client.schedules(book_id)[0].occurrences_done == 1

            client.skip_occurrence(book_id, s.id, SkipOccurrenceIn())
            assert client.schedules(book_id)[0].next_due.isoformat() == "2026-03-01"
        finally:
            client.close()

    def test_offline_post_then_replay_converges(self, server, tmp_path, kind):
        if kind != "offline":
            pytest.skip("this is the offline outbox path")
        api, book_id, checking, food = server
        client, transport = make_client("offline", api.http, tmp_path)
        client.login(EMAIL, PASSWORD)
        s = client.create_schedule(book_id, self._rent(checking, food))
        client.sync()

        transport.plugged = False
        txn = client.post_occurrence(book_id, s.id, PostOccurrenceIn())
        assert txn.date.isoformat() == "2026-01-01"
        assert client.schedules(book_id)[0].occurrences_done == 1  # locally advanced
        assert client.status().queued == 1

        transport.plugged = True
        status = client.sync()
        assert status.queued == 0 and status.rejections == ()
        # The server has exactly one posted occurrence, same imported_id.
        api.login(EMAIL)
        rows = api.get(f"/books/{book_id}/accounts/{checking}/register").json()["items"]
        assert len(rows) == 1
        assert (
            api.get(f"/books/{book_id}/schedules/{s.id}").json()["occurrences_done"] == 1
        )
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
                raw = self.rfile.read(length)
                content_type = self.headers.get("Content-Type", "")
                if content_type.startswith("multipart/form-data"):
                    seen.append(
                        {
                            "path": self.path,
                            "content_type": content_type,
                            "multipart": raw,
                        }
                    )
                else:
                    seen.append({"path": self.path, "body": json.loads(raw or b"null")})
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
        from pymoney_desktop.transport import UrllibTransport

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

    def test_a_multipart_upload(self, server_url):
        from pymoney_desktop.transport import UrllibTransport

        url, seen = server_url
        transport = UrllibTransport(url)
        status, _, _ = transport(
            "POST",
            "/import",
            {"mapping": '{"date":"Date"}'},
            {},
            files={"file": ("statement.csv", b"Date\n2026-01-01\n", "text/csv")},
        )
        assert status == 204
        sent = seen[-1]
        assert sent["content_type"].startswith("multipart/form-data; boundary=")
        assert b'name="mapping"' in sent["multipart"]
        assert b'{"date":"Date"}' in sent["multipart"]
        assert b'filename="statement.csv"' in sent["multipart"]
        assert b"2026-01-01" in sent["multipart"]

    def test_an_unreachable_server_is_offline(self):
        from pymoney_desktop.transport import UrllibTransport

        transport = UrllibTransport("http://127.0.0.1:9", timeout=1)
        with pytest.raises(OfflineError):
            transport("GET", "/health", None, {})


def test_connect_picks_the_client(tmp_path):
    pytest.importorskip("PySide6")
    from pymoney_desktop.ui.app import connect

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

    def test_net_worth_and_period_table(self, rich, kind):
        client, book_id, _usd, _eur, old = rich
        series = client.net_worth(
            book_id,
            start=date(2026, 1, 1),
            options=ReportOptions(as_of=date(2026, 2, 28)),
        )
        assert [p.label for p in series.points] == ["2026-01", "2026-02"]
        january, february = series.points
        # February only spent 20.00 on the (closed) account.
        assert february.net_worth.minor == january.net_worth.minor - 2000
        assert series.target == "EUR"

        table = client.period_table(
            book_id,
            start=date(2026, 1, 1),
            interval="month",
            options=ReportOptions(as_of=date(2026, 2, 28), include_closed=True),
        )
        assert [c.label for c in table.columns] == ["2026-01", "2026-02"]
        assert [a.minor for a in table.income_earned] == [300000, 0, 300000]
        assert [a.minor for a in table.expenses] == [5000, 2000, 7000]
        assert [a.minor for a in table.net] == [295000, -2000, 293000]
        closed_row = next(r for r in table.rows if r.account_id == old)
        assert [c.minor for c in closed_row.display_cells] == [0, 2000]
        hidden = client.period_table(
            book_id,
            start=date(2026, 1, 1),
            options=ReportOptions(as_of=date(2026, 2, 28)),
        )
        assert all(r.account_id != old for r in hidden.rows)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.mark.parametrize("kind", ["remote", "offline"])
def test_attachments_round_trip_through_either_client(server, tmp_path, kind):
    api, book_id, checking, food = server
    client, transport = make_client(kind, api.http, tmp_path)
    client.login(EMAIL, PASSWORD)
    client.accounts(book_id)
    tx = client.post_transaction(book_id, txn(checking, food)).id
    if kind == "offline":
        client.sync()

    att = client.upload_attachment(
        book_id, tx, filename="receipt.png", data=PNG, content_type="image/png"
    )
    assert att.filename == "receipt.png" and att.byte_size == len(PNG)

    listed = client.attachments(book_id, tx)
    assert [a.id for a in listed] == [att.id]

    assert client.download_attachment(book_id, att.id) == PNG

    client.delete_attachment(book_id, att.id)
    assert client.attachments(book_id, tx) == []

    # Attachments need the connection; offline they fail rather than queue.
    if kind == "offline":
        transport.plugged = False
        with pytest.raises(OfflineError):
            client.upload_attachment(
                book_id, tx, filename="r.png", data=PNG, content_type="image/png"
            )


@pytest.mark.parametrize("kind", ["remote", "offline"])
def test_a_stock_split_through_either_client(server, tmp_path, kind):
    from pymoney.api.schemas import StockSplitIn

    api, book_id, checking, _food = server
    api.login(EMAIL)
    acme = api.post(
        f"/books/{book_id}/commodities",
        json={"code": "ACME", "kind": "STOCK", "scale": 4},
    ).json()
    shares = api.account("ACME", "STOCK", commodity_id=acme["id"])["id"]
    api.post_transaction(
        "2026-01-10",
        [
            {"account_id": shares, "quantity": "10", "price": "100.00"},
            {"account_id": str(checking), "value": "-1000.00"},
        ],
    )
    api.logout()

    client, _ = make_client(kind, api.http, tmp_path)
    client.login(EMAIL, PASSWORD)
    client.accounts(book_id)
    split = client.record_stock_split(
        book_id,
        UUID(shares),
        StockSplitIn(date=date(2026, 2, 1), ratio="2", memo="2-for-1"),
    )
    assert split.is_stock_split and len(split.splits) == 1
    assert split.splits[0].quantity.minor == 100000  # +10 shares: 10 -> 20
    assert split.splits[0].action == "SPLIT"
    if kind == "offline":
        client.sync()
    assert client.balance(book_id, UUID(shares)).balance.minor == 200000
    # The server has the transaction, with the same id the client chose --
    # even when it was recorded offline and only replayed on reconnect.
    api.login(EMAIL)
    register = api.get(f"/books/{book_id}/accounts/{shares}/register").json()["items"]
    assert str(split.id) in {r["transaction_id"] for r in register}


def test_a_bad_stock_split_is_the_servers_call(server, tmp_path):
    from pymoney.api.schemas import StockSplitIn

    from pymoney_desktop import RemoteClient

    api, book_id, _checking, food = server
    client, _ = make_client("remote", api.http, tmp_path)
    assert isinstance(client, RemoteClient)
    client.login(EMAIL, PASSWORD)
    with pytest.raises(ApiError) as caught:
        client.record_stock_split(
            book_id, food, StockSplitIn(date=date(2026, 2, 1), ratio="2")
        )
    assert caught.value.status == 422


@pytest.mark.parametrize("kind", ["remote", "offline"])
def test_search_finds_the_same_through_either_client(server, tmp_path, kind):
    from pymoney_desktop import SearchCriteria

    api, book_id, checking, food = server
    client, _ = make_client(kind, api.http, tmp_path)
    client.login(EMAIL, PASSWORD)
    client.post_transaction(book_id, txn(checking, food, memo="Lunch"))
    client.post_transaction(book_id, txn(checking, food, value="-40.00", memo="Dinner"))
    if kind == "offline":
        client.sync()

    def memos(**kw):
        found = client.search(book_id, SearchCriteria(**kw))
        return [t.memo for t in found.items], found.has_more

    assert memos(text="lun") == (["Lunch"], False)
    assert memos(amount="40") == (["Dinner"], False)
    assert memos(amount_min="10", amount_max="50") == (["Dinner", "Lunch"], False)
    assert memos(account_id=food, start=date(2026, 2, 1)) == (["Dinner", "Lunch"], False)
    assert memos(account_id=food, include_children=True, text="x") == ([], False)
    assert memos(text="n", limit=1) == (["Dinner"], True)
    with pytest.raises(ApiError) as refused:
        client.search(book_id, SearchCriteria())
    assert refused.value.status == 422
    client.close()


@pytest.mark.parametrize("kind", ["remote", "offline"])
def test_tags_round_trip_and_are_searchable_through_either_client(server, tmp_path, kind):
    from pymoney.api.schemas import TagIn

    from pymoney_desktop import SearchCriteria

    api, book_id, checking, food = server
    client, _ = make_client(kind, api.http, tmp_path)
    client.login(EMAIL, PASSWORD)
    holiday = client.create_tag(book_id, TagIn(name="Holiday", colour="#ff8800"))
    assert [t.name for t in client.tags(book_id)] == ["Holiday"]

    tagged = client.post_transaction(
        book_id,
        TransactionIn.model_validate(
            {
                "date": "2026-05-18",
                "memo": "Seaside lunch",
                "legs": [
                    {"account_id": str(checking), "value": "-47.50"},
                    {"account_id": str(food), "tag_ids": [str(holiday.id)]},
                ],
            }
        ),
    )
    client.post_transaction(book_id, txn(checking, food, memo="Ordinary lunch"))
    if kind == "offline":
        client.sync()

    categorised = next(s for s in tagged.splits if s.account_id == food)
    assert categorised.tag_ids == [holiday.id]
    round_tripped = client.transaction(book_id, tagged.id)
    assert [s.tag_ids for s in round_tripped.splits if s.account_id == food] == [
        [holiday.id]
    ]

    by_tag = client.search(book_id, SearchCriteria(tag_id=holiday.id))
    assert [t.memo for t in by_tag.items] == ["Seaside lunch"]
    client.close()


@pytest.mark.parametrize("kind", ["remote", "offline"])
def test_reconciling_through_either_client(server, tmp_path, kind):
    from pymoney.api.schemas import ReconcileStateName as S

    api, book_id, checking, food = server
    client, transport = make_client(kind, api.http, tmp_path)
    client.login(EMAIL, PASSWORD)
    first = client.post_transaction(book_id, txn(checking, food, memo="Lunch"))
    second = client.post_transaction(
        book_id, txn(checking, food, value="-40.00", memo="Dinner")
    )
    if kind == "offline":
        # The server books the legs itself, so the split ids it settles on
        # replace the replica's when the pull comes back: re-read before
        # naming a split, as the UI does after every write.
        client.sync()
        first, second = (client.transaction(book_id, t.id) for t in (first, second))
    split = next(s for s in first.splits if s.account_id == checking)

    # One split, walked up and back down.
    marked = client.set_split_state(
        book_id, first.id, split.id, S.CLEARED, date(2026, 2, 28)
    )
    assert next(s for s in marked.splits if s.id == split.id).reconcile_state is S.CLEARED
    back = client.set_split_state(book_id, first.id, split.id, S.NOT_RECONCILED)
    assert (
        next(s for s in back.splits if s.id == split.id).reconcile_state
        is S.NOT_RECONCILED
    )

    # A statement: both checking legs at once, offline first if offline.
    if kind == "offline":
        transport.plugged = False
    ids = [
        next(s for s in t.splits if s.account_id == checking).id for t in (first, second)
    ]
    client.reconcile_account(book_id, checking, ids, S.RECONCILED, date(2026, 2, 28))
    page = client.register(book_id, checking)
    assert {r.reconcile_state.value for r in page.items} == {"RECONCILED"}
    if kind == "offline":
        # The two marks and the statement all wait in the outbox, in order.
        assert client.status().queued == 3
        transport.plugged = True
        assert client.sync().queued == 0
    # The server has it, whichever way it got there.
    api.login(EMAIL)
    rows = api.get(f"/books/{book_id}/accounts/{checking}/register").json()["items"]
    assert all(r["reconcile_state"] == "RECONCILED" for r in rows)
    # And a reconciled transaction is locked for editing, as everywhere.
    with pytest.raises(ApiError) as refused:
        client.delete_transaction(book_id, first.id)
    assert refused.value.status == 409
    client.close()
