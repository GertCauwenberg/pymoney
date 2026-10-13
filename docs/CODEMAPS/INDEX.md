# PyMoney Codemaps — Index

**Last Updated:** 2026-10-13
**Source of truth:** the code in this repository. When the structure changes,
regenerate these maps from the code — documentation that disagrees with the
code is worse than none.

## The Maps

| Map | Read it for | File |
|-----|-------------|------|
| **Architecture** | System overview, component relationships, data flow, concurrency, persistence shape | [ARCHITECTURE.md](ARCHITECTURE.md) |
| **Modules** | Per-module purpose, key files, public APIs, dependencies, usage examples | [MODULES.md](MODULES.md) |
| **Files** | Directory tree, file purposes, generated-file list, "I want to…" cheat sheet | [FILES.md](FILES.md) |

## The System in One Screen

Three independently deployable components; the root `Makefile` delegates:

- **`server/`** — Python package `pymoney-core` (`server/src/pymoney`):
  `core` (pure stdlib double-entry domain), `store` (SQLAlchemy over SQLite /
  PostgreSQL + Alembic), `quotes` (ECB / Yahoo), `importers` (KMyMoney
  migration + recurring CSV statements), `api` (FastAPI), `i18n`. Ships
  `server/docs/openapi.json` as the wire contract.
- **`desktop/`** — Python package `pymoney-desktop`, PySide6. Online
  (`RemoteClient`) or offline with a local SQLite replica + outbox
  (`OfflineClient`). Shares one `.venv` with the server.
- **`frontend/`** — npm workspace: `@pymoney/core` (no DOM: types, API client,
  money maths, React Query hooks) + `@pymoney/web` (React 19 SPA), plus
  Playwright e2e against the real API.

```
  web (React) ─┐                      ┌─ pymoney.core (stdlib only)
  desktop ─────┴─ HTTP ─ pymoney.api ─┤─ pymoney.store (SQL)
                                       ├─ pymoney.quotes
                                       └─ pymoney.importers
```

## Areas at a Glance

| Area | Where | Codemap section |
|------|-------|-----------------|
| Domain model & commands | `server/src/pymoney/core/` | [MODULES.md § core](MODULES.md#pymoneycore--pure-domain-stdlib-only) |
| Persistence & migrations | `server/src/pymoney/store/` | [MODULES.md § store](MODULES.md#pymoneystore--persistence-sqlalchemy-core) |
| HTTP API & wire schemas | `server/src/pymoney/api/` | [MODULES.md § api](MODULES.md#pymoneyapi--the-http-api) |
| Price quotes | `server/src/pymoney/quotes/` | [MODULES.md § quotes](MODULES.md#pymoneyquotes--price-fetching) |
| Importers | `server/src/pymoney/importers/` | [MODULES.md § importers](MODULES.md#pymoneyimporters--getting-data-in) |
| Translations | `server/src/pymoney/i18n/`, `scripts/i18n.py` | [MODULES.md § i18n](MODULES.md#pymoneyi18n--translations) |
| Desktop client & UI | `desktop/src/pymoney_desktop/` | [MODULES.md § desktop](MODULES.md#desktop--package-pymoney-desktop-desktopsrcpymoney_desktop) |
| Shared web client lib | `frontend/packages/core/` | [MODULES.md § @pymoney/core](MODULES.md#pymoneycore-frontendpackagescore) |
| Web app | `frontend/apps/web/` | [MODULES.md § @pymoney/web](MODULES.md#pymoneyweb-frontendappsweb) |
| E2E tests | `frontend/e2e/` | [MODULES.md § e2e](MODULES.md#frontende2e--playwright) |

## Commands

```bash
make install              # .venv (server + desktop) + npm ci (frontend)
make check                # what CI runs for all three, plus i18n-check
make -C server test       # fast: memory + sqlite, dev Hypothesis profile
make -C server test-ci    # CI suite: ci profile, 90% coverage gate
make -C server test-all   # adds postgres
make -C desktop test      # QT_QPA_PLATFORM=offscreen set for you
make -C frontend check    # generate + drift check + typecheck + test + build
run.sh                    # API + Vite dev server together
```

## Keeping These Maps Fresh

Update `docs/CODEMAPS/` when:

- a new package/module is added or removed,
- public API surface changes (exports, routers, wire schemas),
- the architecture or data flow changes (e.g. a new client, a new sync step),
- setup steps or environment variables change.

Regenerate by scanning the code (structure + imports/exports + router prefixes),
then check: every path mentioned exists, all links resolve, freshness timestamps
updated, diagrams still true, no obsolete references.

## Related Documentation

- [README.md](../../README.md) — the long guide: design decisions, persistence,
  importers, API, desktop and web behaviour
- [AGENTS.md](../../AGENTS.md) — contributor commands and gotchas
- [server/README.md](../../server/README.md) · [desktop/README.md](../../desktop/README.md) ·
  [frontend/README.md](../../frontend/README.md) — per-component guides
- `server/docs/openapi.json` — the shipped wire contract
