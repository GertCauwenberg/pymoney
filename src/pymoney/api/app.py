"""The application factory.

``create_app`` takes its settings and engine as arguments rather than reading the
environment itself, so a test builds the same app the server runs -- no import
side effects, no module-level globals, no special test mode that might behave
differently from production.

The OpenAPI document is part of the contract we ship, so it is populated
deliberately: descriptions on every tag, documented error responses, and a
version that matches the package. ``docs/openapi.json`` is committed and a test
fails if the code drifts from it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from .. import __version__
from ..core.clock import Clock
from ..store import create_engine, create_schema
from .errors import install_error_handlers
from .registry import BookRegistry
from .routers import (
    accounts,
    auth,
    books,
    commodities,
    payees,
    prices,
    reports,
    sync,
    system,
    transactions,
)
from .settings import Settings

__all__ = ["create_app"]

DESCRIPTION = """
Double-entry accounting over HTTP.

**Amounts and rates are never JSON floats.** Send them as strings (`"12.34"`,
`"10/3"`) or integers; a JSON float is rejected, because `0.1` is not one tenth
and a ledger cannot afford the difference. Amounts come back as exact minor units
plus a formatted string; rates as an exact `num`/`den` pair plus a decimal
approximation marked as such.

**Writes are commands, not CRUD.** `POST /transactions` takes legs as exact
rationals and the server decides how they book -- which leg absorbs a rounding
remainder is not something a client can work out without reimplementing the
allocator. Structural operations (rename, move, merge, close, reconcile) are each
their own endpoint because each has its own rules.

**Every write accepts `Idempotency-Key`.** Replaying a key returns the original
result instead of applying the command twice, and the response carries
`Idempotency-Replayed: true`. This is what makes a retry over a flaky mobile
connection safe.

**Every response carries `X-PyMoney-Revision`.** One command is one revision,
however many rows it touched. `GET /books/{id}/changes?since=<revision>` returns
everything above a watermark, grouped into atomically applicable batches.
"""

TAGS = [
    {"name": "system", "description": "Health and version. No authentication."},
    {
        "name": "auth",
        "description": (
            "Session cookies, plus a bearer token for clients that cannot hold "
            "cookies. Cookie-authenticated writes also require `X-CSRF-Token`."
        ),
    },
    {
        "name": "books",
        "description": "Ledgers, and who may read or write each one.",
    },
    {"name": "commodities", "description": "Currencies and securities, uniformly."},
    {
        "name": "accounts",
        "description": (
            "The account tree, and balances. Reading needs `viewer`; changing the "
            "chart of accounts needs `owner`, because it reshapes every report."
        ),
    },
    {"name": "payees", "description": "Payees, and merging duplicates."},
    {
        "name": "transactions",
        "description": "Posting, editing, reconciling, and account registers.",
    },
    {
        "name": "prices",
        "description": (
            "Bitemporal price history. A correction is a new row with a later "
            "`as_of`, never an edit."
        ),
    },
    {
        "name": "reports",
        "description": (
            "Balance sheet, income statement and holdings. Every report takes an "
            "explicit valuation policy, so the same request always gives the same "
            "numbers."
        ),
    },
    {
        "name": "sync",
        "description": "The pull half of the replica protocol.",
    },
]

# Documented on every route, so a client can generate error handling.
COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not authenticated."},
    403: {
        "description": (
            "Authenticated, but not allowed to do this: writes need `editor`, "
            "changes to the account tree and to sharing need `owner`."
        )
    },
    404: {"description": "No such book, or no grant on it."},
    409: {
        "description": (
            "Refused because of the ledger's state: a duplicate name, or a "
            "reconciled transaction that may not be edited."
        )
    },
    422: {
        "description": (
            "The request was understood and breaks a rule: an unbalanced "
            "transaction, a float where an exact value was required, a missing "
            "price."
        )
    },
}


def _is_in_memory(url: str) -> bool:
    return url.startswith("sqlite") and (":memory:" in url or "mode=memory" in url)


def create_app(
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build the application.

    :param engine: an open engine to use. Created from ``settings`` if omitted,
        and disposed on shutdown only in that case -- an engine we were handed is
        the caller's to close.
    """
    settings = settings or Settings()
    if engine is None and _is_in_memory(settings.database_url):
        # An in-memory SQLite database is a *single connection*: SQLAlchemy has
        # to pin one with StaticPool, or each connection would get its own empty
        # database. The API needs at least two at once -- the session lookup and
        # the book's store -- so this would fail at runtime with a baffling
        # "cannot start a transaction within a transaction". Better to say so now.
        raise ValueError(
            f"{settings.database_url!r} cannot back the API: an in-memory SQLite "
            f"database supports only one connection. Use a file "
            f"(sqlite+pysqlite:///pymoney.sqlite) or PostgreSQL."
        )
    owns_engine = engine is None
    engine = engine or create_engine(settings.database_url)
    if settings.create_schema:
        create_schema(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = engine
        app.state.registry = BookRegistry(engine, clock=clock)
        for warning in settings.production_warnings():
            # Logged, not silent: someone should see this before a user does.
            print(f"pymoney: WARNING: {warning}")
        try:
            yield
        finally:
            app.state.registry.close()
            if owns_engine:
                engine.dispose()

    app = FastAPI(
        title="PyMoney",
        version=__version__,
        summary="Double-entry accounting that runs everywhere.",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        root_path=settings.root_path,
        responses=COMMON_RESPONSES,
    )
    # Also set outside the lifespan, so a TestClient used without a context
    # manager still finds them.
    app.state.settings = settings
    app.state.engine = engine
    app.state.registry = BookRegistry(engine, clock=clock)

    install_error_handlers(app)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            # Credentials and "*" are mutually exclusive in every browser, which
            # is why the setting takes exact origins.
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-PyMoney-Revision", "Idempotency-Replayed"],
        )

    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(books.router)
    app.include_router(commodities.router)
    app.include_router(accounts.router)
    app.include_router(payees.router)
    app.include_router(transactions.router)
    app.include_router(prices.router)
    app.include_router(reports.router)
    app.include_router(sync.router)
    return app
