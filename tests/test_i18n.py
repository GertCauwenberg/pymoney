"""Translations: the catalogue, the lookup, the user's stored language."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pymoney.i18n import SUPPORTED, catalogue, normalise_locale, translate
from tests.conftest_api import PASSWORD, Client

ROOT = Path(__file__).resolve().parent.parent


class TestCatalogue:
    def test_lookup_fallback_and_parameters(self):
        assert translate("Balance sheet", "nl") == "Balans"
        assert translate("Balance sheet", "en") == "Balance sheet"
        assert translate("Not in the catalogue", "nl") == "Not in the catalogue"
        assert translate("Delete {name}?", "nl", name="Visa") == "Visa verwijderen?"
        assert translate("Delete {name}?", "en", name="Visa") == "Delete Visa?"

    def test_locale_normalisation(self):
        assert normalise_locale("nl-BE") == "nl"
        assert normalise_locale("NL") == "nl"
        assert normalise_locale("de-DE") == "en"
        assert normalise_locale(None) == "en"
        assert catalogue("de") == {} and catalogue("en") == {}
        assert SUPPORTED == ("en", "nl")

    def test_every_string_in_the_code_has_a_dutch_translation(self):
        """scripts/i18n.py --check is what CI runs; the catalogue must be complete
        and free of stale keys, and the frontend's copy identical."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "i18n.py"), "--check"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout
        server = json.loads((ROOT / "src" / "pymoney" / "i18n" / "nl.json").read_text())
        frontend = json.loads(
            (
                ROOT / "frontend" / "packages" / "core" / "src" / "generated" / "nl.json"
            ).read_text()
        )
        assert server == frontend
        assert all(v for v in server.values())
        # Placeholders survive translation.
        for key, value in server.items():
            assert {p for p in __import__("re").findall(r"\{(\w+)\}", key)} == {
                p for p in __import__("re").findall(r"\{(\w+)\}", value)
            }, key


class TestUserLocale:
    def test_stored_with_the_account_and_returned_everywhere(self, user: Client):
        assert user.get("/auth/me").json()["locale"] == "en"
        response = user.patch("/auth/me", json={"locale": "nl"})
        assert response.status_code == 200 and response.json()["locale"] == "nl"
        assert user.get("/auth/me").json()["locale"] == "nl"
        # Login hands it back, so a client can switch before drawing anything.
        assert user.login(user.email)["user"]["locale"] == "nl"
        assert user.patch("/auth/me", json={"locale": "de"}).status_code == 422
        assert (
            user.patch("/auth/me", json={"display_name": "Ann"}).json()["display_name"]
            == "Ann"
        )

    def test_registration_takes_a_language_and_the_emails_follow_it(self, tmp_path):
        from fastapi.testclient import TestClient

        from pymoney.api import create_app
        from pymoney.api.mail import MemoryMailer
        from pymoney.api.settings import Settings

        settings = Settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / 'nl.sqlite'}",
            create_schema=True,
            cookie_secure=False,
            require_activation=True,
            quote_interval_minutes=0,
            maintenance_interval_minutes=0,
            mail_backend="memory",
        )
        mailer = MemoryMailer()
        with TestClient(create_app(settings=settings, mailer=mailer)) as http:
            http.post(
                "/auth/register",
                json={
                    "email": "nl@example.invalid",
                    "password": PASSWORD,
                    "locale": "nl",
                },
            )
            assert mailer.sent[-1].subject == "Activeer uw PyMoney-account"
            assert "Open deze link binnen 48 uur" in mailer.sent[-1].body
            http.post("/auth/resend-activation", json={"email": "nl@example.invalid"})
            assert mailer.sent[-1].subject == "Activeer uw PyMoney-account"
            http.post(
                "/auth/register",
                json={"email": "en@example.invalid", "password": PASSWORD},
            )
            assert mailer.sent[-1].subject == "Activate your PyMoney account"
