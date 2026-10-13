# PyMoney Module Codemap

**Last Updated:** 2026-10-13
**Entry Points:** `pymoney.api:create_app` (server), `pymoney_desktop.__main__:main` (desktop), `frontend/apps/web/src/main.tsx` (web)

Public APIs below are the ones other modules and clients are meant to import.
Everything is generated from the source; see [FILES.md](FILES.md) for the full
tree and [ARCHITECTURE.md](ARCHITECTURE.md) for the data flow.

---

## server/ — package `pymoney-core` (`server/src/pymoney`)

Python 3.12+. Runtime dependencies: **none** for `core`; extras `store`, `api`,
`postgres` add SQLAlchemy, FastAPI and drivers.

### pymoney.core — pure domain (stdlib only)

**Purpose**: The double-entry ledger: entities, commands, invariants, reports.
Knows nothing about HTTP, SQL or Qt.

**Location**: `server/src/pymoney/core/`

**Key Files**:
- `book.py` - `Book`, the consistency boundary: queries, `execute`, `bulk`
- `commands.py` - one dataclass per command + `CommandResult`, idempotency fingerprint
- `handlers/` - one module per aggregate; `(book, command) -> entity`
- `transaction.py` / `posting.py` - `Transaction`, `Split`, `book_legs` (≥2 legs, values sum to zero)
- `amount.py` / `rounding.py` - integer minor units, `Fraction` rates, banker's rounding
- `price.py` - bitemporal `Price`, `PriceDB`, `ValuationPolicy`, `Conversion`
- `reports.py` - `balance_sheet`, `income_statement`, `period_table`, `net_worth_series`, `holdings`
- `changelog.py` - `Change`, `ChangeLog`, `EntityKind`; the audit trail
- `sync.py` - `RevisionBatch` / `SyncPage`: the pull side of the replica protocol
- `store.py` - the `Store` contract + the in-memory implementation
- `recurrence.py` / `schedule.py` - `Frequency`, `Recurrence`, `Schedule`, templates
- `ids.py` / `clock.py` - UUIDv7 ids, injectable `Clock`

**Dependencies**: stdlib only.

**Exports** (grouped in `core/__init__.py`, ~90 names): `Book`, `Commodity`,
`Amount`, `RationalAmount`, `Balances`, `Account`, `AccountType`, `Payee`,
`Tag`, `Transaction`, `Split`, `ReconcileState`, `Price`, `PriceDB`,
`ValuationPolicy`, `Schedule`, all `Command` classes, report functions, and
the `PyMoneyError` hierarchy (`ValidationError`, `ConflictError`,
`ImmutableError`, `UnbalancedTransactionError`, …).

**Usage Example**:
```python
from pymoney.core import AccountType, Book, Commodity, PostTransaction

book = Book(name="Household", base_currency=Commodity.currency("EUR"), store=store)
cash = book.add_account("Cash", AccountType.CASH)
book.execute(PostTransaction(...))
```

### pymoney.core.handlers — command handlers

**Purpose**: Per-aggregate rules, one file per aggregate. A handler validates,
then mutates through `book.put` / `book.drop`; `Book` wraps the call in one
unit of work and one revision.

**Location**: `server/src/pymoney/core/handlers/`

**Key Files**: `accounts.py`, `commodities.py`, `payees.py`, `tags.py`,
`transactions.py`, `prices.py`, `schedules.py`, `settings.py`.

**Dependencies**: `pymoney.core` (entity modules, `errors`).

### pymoney.store — persistence (SQLAlchemy Core)

**Purpose**: `SqlStore` implements `pymoney.core.store.Store` over SQLite and
PostgreSQL, so the same `Book` and the same test suite run on either.

**Location**: `server/src/pymoney/store/`

**Key Files**:
- `engine.py` - `create_engine`, `create_schema`, `open_book`, `session`, `write_transaction`
- `sql.py` - `SqlStore`; small tables cached in memory, large ones queried
- `schema.py` - SQLAlchemy `Table` definitions (no ORM mapping of domain classes)
- `mapper.py` - `Registry`/`Rows`, dumb `to_row`/`from_row` translation
- `connections.py` - writer transactions (`BEGIN IMMEDIATE`) for SQLite WAL
- `migrate.py` / `migrations/` - Alembic chain; `python -m pymoney.store.migrate`
- `identity.py` - `users`, `user_tokens`, `sessions`, `book_grants`
- `attachments.py`, `grants.py`, `import_mappings.py`, `admin.py` - non-domain tables

**Dependencies**: `pymoney.core` (contract), SQLAlchemy, Alembic, defusedxml.

**Exports**: `create_engine`, `create_schema`, `drop_schema`, `open_book`,
`SqlStore`, `Registry`, `Rows`, `metadata`, `ALL_TABLES`, `session`,
`write_connection`, `write_transaction`.

**Usage Example**:
```python
from pymoney.store import create_engine, create_schema, open_book

engine = create_engine("sqlite+pysqlite:///ledger.sqlite")
create_schema(engine)   # or: python -m pymoney.store.migrate
book = open_book(engine, name="Household", base_currency=...)
```

**Schema tables**: `books`, `commodities`, `accounts`, `payees`, `tags`,
`schedules`, `transactions`, `splits`, `split_tags`, `prices`, `changes`,
`command_results`, `attachments`, `import_mappings` (+ 4 identity tables).

### pymoney.quotes — price fetching

**Purpose**: Pluggable quote providers, a fetching service and a scheduler.
Fetched prices are ordinary `Price` rows beside hand-entered ones.

**Location**: `server/src/pymoney/quotes/`

**Key Files**:
- `provider.py` - `QuoteProvider`, `QuoteRequest`, `Quote`, `Http`, `QuoteError`
- `ecb.py` / `yahoo.py` - the two built-in providers (currencies / securities)
- `registry.py` - `ProviderRegistry.discover()`, entry-point group, `PYMONEY_QUOTE_PLUGINS`
- `service.py` - `QuoteService.run(book)`, `FetchReport`, `PlannedFetch`
- `scheduler.py` - `QuoteScheduler`, interval-driven (API startup)

**Dependencies**: `pymoney.core` (Price), stdlib `urllib`.

**Usage Example**:
```python
from pymoney.quotes import ProviderRegistry, QuoteService
report = QuoteService(ProviderRegistry.discover()).run(book)
print(report.summary())
```

### pymoney.importers — getting data in

**Purpose**: Two importers held to a high standard: nothing dropped silently,
every approximation reported, results verified against the source.

**Location**: `server/src/pymoney/importers/`

**Key Files**:
- `kmymoney/` - one-time `.kmy` migration: `parser.py` (defusedxml), `model.py`, `importer.py` (rational → minor-unit arithmetic), `report.py`
- `statement/` - recurring bank CSV: `source.py` (sniff encoding/delimiter/mapping), `mapping.py`, `entry.py`, `matching.py` (payee/category guesses), `proposals.py`, `commit.py` (`import_statement`, one `bulk` unit of work), `report.py`

**Dependencies**: `pymoney.core`, `pymoney.store` (importers write books), defusedxml.

**Usage Example**:
```python
from pymoney.importers.kmymoney import import_kmymoney
report = import_kmymoney("~/money.kmy", book)
print(report.summary())
```

### pymoney.api — the HTTP API

**Purpose**: FastAPI app; every endpoint is a thin shell around a command or a
query on `Book`. Wire schemas are deliberately separate from domain objects.

**Location**: `server/src/pymoney/api/`

**Key Files**:
- `app.py` - `create_app()`; lifespan wires engine, registry, quotes, mail, maintenance
- `registry.py` - `BookRegistry`: one `Book` + lock per book id (decision #14)
- `deps.py` - `Actor`, `current_actor`, `require_role`, `acting_as`, `idempotency_key`
- `schemas.py` - Pydantic wire schemas; amounts/rates as strings or ints, floats rejected
- `views.py` - read models shared with the desktop's offline mode
- `security.py` - scrypt password hashing, session tokens, constant-time compares
- `settings.py` - `Settings` (pydantic-settings; `PYMONEY_*` env vars)
- `ratelimit.py`, `mail.py`, `blobs.py`, `uploads.py`, `errors.py`, `maintenance.py`
- `routers/` - 16 routers (see table below)

**Dependencies**: `pymoney.core`, `pymoney.store`, `pymoney.quotes`, FastAPI, Pydantic.

**Exports**: `create_app`, `BookRegistry`, `Settings` (plus lazy `app` for uvicorn).

**Usage Example**:
```python
from pymoney.api import create_app
app = create_app()
# uvicorn pymoney.api:app --host 0.0.0.0 --port 8000
```

**Routers** (prefixes under `/books/{book_id}` unless noted):

| Router | Prefix | Purpose |
|--------|--------|---------|
| `system` | — | health/version |
| `auth` | `/auth` | register, login, activate, reset, logout, sessions |
| `books` | — | book CRUD, grants/sharing |
| `imports` | — | KMyMoney `.kmy` upload → migration |
| `commodities`, `accounts`, `payees`, `tags` | `…/commodities` etc. | entity CRUD |
| `transactions` | — | post/update/delete, register, search, reconcile, attachments |
| `attachments` | — | receipt blobs |
| `prices` | — | price CRUD |
| `reports` | `…/reports` | balance sheet, income statement, holdings, net worth, periods |
| `schedules` | `…/schedules` | recurring transactions, due panel, post/skip |
| `csv_import` | — | statement CSV: inspect, preview, commit |
| `sync` | — | `GET /changes` for replicas |
| `quotes` | — | trigger/observe quote fetches |

### pymoney.i18n — translations

**Purpose**: One catalogue shared by server emails, web and desktop. Keys are
the English source strings; `server/src/pymoney/i18n/nl.json` is the single
Dutch catalogue (`scripts/i18n.py` copies/verifies it).

**Location**: `server/src/pymoney/i18n/`

**Exports**: `translate(key, locale, **params)`, `catalogue`, `normalise_locale`,
`SUPPORTED = ("en", "nl")`, `DEFAULT_LOCALE = "en"`.

---

## desktop/ — package `pymoney-desktop` (`desktop/src/pymoney_desktop`)

**Purpose**: PySide6 client, online or offline. Speaks the API's wire schemas;
offline it runs the API's own read models over a local SQLite replica.

**Location**: `desktop/src/pymoney_desktop/`

**Key Files**:
- `client.py` - `LedgerClient` interface + shared errors (the UI can't tell remote from offline except via `status()`)
- `remote.py` - `RemoteClient`: bearer token, read cache kept honest by `X-PyMoney-Revision`
- `offline.py` - `OfflineClient`: local replica + `desktop_outbox` with idempotency keys
- `replica.py` - applies sync batches idempotently, dependency order, server audit stamps
- `transport.py` - one HTTP call for both clients (urllib; tests wrap FastAPI `TestClient`)
- `statement_import.py` - the five CSV-import calls, always server-side
- `drafts.py` - transaction form logic without Qt (twin of the web's `ledger.ts`)
- `i18n.py` - `_()` bound to one locale, backed by `pymoney.i18n`
- `ui/` - `app.py`, `main_window.py`, dialogs (`transaction_dialog`, `import_dialog`, `accounts_dialog`, `payees_dialog`, `schedules`, `reconcile`, `search_dialog`, `attachments_dialog`, `stock_split_dialog`, `login_dialog`), `reports.py`, `charts.py`, `theme.py`

**Dependencies**: `pymoney-core[api]` (so: core + store + FastAPI schemas), PySide6.

**Exports**: `LedgerClient`, `RemoteClient`, `OfflineClient`, `set_locale`/`_`,
`main` (`pymoney-desktop` console script).

**Usage Example**:
```python
from pymoney_desktop.remote import RemoteClient
client = RemoteClient(transport, token)
client.post_transaction(book_id, payload)
```

Offline tables in the replica file: `desktop_kv`, `desktop_sync`,
`desktop_outbox` (beside the ledger schema).

---

## frontend/ — npm workspace

### @pymoney/core (`frontend/packages/core`)

**Purpose**: Platform-agnostic client library. No DOM (tsconfig `lib: ES2022`
only) — a mobile app can reuse it. Exports `.` and `./hooks`.

**Location**: `frontend/packages/core/src/`

**Key Files**:
- `types.ts` - re-exports from `generated/openapi.d.ts` (the wire contract)
- `client.ts` - `ApiClient`, `AuthStore`, `MemoryAuthStore`, `ApiError`, `isPending`
- `money.ts` - `formatAmount` etc.; BigInt string maths, locale separators, never floats
- `ledger.ts` - `LegDraft`/entry arithmetic for the transaction form (typed strings → minor units)
- `periods.ts` - calendar-local date ranges for reports (`Period`, `isoDate`, `today`)
- `reports.ts` - section grouping, CSV of report payloads
- `i18n.ts` + `hooks/i18n.tsx` - `t()` translation hooks over the shared catalogue
- `hooks/` - `ApiProvider`, `createQueryClient`, `keys`, ~40 `use*` query/mutation hooks
- `generated/` - `openapi.d.ts`, `nl.json` (**generated** — `npm run generate`)

**Dependencies**: `@tanstack/react-query` and `react` are peer deps (hooks only);
the non-hook modules are dependency-free.

**Usage Example**:
```typescript
import { ApiClient, formatAmount } from "@pymoney/core";
import { useAccounts, ApiProvider } from "@pymoney/core/hooks";
```

### @pymoney/web (`frontend/apps/web`)

**Purpose**: The React web app: routes, pages, report views.

**Location**: `frontend/apps/web/src/`

**Key Files**:
- `main.tsx` - `ApiProvider` + `BrowserRouter` + `AppI18n`; a 401 anywhere clears the session
- `App.tsx` - all routes (`RequireAuth` guards `/books`, `/books/:bookId/…`)
- `api.ts` - `createWebClient()`: cookie session + CSRF token
- `ledger/` - `TransactionForm`, `TransactionPage`, `AccountRegisterPage`, `SearchPage`, `ImportStatementPage`, `ReconcileMark`
- `pages/` - `BooksPage`, `BookShell`, `LoginPage`, `PasswordPages`, `AccountsPage`, `PayeesPage`, `SchedulesPage`, `CommoditiesPage`, `PricesPage`, `HistoryPage`, `SharingPage`, `SettingsShell`, …
- `reports/` - `BalanceSheetReport`, `IncomeStatementReport`, `HoldingsReport`, `CategoryPieReport`, `NetWorthReport`, `PeriodTableReport`, `ChartsShell`, `useReportParams`
- `components/` - `Money`, `ReportTable`, `HistoryTable`, `DuePanel`, `ValuationPanel`, charts, form fields, `RequireAuth`
- `i18n.tsx` - web-side locale switch

**Dependencies**: `@pymoney/core`, React 19, react-router-dom 7, TanStack Query 5.

**Route map** (from `App.tsx`): `/login`, `/forgot-password`, `/reset-password`,
`/activate`, `/books`, `/books/:bookId/{reports/{balance-sheet,income-statement,
holdings,charts/…},settings/{accounts,payees,schedules,commodities,prices,history},
sharing,delete,search,accounts/:accountId[+/import],transactions/:transactionId}`.

### frontend/e2e — Playwright

**Purpose**: E2E against the real API + Vite dev server (needs `make -C
../server install` first). Specs: `onboarding`, `ledger`, `import`, `reconcile`,
`search`, `payees`, `prices`, `charts`, `identity`, `language`, `tabs`.

---

## scripts/ (repo root)

- `i18n.py` - copy `nl.json` into the frontend; `--check` fails on missing/stale keys (CI)
- `i18n_wrap_web.py` / `i18n_wrap_desktop.py` - wrap new user-visible strings for translation

## Related Areas

- [ARCHITECTURE.md](ARCHITECTURE.md) — system diagram and data flow
- [FILES.md](FILES.md) — directory tree and file purposes
- [INDEX.md](INDEX.md) — navigation overview
