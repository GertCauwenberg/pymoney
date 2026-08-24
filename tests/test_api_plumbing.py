"""The parts of the API that are not endpoints: registry, settings, factory."""

from __future__ import annotations

import pytest

from pymoney.api import Settings, create_app
from pymoney.api.registry import BookRegistry
from pymoney.core import ConflictError, NotFoundError, new_id
from pymoney.core.clock import ManualClock
from pymoney.core.commodity import Commodity
from pymoney.store import create_engine, create_schema
from tests.conftest_api import Client, Ledger


@pytest.fixture
def registry(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'reg.sqlite'}")
    create_schema(engine)
    registry = BookRegistry(engine, clock=ManualClock())
    yield registry
    registry.close()
    engine.dispose()


class TestRegistry:
    def test_one_book_object_is_reused(self, registry):
        """Reopening per request would reload the caches and, worse, lose the
        idempotency cache that makes Idempotency-Key work."""
        book = registry.create(name="B", base_currency=Commodity.currency("EUR"))
        with registry.open(book.id) as first, registry.open(book.id) as second:
            assert first is second is book

    def test_the_lock_is_re_entrant(self, registry):
        """A dependency holding it must not deadlock a nested open."""
        book = registry.create(name="B", base_currency=Commodity.currency("EUR"))
        with registry.open(book.id), registry.open(book.id) as inner:
            assert inner is book

    def test_an_unknown_book(self, registry):
        with pytest.raises(NotFoundError), registry.open(new_id()):
            pass

    def test_a_duplicate_id(self, registry):
        book = registry.create(name="B", base_currency=Commodity.currency("EUR"))
        with pytest.raises(ConflictError, match="already exists"):
            registry.create(
                name="again",
                base_currency=Commodity.currency("USD"),
                book_id=book.id,
            )

    def test_known_ids_and_exists(self, registry):
        book = registry.create(name="B", base_currency=Commodity.currency("EUR"))
        assert registry.known_ids() == (book.id,)
        assert registry.exists(book.id)
        assert not registry.exists(new_id())

    def test_forget_closes_and_allows_reopening(self, registry):
        book = registry.create(name="B", base_currency=Commodity.currency("EUR"))
        revision = book.revision
        registry.forget(book.id)
        registry.forget(book.id)  # idempotent
        with registry.open(book.id) as reopened:
            assert reopened is not book
            assert reopened.revision == revision

    def test_forgetting_an_unknown_book_is_harmless(self, registry):
        registry.forget(new_id())

    def test_repr(self, registry):
        assert "0 open" in repr(registry)


class TestSettings:
    def test_the_unsafe_defaults_are_reported(self):
        problems = Settings(
            database_url="sqlite+pysqlite:///:memory:",
            cookie_secure=False,
            allow_registration=True,
            cors_origins=["*"],
        ).production_warnings()
        joined = " ".join(problems)
        assert "COOKIE_SECURE" in joined
        assert "nothing will be kept" in joined
        assert "exact origins" in joined
        assert "create an account" in joined

    def test_a_locked_down_install_warns_about_nothing(self):
        assert (
            Settings(
                database_url="postgresql+psycopg://u:p@db/pymoney",
                cookie_secure=True,
                allow_registration=False,
            ).production_warnings()
            == []
        )

    def test_the_env_prefix(self, monkeypatch):
        monkeypatch.setenv("PYMONEY_COOKIE_NAME", "custom")
        assert Settings().cookie_name == "custom"


class TestAppFactory:
    def test_a_supplied_engine_is_not_disposed(self, tmp_path):
        """It is the caller's to close; we did not open it."""
        from fastapi.testclient import TestClient

        engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'own.sqlite'}")
        create_schema(engine)
        app = create_app(settings=Settings(cookie_secure=False), engine=engine)
        with TestClient(app) as http:
            assert http.get("/health").status_code == 200
        # Still usable after the app has shut down.
        with engine.connect():
            pass
        engine.dispose()

    def test_cors_is_configured_for_exact_origins(self, tmp_path):
        from fastapi.testclient import TestClient

        app = create_app(
            settings=Settings(
                database_url=f"sqlite+pysqlite:///{tmp_path / 'cors.sqlite'}",
                create_schema=True,
                cors_origins=["https://app.example.invalid"],
            )
        )
        with TestClient(app) as http:
            response = http.get(
                "/health", headers={"Origin": "https://app.example.invalid"}
            )
        assert (
            response.headers["access-control-allow-origin"]
            == "https://app.example.invalid"
        )
        assert response.headers["access-control-allow-credentials"] == "true"
        assert "X-PyMoney-Revision" in response.headers.get(
            "access-control-expose-headers", ""
        )

    def test_the_module_level_app_is_lazy(self, monkeypatch, tmp_path):
        """`uvicorn pymoney.api:app` must work, but importing the package must
        not open a database."""
        import pymoney.api as package

        monkeypatch.setenv(
            "PYMONEY_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'lazy.sqlite'}"
        )
        monkeypatch.delitem(vars(package), "app", raising=False)
        application = package.app
        assert application.title == "PyMoney"
        assert package.app is application  # cached

    def test_an_unknown_attribute_still_raises(self):
        import pymoney.api as package

        with pytest.raises(AttributeError):
            _ = package.nonsense


class TestRemainingEndpoints:
    def test_get_one_commodity(self, ledger: Ledger):
        body = ledger.client.get(
            f"/books/{ledger.book_id}/commodities/{ledger.eur}"
        ).json()
        assert body["code"] == "EUR"

    def test_delete_an_unused_commodity(self, ledger: Ledger):
        created = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "CHF"}
        ).json()
        assert (
            ledger.client.delete(
                f"/books/{ledger.book_id}/commodities/{created['id']}"
            ).status_code
            == 204
        )

    def test_get_and_delete_a_payee(self, ledger: Ledger):
        payee = ledger.client.post(
            f"/books/{ledger.book_id}/payees", json={"name": "Shop"}
        ).json()
        assert (
            ledger.client.get(f"/books/{ledger.book_id}/payees/{payee['id']}").json()[
                "name"
            ]
            == "Shop"
        )
        assert (
            ledger.client.delete(
                f"/books/{ledger.book_id}/payees/{payee['id']}"
            ).status_code
            == 204
        )

    def test_delete_a_price(self, ledger: Ledger):
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        price = ledger.client.post(
            f"/books/{ledger.book_id}/prices",
            json={
                "base_id": usd["id"],
                "quote_id": ledger.eur,
                "rate": "0.9",
                "effective_date": "2026-01-31",
            },
        ).json()
        assert (
            ledger.client.delete(
                f"/books/{ledger.book_id}/prices/{price['id']}"
            ).status_code
            == 204
        )
        assert ledger.client.get(f"/books/{ledger.book_id}/prices").json()["items"] == []

    def test_rename_a_book(self, ledger: Ledger):
        response = ledger.client.patch(
            f"/books/{ledger.book_id}",
            json={"name": "Household 2026", "base_currency_code": "EUR"},
        )
        assert response.json()["name"] == "Household 2026"

    def test_an_empty_grant_subject_is_refused(self, ledger: Ledger):
        response = ledger.client.put(
            f"/books/{ledger.book_id}/grants", json={"subject": "  ", "role": "viewer"}
        )
        assert response.status_code == 422

    def test_a_grant_cannot_outlive_its_book(self, user: Client):
        """The foreign key guarantees it, so the book list needs no guard."""
        from datetime import datetime, timezone

        from sqlalchemy.exc import IntegrityError

        from pymoney.store import identity

        user.make_book()
        engine = user.http.app.state.engine
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                identity.book_grants.insert().values(
                    book_id=new_id(),
                    subject=user.email,
                    role="owner",
                    created_at=datetime.now(timezone.utc),
                )
            )
        assert len(user.get("/books").json()) == 1

    def test_an_oversized_page_is_capped_not_refused(self, ledger: Ledger):
        response = ledger.client.get(
            f"/books/{ledger.book_id}/transactions", params={"limit": 100000}
        )
        assert response.status_code == 200

    def test_a_zero_page_is_refused(self, ledger: Ledger):
        response = ledger.client.get(
            f"/books/{ledger.book_id}/transactions", params={"limit": 0}
        )
        assert response.status_code == 422

    def test_a_cursor_that_is_not_an_object(self, ledger: Ledger):
        import base64

        cursor = base64.urlsafe_b64encode(b"[1,2]").decode().rstrip("=")
        response = ledger.client.get(
            f"/books/{ledger.book_id}/transactions", params={"cursor": cursor}
        )
        assert response.status_code == 400

    def test_a_register_cursor_missing_its_keys(self, ledger: Ledger):
        import base64

        cursor = base64.urlsafe_b64encode(b'{"d":"2026-01-01"}').decode().rstrip("=")
        response = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/register",
            params={"cursor": cursor},
        )
        assert response.status_code == 400

    def test_health_reports_degraded_when_the_database_is_gone(self, api: Client):
        api.http.app.state.engine.dispose()
        from sqlalchemy import create_engine as sa_create_engine

        api.http.app.state.engine = sa_create_engine(
            "sqlite+pysqlite:////nonexistent/path/db.sqlite"
        )
        assert api.http.get("/health").json()["status"] == "degraded"
