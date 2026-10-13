# PyMoney Architecture Codemap

**Last Updated:** 2026-10-13
**Entry Points:** `server/src/pymoney/api/app.py` (`create_app`), `desktop/src/pymoney_desktop/__main__.py`, `frontend/apps/web/src/main.tsx`

PyMoney is a double-entry accounting system in three independently deployable
components. The root `Makefile` only delegates; each component has its own
Makefile, package manifest, tests and coverage gate.

## System Overview

```
                        ┌─────────────────────────────────────────┐
                        │              Clients                    │
                        │                                         │
                        │  @pymoney/web (React, browser)          │
                        │  pymoney-desktop (PySide6, online or    │
                        │                   offline w/ replica)   │
                        └───────────────┬─────────────────────────┘
                                        │ HTTP / JSON
                                        │ (amounts are strings, never floats)
                        ┌───────────────▼─────────────────────────┐
                        │   pymoney.api  —  FastAPI               │
                        │   routers → views → commands/queries    │
                        │   auth, rate limits, mail, attachments  │
                        └───────────────┬─────────────────────────┘
                                        │ Book.execute(command)
                        ┌───────────────▼─────────────────────────┐
                        │   pymoney.core  —  pure stdlib domain   │
                        │   Book · commands · handlers · reports  │
                        │   rounding · prices · changelog · sync  │
                        └───────────────┬─────────────────────────┘
                                        │ Store contract
                        ┌───────────────▼─────────────────────────┐
                        │   pymoney.store  —  SQLAlchemy Core     │
                        │   SQLite / PostgreSQL + Alembic         │
                        │   + identity, grants, attachments meta  │
                        └─────────────────────────────────────────┘

   Side modules (imported by the API, never by the core):
     pymoney.quotes    — ECB / Yahoo price providers + scheduler
     pymoney.importers — KMyMoney migration + recurring CSV statements
     pymoney.i18n      — shared translation catalogue (en source, nl.json)
```

The dependency rule is one-way and enforced by design: `core` has **no runtime
dependencies** (stdlib only), `store` implements the core's `Store` contract,
and `api`/`quotes`/`importers` sit on top. Nothing in `core` imports them.

## Component Relationships

| Component | Package | Depends on | Deployable as |
|-----------|---------|------------|---------------|
| `server/` | `pymoney-core` | stdlib; extras: SQLAlchemy, FastAPI | uvicorn app / Docker image |
| `desktop/` | `pymoney-desktop` | `pymoney-core[api]`, PySide6 | PyInstaller binary (`build/pyinstaller`) |
| `frontend/` | `@pymoney/core` + `@pymoney/web` | React 19, react-router, TanStack Query | static Vite build / Docker image |

Shared artifacts (generated — never hand-edit):

- `server/docs/openapi.json` — the wire contract, from `make -C server openapi`
- `frontend/packages/core/src/generated/openapi.d.ts` — from `npm run generate`
- `frontend/packages/core/src/generated/nl.json` — copied by `scripts/i18n.py`

## Data Flow

### Write path (a posted transaction)

```
Client form (string amounts)
  → POST /books/{id}/transactions        api/routers/transactions.py
  → actor + role check                   api/deps.py (require_role)
  → wire schema validation               api/schemas.py (rejects JSON floats)
  → registry.open(book_id)               api/registry.py — one Book + one lock
                                        per book (design decision #14)
  → book.execute(PostTransaction(...))   core/book.py
  → handler validates & books legs       core/handlers/transactions.py
  → book.put/drop in one unit of work    core/book.py (bulk/execute_all)
  → store writes rows + change log       store/sql.py (SqlStore)
  → revision bumped, CommandResult back  core/changelog.py, commands.py
  → response stamped X-PyMoney-Revision  api/routers/_shared.py
```

Commands are idempotent: a fingerprint + `command_results` row lets a retried
command replay its recorded result instead of applying twice.

### Read path (a report)

```
GET /books/{id}/reports/balance-sheet
  → router: authorise, parse query      api/routers/reports.py
  → view assembles read model           api/views.py (shared with desktop offline)
  → book.balances_* + ValuationPolicy   core/reports.py, core/price.py
  → exact rational maths → minor units  core/rounding.py (banker's rounding)
  → wire schema (amounts as strings)    api/schemas.py
```

Reports take an explicit `ValuationPolicy` (decision #12), so the same call on
the same book always yields the same numbers — on the web, on the desktop, and
on a desktop replica running the same view code.

### Sync path (desktop offline replica)

```
GET /books/{id}/changes?since=N         api/routers/sync.py
  → RevisionBatch of atomic Change rows core/sync.py
  → replica applies batch idempotently  desktop/src/pymoney_desktop/replica.py
Writes meanwhile: validated locally against the same Book, queued in
desktop_outbox with idempotency keys   desktop/src/pymoney_desktop/offline.py
  → replay in order on reconnect; server answer is final (rejection, not merge)
```

### Quote path

```
QuoteScheduler (interval) → QuoteService.run(book)
  → ProviderRegistry.discover()         entry points / PYMONEY_QUOTE_PLUGINS
  → EcbProvider (currencies) | YahooProvider (securities)
  → Price rows (source=provider, exact num/den rate)  core/price.py
```

## Concurrency & Consistency

- **One writer per book**: `BookRegistry` holds one `Book` per book id with a
  per-book `threading.Lock`; requests for the same book serialise. Run the API
  single-worker.
- **The store is the only mutator** (decision #14); `Book._put`/`_drop` assert
  a unit of work is open.
- **Every mutation is audited** into `changes` with a per-book `revision`
  (decision #11); reconciled (`FROZEN`) data is immutable.
- **SQLite writes take `BEGIN IMMEDIATE`** (`store/connections.py`) so
  concurrent writers queue instead of failing `SQLITE_BUSY_SNAPSHOT`.

## Persistence Shape

Ledger tables live in `store/schema.py`; identity (`users`, `sessions`,
`book_grants`, `user_tokens`) in `store/identity.py`, same database and same
Alembic chain. Attachment *bytes* live on disk under
`<attachments_dir>/<book_id>/<attachment_id>` (`api/blobs.py`) — a backup must
capture the database and that directory together. Migrations are a deployment
step: `python -m pymoney.store.migrate`.

## External Dependencies

| Dependency | Where | Purpose |
|------------|-------|---------|
| SQLAlchemy ≥2 / Alembic | `store` extra | SQL Core persistence + migrations |
| FastAPI / Pydantic / uvicorn | `api` extra | HTTP API |
| defusedxml | `store` extra | safe KMyMoney XML parsing |
| psycopg / psycopg2 | `postgres` extra | PostgreSQL drivers |
| PySide6 | `desktop` | desktop UI |
| React 19, react-router 7, TanStack Query 5 | `frontend` | web UI |
| Playwright | `frontend/e2e` | end-to-end tests against the real API |

## Related Areas

- [MODULES.md](MODULES.md) — per-module public APIs and dependencies
- [FILES.md](FILES.md) — directory-by-directory file purposes
- [INDEX.md](INDEX.md) — navigation overview
- Root `README.md` — the 14 design decisions and where they live
