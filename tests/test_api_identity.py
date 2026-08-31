"""Onboarding and offboarding: activation, forgotten passwords, leaving.

The `api` fixture trusts addresses (no activation) so the rest of the suite
can register and go; the tests here build an app with activation required and
a memory mailer, and read the links the way a person would read their inbox.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from pymoney.api import create_app
from pymoney.api.mail import (
    ConsoleMailer,
    FileMailer,
    MemoryMailer,
    Message,
    mailer_from_settings,
)
from pymoney.api.maintenance import MaintenanceScheduler, purge
from pymoney.api.settings import Settings
from pymoney.core.clock import ManualClock
from pymoney.store import identity
from tests.conftest_api import PASSWORD, Client

LINK = re.compile(r"https?://\S+")


@pytest.fixture
def strict(tmp_path: Path):
    """An app that requires activation; `mailer.sent` is the inbox."""
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'strict.sqlite'}",
        create_schema=True,
        cookie_secure=False,
        allow_registration=True,
        require_activation=True,
        quote_interval_minutes=0,
        maintenance_interval_minutes=0,
        mail_backend="memory",
        public_url="https://money.example.org/",
    )
    mailer = MemoryMailer()
    app = create_app(settings=settings, clock=ManualClock(), mailer=mailer)
    with TestClient(app) as http:
        yield Client(http=http), mailer


def token_in(message: Message) -> str:
    link = LINK.search(message.body).group(0)
    return link.split("token=", 1)[1]


class TestActivation:
    def test_registering_sends_a_link_and_starts_no_session(self, strict):
        client, mailer = strict
        response = client.http.post(
            "/auth/register",
            json={
                "email": "new@example.invalid",
                "password": PASSWORD,
                "display_name": "New",
            },
        )
        assert response.status_code == 202
        assert response.json() == {"status": "pending", "email": "new@example.invalid"}
        assert "set-cookie" not in response.headers
        assert len(mailer.sent) == 1
        assert mailer.sent[0].to == "new@example.invalid"
        assert "https://money.example.org/activate?token=" in mailer.sent[0].body
        # No session yet: logging in says why.
        refused = client.http.post(
            "/auth/login", json={"email": "new@example.invalid", "password": PASSWORD}
        )
        assert (
            refused.status_code == 403
            and "not been activated" in refused.json()["detail"]
        )
        # A wrong password on a pending account is still just a 401.
        assert (
            client.http.post(
                "/auth/login", json={"email": "new@example.invalid", "password": "nope"}
            ).status_code
            == 401
        )

    def test_the_link_activates_logs_in_and_works_once(self, strict):
        client, mailer = strict
        client.http.post(
            "/auth/register", json={"email": "new@example.invalid", "password": PASSWORD}
        )
        token = token_in(mailer.sent[0])
        response = client.http.post("/auth/activate", json={"token": token})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user"]["email"] == "new@example.invalid" and body["token"]
        assert (
            client.http.post("/auth/activate", json={"token": token}).status_code == 410
        )
        assert (
            client.http.post(
                "/auth/login", json={"email": "new@example.invalid", "password": PASSWORD}
            ).status_code
            == 200
        )

    def test_a_garbage_or_expired_token_is_gone(self, strict, monkeypatch):
        client, mailer = strict
        assert (
            client.http.post(
                "/auth/activate", json={"token": "not-a-real-token"}
            ).status_code
            == 410
        )
        client.http.post(
            "/auth/register", json={"email": "late@example.invalid", "password": PASSWORD}
        )
        token = token_in(mailer.sent[-1])
        engine = client.http.app.state.engine
        with engine.begin() as conn:
            conn.execute(
                update(identity.user_tokens).values(
                    expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc)
                )
            )
        assert (
            client.http.post("/auth/activate", json={"token": token}).status_code == 410
        )

    def test_resend_replaces_the_link_and_reveals_nothing(self, strict):
        client, mailer = strict
        client.http.post(
            "/auth/register", json={"email": "new@example.invalid", "password": PASSWORD}
        )
        first = token_in(mailer.sent[0])
        assert (
            client.http.post(
                "/auth/resend-activation", json={"email": "new@example.invalid"}
            ).status_code
            == 202
        )
        second = token_in(mailer.sent[1])
        assert first != second
        assert (
            client.http.post("/auth/activate", json={"token": first}).status_code == 410
        )
        assert (
            client.http.post("/auth/activate", json={"token": second}).status_code == 200
        )
        # Unknown, or already activated: same answer, no mail.
        before = len(mailer.sent)
        assert (
            client.http.post(
                "/auth/resend-activation", json={"email": "nobody@example.invalid"}
            ).status_code
            == 202
        )
        assert (
            client.http.post(
                "/auth/resend-activation", json={"email": "new@example.invalid"}
            ).status_code
            == 202
        )
        assert len(mailer.sent) == before

    def test_registering_again_before_activating_resends_and_changes_nothing(
        self, strict
    ):
        """Anyone can type a stranger's address. Until the link is followed the
        pending row is not theirs to replace: the password and name stay as
        first registered, and the owner gets a fresh link in the same mailbox."""
        client, mailer = strict
        client.http.post(
            "/auth/register",
            json={
                "email": "new@example.invalid",
                "password": PASSWORD,
                "display_name": "The Owner",
            },
        )
        again = client.http.post(
            "/auth/register",
            json={
                "email": "New@example.invalid",
                "password": "an-impostors-password",
                "display_name": "Impostor",
            },
        )
        assert again.status_code == 202 and again.json()["email"] == "New@example.invalid"
        assert [m.to for m in mailer.sent] == ["new@example.invalid"] * 2
        # The first link is superseded, the second works; either way it is the
        # original account that activates.
        assert (
            client.http.post(
                "/auth/activate", json={"token": token_in(mailer.sent[0])}
            ).status_code
            == 410
        )
        activated = client.http.post(
            "/auth/activate", json={"token": token_in(mailer.sent[1])}
        )
        assert activated.status_code == 200
        assert activated.json()["user"]["display_name"] == "The Owner"
        assert (
            client.http.post(
                "/auth/login",
                json={
                    "email": "new@example.invalid",
                    "password": "an-impostors-password",
                },
            ).status_code
            == 401
        )
        assert (
            client.http.post(
                "/auth/login",
                json={"email": "new@example.invalid", "password": PASSWORD},
            ).status_code
            == 200
        )
        # Once activated, the address is taken.
        assert (
            client.http.post(
                "/auth/register",
                json={"email": "new@example.invalid", "password": PASSWORD},
            ).status_code
            == 409
        )


class TestForgottenPassword:
    def test_the_whole_reset_flow_logs_out_every_other_session(self, user: Client):
        mailer = user.http.app.state.mailer
        other = user.token  # a second device, still holding the old session
        response = user.http.post("/auth/forgot-password", json={"email": user.email})
        assert response.status_code == 202 and response.json()["status"] == "pending"
        assert mailer.sent[-1].subject == "Reset your PyMoney password"
        token = token_in(mailer.sent[-1])

        reset = user.http.post(
            "/auth/reset-password",
            json={"token": token, "password": "a brand new password"},
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["user"]["email"] == user.email
        # The old session is dead, the old password does not work, the new one does.
        assert (
            user.http.get(
                "/auth/me", headers={"Authorization": f"Bearer {other}"}
            ).status_code
            == 401
        )
        assert (
            user.http.post(
                "/auth/login", json={"email": user.email, "password": PASSWORD}
            ).status_code
            == 401
        )
        assert (
            user.http.post(
                "/auth/login",
                json={"email": user.email, "password": "a brand new password"},
            ).status_code
            == 200
        )
        # Single use.
        assert (
            user.http.post(
                "/auth/reset-password",
                json={"token": token, "password": "a brand new password"},
            ).status_code
            == 410
        )

    def test_unknown_addresses_get_the_same_answer_and_no_mail(self, api: Client):
        mailer = api.http.app.state.mailer
        assert (
            api.http.post(
                "/auth/forgot-password", json={"email": "nobody@example.invalid"}
            ).status_code
            == 202
        )
        assert mailer.sent == []

    def test_a_pending_account_gets_no_reset_link(self, strict):
        client, mailer = strict
        client.http.post(
            "/auth/register", json={"email": "new@example.invalid", "password": PASSWORD}
        )
        before = len(mailer.sent)
        assert (
            client.http.post(
                "/auth/forgot-password", json={"email": "new@example.invalid"}
            ).status_code
            == 202
        )
        assert len(mailer.sent) == before

    def test_a_short_new_password_is_refused(self, user: Client):
        assert (
            user.http.post(
                "/auth/reset-password", json={"token": "x" * 20, "password": "short"}
            ).status_code
            == 422
        )


class TestDeletingABook:
    def test_owner_deletes_with_the_name_as_confirmation(self, user: Client):
        book_id = user.make_book("Old Books")
        user.account("Cash", "CASH")
        refused = user.delete(f"/books/{book_id}", json={"name": "old books"})
        assert refused.status_code == 422 and "exact name" in refused.json()["detail"]
        assert user.get(f"/books/{book_id}").status_code == 200
        assert (
            user.delete(f"/books/{book_id}", json={"name": "Old Books"}).status_code
            == 204
        )
        assert user.get(f"/books/{book_id}").status_code == 404
        assert user.get("/books").json() == []
        # Nothing of it is left in the database, grants included.
        engine = user.http.app.state.engine
        from uuid import UUID

        from pymoney.store import schema

        with engine.connect() as conn:
            for table in (*schema.ALL_TABLES, identity.book_grants):
                column = table.c.book_id if "book_id" in table.c else table.c.id
                assert (
                    conn.execute(select(column).where(column == UUID(book_id))).all()
                    == []
                ), table.name

    def test_editors_cannot(self, api: Client):
        api.register("owner@example.invalid")
        book_id = api.make_book("Shared")
        api.put(
            f"/books/{book_id}/grants",
            json={"subject": "staff@example.invalid", "role": "editor"},
        )
        api.logout()
        api.register("staff@example.invalid")
        assert api.delete(f"/books/{book_id}", json={"name": "Shared"}).status_code == 403

    def test_the_book_can_be_created_again_and_is_a_new_book(self, user: Client):
        first = user.make_book("Twice")
        user.delete(f"/books/{first}", json={"name": "Twice"})
        second = user.make_book("Twice")
        assert second != first
        assert user.get(f"/books/{second}/accounts").status_code == 200


class TestDeletingTheAccount:
    def test_refused_while_owning_a_book(self, user: Client):
        user.make_book("Mine")
        response = user.delete("/auth/me", json={"password": PASSWORD})
        assert (
            response.status_code == 409
            and "still own 1 book" in response.json()["detail"]
        )

    def test_needs_the_password(self, user: Client):
        assert user.delete("/auth/me", json={"password": "wrong"}).status_code == 403
        assert user.get("/auth/me").status_code == 200

    def test_deletes_the_user_their_sessions_and_grants_on_others_books(
        self, api: Client
    ):
        api.register("owner@example.invalid")
        book_id = api.make_book("Theirs")
        api.put(
            f"/books/{book_id}/grants",
            json={"subject": "guest@example.invalid", "role": "viewer"},
        )
        api.logout()
        api.register("guest@example.invalid")
        assert api.get("/books").json()[0]["name"] == "Theirs"
        token = api.token
        assert api.delete("/auth/me", json={"password": PASSWORD}).status_code == 204
        assert (
            api.http.get(
                "/auth/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )
        assert (
            api.http.post(
                "/auth/login",
                json={"email": "guest@example.invalid", "password": PASSWORD},
            ).status_code
            == 401
        )
        api.login("owner@example.invalid")
        assert [g["subject"] for g in api.get(f"/books/{book_id}/grants").json()] == [
            "owner@example.invalid"
        ]
        # The address is free again.
        api.logout()
        assert (
            api.http.post(
                "/auth/register",
                json={"email": "guest@example.invalid", "password": PASSWORD},
            ).status_code
            == 201
        )

    def test_hand_the_book_over_then_leave(self, api: Client):
        api.register("owner@example.invalid")
        book_id = api.make_book("Handover")
        api.put(
            f"/books/{book_id}/grants",
            json={"subject": "heir@example.invalid", "role": "owner"},
        )
        api.delete(f"/books/{book_id}/grants/owner@example.invalid")
        assert api.delete("/auth/me", json={"password": PASSWORD}).status_code == 204


class TestMaintenance:
    def test_purge_removes_what_expired_and_never_activated_accounts(self, strict):
        client, mailer = strict
        engine = client.http.app.state.engine
        now = datetime.now(timezone.utc)
        # A pending account from long ago, and one from just now.
        client.http.post(
            "/auth/register", json={"email": "old@example.invalid", "password": PASSWORD}
        )
        client.http.post(
            "/auth/register",
            json={"email": "fresh@example.invalid", "password": PASSWORD},
        )
        with engine.begin() as conn:
            conn.execute(
                update(identity.users)
                .where(identity.users.c.email_key == "old@example.invalid")
                .values(created_at=now - timedelta(days=30))
            )
        # An activated account with an expired session and a used token.
        client.http.post(
            "/auth/register", json={"email": "real@example.invalid", "password": PASSWORD}
        )
        client.http.post("/auth/activate", json={"token": token_in(mailer.sent[-1])})
        with engine.begin() as conn:
            conn.execute(
                update(identity.sessions).values(expires_at=now - timedelta(days=1))
            )

        report = purge(engine, unactivated_after=timedelta(days=7), now=now)
        assert (report.sessions, report.tokens, report.unactivated_users) == (1, 1, 1)
        with engine.connect() as conn:
            left = {row.email for row in conn.execute(select(identity.users.c.email))}
        assert left == {"fresh@example.invalid", "real@example.invalid"}
        # The purged address can register again.
        assert (
            client.http.post(
                "/auth/register",
                json={"email": "old@example.invalid", "password": PASSWORD},
            ).status_code
            == 202
        )
        assert purge(engine, unactivated_after=timedelta(days=7), now=now).total == 0

    def test_the_scheduler_runs_and_stops(self, strict):
        client, _ = strict
        scheduler = MaintenanceScheduler(
            client.http.app.state.engine,
            interval=timedelta(milliseconds=10),
            unactivated_after=timedelta(days=7),
        )
        scheduler.start()
        import time

        deadline = time.time() + 5
        while scheduler.runs == 0 and time.time() < deadline:
            time.sleep(0.01)
        scheduler.stop()
        assert scheduler.runs >= 1 and not scheduler.running
        with pytest.raises(ValueError):
            MaintenanceScheduler(
                client.http.app.state.engine,
                interval=timedelta(0),
                unactivated_after=timedelta(days=1),
            )

    def test_the_app_runs_one_when_asked(self, tmp_path):
        settings = Settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / 'm.sqlite'}",
            create_schema=True,
            cookie_secure=False,
            quote_interval_minutes=0,
            maintenance_interval_minutes=30,
            mail_backend="memory",
        )
        with TestClient(create_app(settings=settings)) as http:
            assert (
                http.app.state.maintenance is not None
                and http.app.state.maintenance.running
            )
        assert not http.app.state.maintenance.running


class TestMailers:
    def test_file_and_console_and_selection(self, tmp_path, capsys):
        message = Message(
            to="a@example.invalid",
            subject="Hi",
            body="link: https://x/activate?token=abc",
        )
        path = tmp_path / "mail" / "out.jsonl"
        FileMailer(path).send(message)
        FileMailer(path).send(message)
        lines = path.read_text().splitlines()
        assert len(lines) == 2 and '"to": "a@example.invalid"' in lines[0]
        ConsoleMailer().send(message)
        assert "mail to a@example.invalid: Hi" in capsys.readouterr().err

        assert isinstance(
            mailer_from_settings(Settings(mail_backend="console")), ConsoleMailer
        )
        assert isinstance(
            mailer_from_settings(Settings(mail_backend="memory")), MemoryMailer
        )
        assert isinstance(
            mailer_from_settings(Settings(mail_backend="file", mail_file=str(path))),
            FileMailer,
        )
        with pytest.raises(ValueError, match="SMTP_HOST"):
            mailer_from_settings(Settings(mail_backend="smtp"))
        with pytest.raises(ValueError, match="unknown mail backend"):
            mailer_from_settings(Settings(mail_backend="pigeon"))

    def test_smtp_mailer_talks_smtp(self, monkeypatch):
        from pymoney.api import mail

        calls: list[str] = []

        class FakeSmtp:
            def __init__(self, host, port, timeout):
                calls.append(f"connect {host}:{port}")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                calls.append("quit")

            def starttls(self):
                calls.append("starttls")

            def login(self, user, password):
                calls.append(f"login {user}")

            def send_message(self, msg):
                calls.append(f"send {msg['To']} {msg['Subject']}")

        monkeypatch.setattr(mail.smtplib, "SMTP", FakeSmtp)
        mailer = mailer_from_settings(
            Settings(
                mail_backend="smtp",
                smtp_host="mail.example.org",
                smtp_username="u",
                smtp_password="p",
            )
        )
        mailer.send(Message(to="a@example.invalid", subject="Hi", body="x"))
        assert calls == [
            "connect mail.example.org:587",
            "starttls",
            "login u",
            "send a@example.invalid Hi",
            "quit",
        ]


def test_the_migration_activates_everyone_who_already_existed(tmp_path):
    """An upgrade must not turn today's users into pending accounts the
    maintenance job would purge."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    from pymoney.core.ids import new_id
    from pymoney.store import create_engine, migrate

    url = f"sqlite+pysqlite:///{tmp_path / 'up.sqlite'}"
    migrate.upgrade(url, "fb97e811744f")  # the revision before activation existed
    engine = create_engine(url)
    then = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, email_key, display_name, active, created_at, updated_at) "
                "VALUES (:id, 'old@example.invalid', 'old@example.invalid', 'Old', 1, :at, :at)"
            ),
            {"id": new_id().bytes, "at": then.strftime("%Y-%m-%d %H:%M:%S.%f")},
        )
    engine.dispose()
    migrate.upgrade(url)
    engine = create_engine(url)
    with engine.connect() as conn:
        row = conn.execute(
            select(identity.users.c.activated_at, identity.users.c.created_at)
        ).one()
    engine.dispose()
    assert row.activated_at == row.created_at == then
