"""Configuration, from the environment.

All settings are ``PYMONEY_``-prefixed so one environment can hold several
services. The defaults are safe for local development and deliberately *unsafe
to deploy*: :meth:`Settings.production_warnings` lists what must change, and the
app logs it at startup rather than letting someone discover it later.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PYMONEY_", env_file=".env", extra="ignore"
    )

    database_url: str = "sqlite+pysqlite:///pymoney.sqlite"
    """Where the ledger lives. Same variable Alembic uses."""

    create_schema: bool = False
    """Create tables at startup. Convenient for tests; use Alembic in anger."""

    session_lifetime_hours: int = Field(default=24 * 14, ge=1)
    session_idle_hours: int = Field(default=24 * 7, ge=1)
    """A session also expires after this long without being used."""

    cookie_name: str = "pymoney_session"
    cookie_secure: bool = True
    """Off only for plain-HTTP local development."""
    cookie_samesite: str = "lax"
    cookie_domain: str | None = None

    allow_registration: bool = True
    """Whether POST /auth/register works. Off once the household is set up."""

    require_activation: bool = True
    """Registering sends an activation link and the account is unusable until it
    is followed. Off: the address is taken on trust and the session starts at once."""
    activation_hours: int = Field(default=48, ge=1)
    """How long an activation link is valid."""
    purge_unactivated_after_hours: int = Field(default=7 * 24, ge=1)
    """A never-activated account older than this is deleted by the maintenance job."""
    reset_hours: int = Field(default=2, ge=1)
    """How long a password-reset link is valid."""
    maintenance_interval_minutes: int = Field(default=60, ge=0)
    """How often expired sessions and tokens and stale pending accounts are purged. 0 turns it off."""

    public_url: str = "http://localhost:5173"
    """Where the web app is reached: the links in emails point here."""
    mail_backend: str = "console"
    """console, smtp, file or memory (see pymoney.api.mail)."""
    mail_from: str = "pymoney@localhost"
    mail_file: str = "pymoney-mail.jsonl"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True

    cors_origins: list[str] = Field(default_factory=list)
    """Exact origins allowed to send credentialed requests. Never "*"."""

    default_page_size: int = Field(default=100, ge=1, le=1000)
    max_page_size: int = Field(default=500, ge=1, le=5000)

    root_path: str = ""
    """Set when mounted behind a prefix-stripping proxy."""

    max_upload_mb: int = Field(default=50, ge=1, le=2048)
    """Largest file `POST /books/import/kmymoney` accepts."""

    quote_interval_minutes: int = Field(default=360, ge=0)
    """How often the scheduler fetches quotes for every book. 0 turns it off;
    `POST /books/{id}/quotes/fetch` and `python -m pymoney.quotes` still work."""
    quote_lookback_days: int = Field(default=7, ge=0, le=3650)
    """How far back a commodity's first fetch reaches. Backfill further by hand."""
    quote_plugins: list[str] = Field(default_factory=list)
    """Extra quote providers as `module:Class` references (see pymoney.quotes.registry)."""

    def production_warnings(self) -> list[str]:
        """Settings that are fine locally and wrong in production."""
        problems = []
        if not self.cookie_secure:
            problems.append(
                "PYMONEY_COOKIE_SECURE is off: session cookies will be sent over "
                "plain HTTP"
            )
        if self.database_url.startswith("sqlite") and ":memory:" in self.database_url:
            problems.append("the database is in memory; nothing will be kept")
        if "*" in self.cors_origins:
            problems.append(
                "CORS origin '*' cannot be combined with cookie auth and will be "
                "rejected by browsers; list exact origins"
            )
        if self.allow_registration:
            problems.append(
                "PYMONEY_ALLOW_REGISTRATION is on: anyone who can reach this "
                "server can create an account"
            )
        if self.allow_registration and not self.require_activation:
            problems.append(
                "PYMONEY_REQUIRE_ACTIVATION is off: email addresses are taken on trust"
            )
        if self.mail_backend == "console":
            problems.append(
                "PYMONEY_MAIL_BACKEND is console: activation and reset links go to the "
                "server log, not to the user"
            )
        return problems


def get_settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]
