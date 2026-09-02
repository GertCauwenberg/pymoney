"""Authentication, CSRF and per-book access control."""

from __future__ import annotations

import pytest

from pymoney.api.security import (
    PasswordHash,
    hash_password,
    hash_token,
    idempotency_key_to_uuid,
    new_session_token,
    verify_password,
)
from tests.conftest_api import PASSWORD, Client


class TestPasswordHashing:
    def test_round_trip(self):
        stored = hash_password("a long enough passphrase")
        assert verify_password("a long enough passphrase", stored)
        assert not verify_password("a long enough passphras", stored)

    def test_the_password_is_not_recoverable(self):
        stored = hash_password("secret passphrase here")
        assert b"secret" not in stored.digest
        assert len(stored.salt) == 32

    def test_salts_differ(self):
        """Two users with the same password must not share a hash."""
        first = hash_password(PASSWORD)
        second = hash_password(PASSWORD)
        assert first.salt != second.salt
        assert first.digest != second.digest

    def test_no_password_is_not_any_password(self):
        assert not verify_password("", None)
        assert not verify_password("anything", None)

    def test_a_missing_record_costs_a_full_derivation(self, monkeypatch):
        """Otherwise "no such user" answers a hundred times faster than "wrong
        password", and the response time enumerates the registered addresses."""
        from pymoney.api import security

        calls: list[bytes] = []
        real = security._derive

        def counting(password, salt, params):
            calls.append(salt)
            return real(password, salt, params)

        stored = hash_password(PASSWORD)
        monkeypatch.setattr(security, "_derive", counting)
        assert verify_password(PASSWORD, stored)
        assert not verify_password(PASSWORD, None)
        assert not verify_password(PASSWORD, PasswordHash(b"", b"", stored.params))
        assert len(calls) == 3
        # The decoy is never a match, whatever the password.
        assert not verify_password("", None)

    def test_the_parameters_travel_with_the_hash(self):
        """So they can be raised later without locking anyone out."""
        stored = hash_password(PASSWORD)
        assert stored.parsed_params == (2**15, 8, 1)

    def test_an_absurd_password_is_refused_not_hashed(self):
        with pytest.raises(ValueError, match="the limit is"):
            hash_password("x" * 2000)

    def test_a_corrupt_stored_record_fails_closed(self):
        from pymoney.api.security import PasswordHash

        broken = PasswordHash(digest=b"x", salt=b"y", params="not,numbers,here")
        assert not verify_password(PASSWORD, broken)


class TestTokens:
    def test_session_tokens_are_unguessable_and_unique(self):
        tokens = {new_session_token() for _ in range(200)}
        assert len(tokens) == 200
        assert all(len(t) > 40 for t in tokens)

    def test_only_the_hash_is_stored(self):
        token = new_session_token()
        assert hash_token(token) != token.encode()
        assert len(hash_token(token)) == 32

    def test_idempotency_keys_accept_a_uuid_unchanged(self):
        import uuid

        value = uuid.uuid4()
        assert idempotency_key_to_uuid(str(value)) == value

    def test_idempotency_keys_accept_anything_deterministically(self):
        """Clients in the wild do not all send UUIDs."""
        first = idempotency_key_to_uuid("mobile-retry-42")
        assert first == idempotency_key_to_uuid("mobile-retry-42")
        assert first != idempotency_key_to_uuid("mobile-retry-43")


class TestRegistration:
    def test_creates_a_user_and_logs_in(self, api: Client):
        body = api.register("new@example.invalid")
        assert body["user"]["email"] == "new@example.invalid"
        assert body["csrf_token"]
        assert body["token"]
        assert api.get("/auth/me").json()["email"] == "new@example.invalid"

    def test_sets_an_httponly_cookie(self, api: Client):
        api.register("new@example.invalid")
        cookie = api.http.cookies.jar._cookies  # type: ignore[attr-defined]
        assert api.http.cookies.get("pymoney_session")
        del cookie
        # The response header is what a browser actually reads.
        response = api.http.post(
            "/auth/login",
            json={"email": "new@example.invalid", "password": PASSWORD},
        )
        header = response.headers["set-cookie"]
        assert "HttpOnly" in header
        assert "SameSite=lax" in header or "samesite=lax" in header.lower()

    def test_duplicate_email(self, api: Client):
        api.register("dup@example.invalid")
        response = api.http.post(
            "/auth/register",
            json={"email": "dup@example.invalid", "password": PASSWORD},
        )
        assert response.status_code == 409

    def test_email_is_matched_case_insensitively(self, api: Client):
        api.register("Mixed@Example.invalid")
        response = api.http.post(
            "/auth/register",
            json={"email": "mixed@example.invalid", "password": PASSWORD},
        )
        assert response.status_code == 409

    def test_a_short_password_is_refused(self, api: Client):
        response = api.http.post(
            "/auth/register", json={"email": "x@example.invalid", "password": "short"}
        )
        assert response.status_code == 422

    def test_something_that_is_not_an_email(self, api: Client):
        response = api.http.post(
            "/auth/register", json={"email": "nope", "password": PASSWORD}
        )
        assert response.status_code == 422

    def test_registration_can_be_closed(self, tmp_path):
        from fastapi.testclient import TestClient

        from pymoney.api import create_app
        from pymoney.api.settings import Settings

        app = create_app(
            settings=Settings(
                database_url=f"sqlite+pysqlite:///{tmp_path / 'closed.sqlite'}",
                create_schema=True,
                allow_registration=False,
            )
        )
        with TestClient(app) as http:
            response = http.post(
                "/auth/register",
                json={"email": "x@example.invalid", "password": PASSWORD},
            )
            assert response.status_code == 403


class TestLogin:
    def test_works(self, api: Client):
        api.register("a@example.invalid")
        api.logout()
        body = api.login("a@example.invalid")
        assert body["user"]["email"] == "a@example.invalid"

    def test_wrong_password(self, api: Client):
        api.register("a@example.invalid")
        response = api.http.post(
            "/auth/login", json={"email": "a@example.invalid", "password": "wrong one"}
        )
        assert response.status_code == 401

    def test_unknown_user_says_the_same_thing(self, api: Client):
        """Not "no such user": that enumerates registered addresses."""
        response = api.http.post(
            "/auth/login", json={"email": "ghost@example.invalid", "password": PASSWORD}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "wrong email or password"

    def test_attempts_against_one_account_are_throttled(self, api: Client):
        """Five per minute per address, from anywhere; the sixth is a 429 with
        Retry-After, and it does not extend the penalty. The limit is on
        attempts, so the genuine password is refused too -- for a minute."""
        api.register("a@example.invalid")
        api.logout()
        for _ in range(5):
            response = api.http.post(
                "/auth/login", json={"email": "A@example.invalid", "password": "wrong"}
            )
            assert response.status_code == 401
        for _ in range(3):
            response = api.http.post(
                "/auth/login", json={"email": "a@example.invalid", "password": PASSWORD}
            )
            assert response.status_code == 429
            assert int(response.headers["Retry-After"]) >= 1
            assert "try again" in response.json()["detail"]
        # Another account from the same client is still fine: 8 < 20.
        api.register("b@example.invalid")
        # Time passes; the bucket drains.
        from time import monotonic

        throttle = api.http.app.state.throttle
        for limiter in (throttle.login_by_account, throttle.login_by_client):
            limiter.now = lambda: monotonic() + 61
        assert (
            api.http.post(
                "/auth/login", json={"email": "a@example.invalid", "password": PASSWORD}
            ).status_code
            == 200
        )

    def test_one_client_cannot_hammer_many_accounts(self, api: Client):
        for n in range(20):
            response = api.http.post(
                "/auth/login", json={"email": f"u{n}@example.invalid", "password": "x"}
            )
            assert response.status_code == 401
        assert (
            api.http.post(
                "/auth/login", json={"email": "u99@example.invalid", "password": "x"}
            ).status_code
            == 429
        )

    def test_the_proxy_header_is_only_trusted_when_configured(self, api: Client):
        """A client may write X-Forwarded-For too; it must not pick its bucket."""
        from pymoney.api.ratelimit import client_address

        throttle = api.http.app.state.throttle
        settings = api.http.app.state.settings
        for n in range(20):
            api.http.post(
                "/auth/login",
                json={"email": f"u{n}@example.invalid", "password": "x"},
                headers={"X-Forwarded-For": f"10.0.0.{n}"},
            )
        assert (
            api.http.post(
                "/auth/login", json={"email": "u99@example.invalid", "password": "x"}
            ).status_code
            == 429
        )
        # Configured, the header names the client, and the proxy's own address
        # is no longer the bucket.
        settings.client_ip_header = "X-Forwarded-For"
        assert (
            api.http.post(
                "/auth/login",
                json={"email": "u99@example.invalid", "password": "x"},
                headers={"X-Forwarded-For": "10.0.0.99, 192.168.0.1"},
            ).status_code
            == 401
        )
        del throttle, client_address

    def test_mail_sending_endpoints_are_throttled_per_address(self, api: Client):
        """Three emails a minute for one address, however it is capitalised."""
        codes = [
            api.http.post("/auth/forgot-password", json={"email": e}).status_code
            for e in ("x@example.invalid", "X@example.invalid", " x@example.invalid ")
        ]
        assert codes == [202, 202, 202]
        assert (
            api.http.post(
                "/auth/resend-activation", json={"email": "x@example.invalid"}
            ).status_code
            == 429
        )
        assert (
            api.http.post(
                "/auth/register",
                json={"email": "x@example.invalid", "password": PASSWORD},
            ).status_code
            == 429
        )
        # Other addresses are unaffected.
        assert (
            api.http.post(
                "/auth/forgot-password", json={"email": "y@example.invalid"}
            ).status_code
            == 202
        )

    def test_me_hands_the_csrf_token_back_to_its_cookie_session(self, api: Client):
        """A fresh browser tab shares the cookie but not sessionStorage; /me is
        how it gets its CSRF token back -- and the token must actually work."""
        session = api.register("a@example.invalid")
        response = api.http.get("/auth/me")
        assert response.headers["cache-control"] == "no-store"
        token = response.json()["csrf_token"]
        assert token == session["csrf_token"]
        # A write with the recovered token, and none without.
        ok = api.http.post(
            "/books",
            json={"name": "B", "base_currency_code": "EUR"},
            headers={"X-CSRF-Token": token},
        )
        assert ok.status_code == 201
        refused = api.http.post("/books", json={"name": "C", "base_currency_code": "EUR"})
        assert refused.status_code == 403
        # A bearer client gets no token: it has no ambient credential to bind it to.
        bare = api.http.get(
            "/auth/me", headers={"Authorization": f"Bearer {session['token']}"}
        )
        assert bare.json()["csrf_token"] is None

    def test_logout_invalidates_the_session(self, api: Client):
        api.register("a@example.invalid")
        api.logout()
        assert api.get("/auth/me").status_code == 401


class TestCredentials:
    def test_no_credentials(self, api: Client):
        assert api.get("/auth/me").status_code == 401

    def test_a_bogus_token(self, api: Client):
        response = api.http.get(
            "/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_a_bearer_token_works_without_cookies(self, api: Client):
        body = api.register("a@example.invalid")
        api.http.cookies.clear()
        response = api.http.get(
            "/auth/me", headers={"Authorization": f"Bearer {body['token']}"}
        )
        assert response.status_code == 200

    def test_a_bearer_client_needs_no_csrf_token(self, api: Client):
        """It has no ambient credentials, so there is nothing to forge."""
        body = api.register("a@example.invalid")
        api.http.cookies.clear()
        response = api.http.post(
            "/books",
            json={"name": "B", "base_currency_code": "EUR"},
            headers={"Authorization": f"Bearer {body['token']}"},
        )
        assert response.status_code == 201

    def test_a_cookie_write_without_the_csrf_token_is_refused(self, api: Client):
        """The cookie travels automatically, so on its own it proves nothing."""
        api.register("a@example.invalid")
        response = api.http.post(
            "/books", json={"name": "B", "base_currency_code": "EUR"}
        )
        assert response.status_code == 403
        assert "X-CSRF-Token" in response.json()["detail"]

    def test_a_cookie_write_with_the_wrong_csrf_token_is_refused(self, api: Client):
        api.register("a@example.invalid")
        response = api.http.post(
            "/books",
            json={"name": "B", "base_currency_code": "EUR"},
            headers={"X-CSRF-Token": "guessed"},
        )
        assert response.status_code == 403

    def test_a_cookie_read_needs_no_csrf_token(self, api: Client):
        api.register("a@example.invalid")
        assert api.http.get("/auth/me").status_code == 200

    def test_an_expired_session_is_rejected_and_removed(self, api: Client, tmp_path):
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select, update

        from pymoney.store import identity

        api.register("a@example.invalid")
        engine = api.http.app.state.engine
        with engine.begin() as conn:
            conn.execute(
                update(identity.sessions).values(
                    expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
                )
            )
        assert api.get("/auth/me").status_code == 401
        with engine.connect() as conn:
            assert conn.execute(select(identity.sessions.c.id)).all() == []

    def test_an_idle_session_is_rejected(self, api: Client):
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import update

        from pymoney.store import identity

        api.register("a@example.invalid")
        engine = api.http.app.state.engine
        with engine.begin() as conn:
            conn.execute(
                update(identity.sessions).values(
                    last_seen_at=datetime.now(timezone.utc) - timedelta(days=365)
                )
            )
        assert api.get("/auth/me").status_code == 401

    def test_a_deactivated_user_cannot_use_their_session(self, api: Client):
        from sqlalchemy import update

        from pymoney.store import identity

        api.register("a@example.invalid")
        engine = api.http.app.state.engine
        with engine.begin() as conn:
            conn.execute(update(identity.users).values(active=False))
        assert api.get("/auth/me").status_code == 401


class TestBookAccess:
    def test_the_creator_owns_the_book(self, user: Client):
        book_id = user.make_book()
        body = user.get(f"/books/{book_id}").json()
        assert body["role"] == "owner"

    def test_only_granted_books_are_listed(self, api: Client):
        api.register("one@example.invalid")
        mine = api.make_book("Mine")
        api.logout()
        api.register("two@example.invalid")
        theirs = api.make_book("Theirs")
        listed = {b["id"] for b in api.get("/books").json()}
        assert listed == {theirs}
        assert mine not in listed

    def test_another_users_book_is_a_404_not_a_403(self, api: Client):
        """Whether a book exists is itself information the caller has no grant to."""
        api.register("one@example.invalid")
        mine = api.make_book("Mine")
        api.logout()
        api.register("two@example.invalid")
        assert api.get(f"/books/{mine}").status_code == 404
        assert api.get(f"/books/{mine}/accounts").status_code == 404

    def test_a_viewer_can_read_but_not_write(self, api: Client):
        api.register("owner@example.invalid")
        book_id = api.make_book()
        cash = api.account("Cash", "CASH")
        food = api.account("Food", "EXPENSE")
        api.put(
            f"/books/{book_id}/grants",
            json={"subject": "guest@example.invalid", "role": "viewer"},
        )
        api.logout()
        api.register("guest@example.invalid")
        assert api.get(f"/books/{book_id}/accounts").status_code == 200
        response = api.post(
            f"/books/{book_id}/transactions",
            json={
                "date": "2026-01-01",
                "legs": [
                    {"account_id": cash["id"], "value": "-1.00"},
                    {"account_id": food["id"]},
                ],
            },
        )
        assert response.status_code == 403
        assert "editor" in response.json()["detail"]
        assert api.post(f"/books/{book_id}/payees", json={"name": "X"}).status_code == 403

    def test_an_editor_posts_but_does_not_reshape_the_chart(self, api: Client):
        """Transactions and payees are an editor's; the account tree, like
        sharing, reshapes every report the household sees and is the owner's."""
        api.register("owner@example.invalid")
        book_id = api.make_book()
        cash = api.account("Cash", "CASH")
        food = api.account("Food", "EXPENSE")
        api.put(
            f"/books/{book_id}/grants",
            json={"subject": "staff@example.invalid", "role": "editor"},
        )
        api.logout()
        api.register("staff@example.invalid")
        posted = api.post(
            f"/books/{book_id}/transactions",
            json={
                "date": "2026-01-01",
                "legs": [
                    {"account_id": cash["id"], "value": "-1.00"},
                    {"account_id": food["id"]},
                ],
            },
        )
        assert posted.status_code == 201
        assert (
            api.post(f"/books/{book_id}/payees", json={"name": "Shop"}).status_code == 201
        )
        assert (
            api.patch(
                f"/books/{book_id}/transactions/{posted.json()['id']}",
                json={"memo": "edited"},
            ).status_code
            == 200
        )

        refused = api.post(
            f"/books/{book_id}/accounts", json={"name": "X", "type": "CASH"}
        )
        assert refused.status_code == 403
        assert "owner" in refused.json()["detail"]
        for method, url, body in (
            ("patch", f"/books/{book_id}/accounts/{cash['id']}", {"notes": "n"}),
            ("post", f"/books/{book_id}/accounts/{cash['id']}/rename", {"name": "Y"}),
            ("post", f"/books/{book_id}/accounts/{cash['id']}/move", {"parent_id": None}),
            ("post", f"/books/{book_id}/accounts/{cash['id']}/closed", {"closed": True}),
            ("delete", f"/books/{book_id}/accounts/{cash['id']}", None),
        ):
            response = getattr(api, method)(
                url, **({} if body is None else {"json": body})
            )
            assert response.status_code == 403, (method, url, response.text)
        assert (
            api.put(
                f"/books/{book_id}/grants",
                json={"subject": "x@example.invalid", "role": "viewer"},
            ).status_code
            == 403
        )

    def test_grants_can_be_listed_and_revoked(self, api: Client):
        api.register("owner@example.invalid")
        book_id = api.make_book()
        api.put(
            f"/books/{book_id}/grants",
            json={"subject": "guest@example.invalid", "role": "viewer"},
        )
        subjects = {g["subject"] for g in api.get(f"/books/{book_id}/grants").json()}
        assert subjects == {"owner@example.invalid", "guest@example.invalid"}
        assert (
            api.delete(f"/books/{book_id}/grants/guest@example.invalid").status_code
            == 204
        )
        assert len(api.get(f"/books/{book_id}/grants").json()) == 1

    def test_a_role_can_be_changed(self, api: Client):
        api.register("owner@example.invalid")
        book_id = api.make_book()
        for role in ("viewer", "editor"):
            api.put(
                f"/books/{book_id}/grants",
                json={"subject": "guest@example.invalid", "role": role},
            )
        grants = {
            g["subject"]: g["role"] for g in api.get(f"/books/{book_id}/grants").json()
        }
        assert grants["guest@example.invalid"] == "editor"

    def test_the_last_owner_cannot_be_revoked_or_demoted(self, api: Client):
        """A book with no owner could never be administered again -- and a
        PUT to a lower role orphans it exactly as a DELETE would."""
        api.register("owner@example.invalid")
        book_id = api.make_book()
        response = api.delete(f"/books/{book_id}/grants/owner@example.invalid")
        assert response.status_code == 409
        assert "only owner" in response.json()["detail"]
        for role in ("viewer", "editor"):
            response = api.put(
                f"/books/{book_id}/grants",
                json={"subject": "Owner@Example.invalid", "role": role},
            )
            assert response.status_code == 409, role
        # Still the owner, so still able to administer.
        assert api.get(f"/books/{book_id}/grants").status_code == 200
        # With a second owner, stepping down is allowed.
        api.put(
            f"/books/{book_id}/grants",
            json={"subject": "co@example.invalid", "role": "owner"},
        )
        response = api.put(
            f"/books/{book_id}/grants",
            json={"subject": "owner@example.invalid", "role": "viewer"},
        )
        assert response.status_code == 200
        assert api.get(f"/books/{book_id}/grants").status_code == 403

    def test_grants_match_logins_whatever_the_capitalisation(self, api: Client):
        """Logins are case-insensitive; grants must be too, or the Sharing page
        silently gives nobody anything."""
        api.register("Alice@Example.org")
        book_id = api.make_book()
        response = api.put(
            f"/books/{book_id}/grants",
            json={"subject": " Bob@Example.org ", "role": "editor"},
        )
        assert response.status_code == 200
        assert response.json()["subject"] == "bob@example.org"
        assert [g["subject"] for g in api.get(f"/books/{book_id}/grants").json()] == [
            "alice@example.org",
            "bob@example.org",
        ]
        api.logout()
        api.register("bob@example.org")
        assert [b["id"] for b in api.get("/books").json()] == [book_id]
        assert api.get(f"/books/{book_id}").json()["role"] == "editor"
        # The user still sees their address as they wrote it.
        api.logout()
        api.login("alice@example.org")
        assert api.get("/auth/me").json()["email"] == "Alice@Example.org"
        # Revoking by any spelling works, and is refused for an opaque subject
        # only when spelled exactly.
        assert api.delete(f"/books/{book_id}/grants/BOB@example.org").status_code == 204
        api.put(
            f"/books/{book_id}/grants", json={"subject": "OIDC|AbC", "role": "viewer"}
        )
        assert [g["subject"] for g in api.get(f"/books/{book_id}/grants").json()] == [
            "OIDC|AbC",
            "alice@example.org",
        ]

    def test_an_unknown_book_is_404(self, user: Client):
        from pymoney.core import new_id

        assert user.get(f"/books/{new_id()}").status_code == 404
