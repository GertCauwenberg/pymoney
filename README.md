# PyMoney

Double-entry accounting you can self-host. A web app, a desktop client (with an
offline mode), and a server over SQLite or PostgreSQL.

## Features

- **Double-entry books** — hierarchical accounts, payees, tags, and monthly budgets
- **Reports** — balance sheet, income statement, holdings; charts for income/expenses, net worth over time, and holdings
- **Two clients** — a React web app and a PySide6 desktop app that keeps working offline against a local replica
- **KMyMoney import** — one-time migration; every balance matches what KMyMoney displayed
- **Bank statements (CSV)** — map any bank's columns once, review new/duplicate/transfer rows, then post
- **Scheduled transactions** — rent, salary, subscriptions; due when you look, never posted behind your back
- **Price quotes** — ECB reference rates and Yahoo Finance, plus a plugin interface
- **Attachments** — receipts and invoices on the transactions they belong to
- **Sharing** — per-book roles: viewer, editor, owner
- **Languages** — English and Dutch; numbers follow the locale

## Quick start (Docker)

```bash
cp .env.example .env          # then set a real POSTGRES_PASSWORD
docker compose up -d          # PostgreSQL
docker compose --profile api run --rm api python -m pymoney.store.migrate
docker compose --profile api up -d    # API on http://127.0.0.1:8000
```

The API serves interactive docs at `/docs`. Registration is **closed by
default** — open it once to sign up, then close it again:

```bash
# in .env
PYMONEY_ALLOW_REGISTRATION=true
```

Or skip registration entirely: import a KMyMoney file (see below) or grant
access to an existing book by hand:

```bash
docker compose --profile api run --rm api \
    python -m pymoney.store.grants BOOK_ID you@example.org owner
```

### The web app

For development, run the API and the Vite dev server together:

```bash
make install        # shared .venv for server + desktop, npm for the frontend
./run.sh            # API on :8000, web app on http://localhost:5173
```

Or build the static web app for production (`make -C frontend build`) and serve
it from the same origin as the API. See
[frontend/README.md](frontend/README.md#deploying-the-web-app).

### The desktop app

```bash
make -C desktop install
make -C desktop run             # or: python -m pymoney_desktop
```

The login dialog offers “Keep a local copy and work offline”. Offline you can
read, post, edit, and reconcile; writes queue in an outbox and replay when the
server is back. Importing a book or a bank statement needs the connection.

`make -C desktop build` produces a standalone `dist/PyMoney/` folder
(PyInstaller).

## Using PyMoney

### Books and sharing

A new user lands on “create your first book” (name and base currency). Owners
manage **Sharing**: add someone by the email they log in with, and give them
`viewer`, `editor`, or `owner`. A book always keeps at least one owner.

Roles: a **viewer** reads; an **editor** also posts and edits transactions,
reconciles them, and adds payees; an **owner** also changes the chart of
accounts, marks splits frozen, renames the book, and manages sharing.

Registering sends an **activation link** (unless `PYMONEY_REQUIRE_ACTIVATION` is
off); a forgotten password is reset through an emailed link. Both links are
single use and short-lived. Email goes out through `PYMONEY_MAIL_BACKEND` —
with the default `console` backend the link is in the server log, which is fine
on a laptop and not on a server.

**Search** (the web app's Search tab, or Ctrl+F on the desktop) finds a
transaction by a word from a memo, an exact amount or range, a date range, an
account, a payee, or a tag — any combination.

### Importing from KMyMoney

From the web app's books page, or the command line:

```bash
python -m pymoney.importers.kmymoney money.kmy --inspect   # what is in there?
python -m pymoney.importers.kmymoney money.kmy --dry-run   # what would happen?
python -m pymoney.importers.kmymoney money.kmy --database ledger.sqlite --owner you@example.org
```

**Every balance matches the figure KMyMoney displayed, to the minor unit** —
the importer verifies that on your data and says so in its report. Where
rounded split amounts had drifted a minor unit, it posts a correcting entry
against a dedicated equity account and reports each one.

Tags come across. Saved reports, cost centres, and per-account settings do not;
the report counts every one. Reads gzipped or plain XML; GPG-encrypted files
are refused with the `gpg --decrypt` command line rather than asking for your
passphrase.

### Importing bank statements (CSV)

**Import CSV** on any register (web) or the toolbar button (desktop). The
server sniffs the delimiter, header, and number format and suggests a mapping;
confirm it once and it is remembered per account. A grouping mark on the wrong
side of the decimal point is refused, not read.

Nothing is posted without a review. Each row is classified as **new**,
**duplicate** (already in the book, or you typed it in before the statement
arrived), or **transfer** (the mirror image is already recorded). Duplicates
default to skip; the rest post with a proposed payee and category you can
override. Re-importing the same file posts nothing.

A statement is in the account's currency, not the book's: a HUF account in a
EUR book books in HUF, and a EUR category is a conversion at the rate the book
holds for that day. Rows with no rate are reported, not fatal — add the price
and import again.

### Scheduled transactions

**Settings → Scheduled** (web) or **Scheduled…** (desktop) builds a template
plus a recurrence (daily / weekly / monthly / yearly, with an end). Nothing
posts on a timer: a due banner appears, and each one posts when you confirm.
Two people (or an offline replay) confirming the same occurrence posts it
once.

### Attachments

Receipts and invoices attach to a transaction. The bytes are plain files under
`PYMONEY_ATTACHMENTS_DIR` (`/var/lib/pymoney/attachments` in the image), not
rows in the database. Uploads are size-limited (`PYMONEY_MAX_ATTACHMENT_MB`)
and must be a common image format or a PDF. Deleting a transaction removes its
attachments; deleting a book removes its whole blob directory.

### Price quotes

A commodity whose `quote_source` names a provider gets priced automatically —
**`ecb`** for currencies, **`yahoo`** for securities — on a schedule
(`PYMONEY_QUOTE_INTERVAL_MINUTES`) or on demand from **Settings → Commodities**.
Hand-entered prices are never overwritten. Custom providers register through
the `pymoney.quote_providers` entry point or `PYMONEY_QUOTE_PLUGINS`.

Command line, for cron and backfills **when the server is not running** against
the same database:

```bash
python -m pymoney.quotes --list-providers
python -m pymoney.quotes --url ledger.sqlite
python -m pymoney.quotes --url ledger.sqlite --start 2020-01-01   # backfill
```

### Languages

English and Dutch. The language is stored with the user account; before login
the web app uses this browser's choice. Numbers follow the locale too.

## Configuration

All settings are `PYMONEY_*`-prefixed (plus `POSTGRES_*` for the database).
`.env.example` lists every one with comments; the ones that matter most:

| Variable | Default | What it does |
|---|---|---|
| `PYMONEY_DATABASE_URL` | — | SQLAlchemy DSN. Compose sets this for you. |
| `PYMONEY_ALLOW_REGISTRATION` | `false` | Open only while the household signs up. |
| `PYMONEY_REQUIRE_ACTIVATION` | `true` | New accounts wait for an emailed link. |
| `PYMONEY_PUBLIC_URL` | — | Where those email links point (the web app's address). |
| `PYMONEY_MAIL_BACKEND` | `console` | `console` (links in the log), `smtp`, `file`, or `memory`. |
| `PYMONEY_SMTP_*` | — | Host, port, credentials, STARTTLS, when using `smtp`. |
| `PYMONEY_COOKIE_SECURE` | `true` | Turn off **only** for plain-HTTP local development. |
| `PYMONEY_CORS_ORIGINS` | `[]` | Exact origins, if the web app is on another origin. |
| `PYMONEY_ATTACHMENTS_DIR` | `/var/lib/pymoney/attachments` | Where receipt bytes live. Must be a persistent volume. |
| `PYMONEY_MAX_ATTACHMENT_MB` | `25` | Cap on one attachment upload. |
| `PYMONEY_MAX_UPLOAD_MB` | `50` | Cap on a KMyMoney / CSV upload. |
| `PYMONEY_QUOTE_INTERVAL_MINUTES` | `360` | Quote fetch cadence; `0` disables the scheduler. |
| `PYMONEY_CLIENT_IP_HEADER` | — | Set to `X-Forwarded-For` behind a reverse proxy. |
| `PYMONEY_AUTH_RATE_*` | see `.env.example` | Throttling for login and email-sending endpoints. |

Migrations are a **deployment step**, never a start-up side effect:

```bash
python -m pymoney.store.migrate      # or: make -C server migrate
```

## Backups

Two things must be captured *together*, or the metadata and the files drift
apart:

1. the database (`pg_dump` for PostgreSQL, or the SQLite file);
2. `PYMONEY_ATTACHMENTS_DIR` — the receipt blobs the rows point at.

Dump the database first, then copy the directory — any file added in between is
an extra blob, not a broken reference. The directory is hash-named and safe to
rsync incrementally.

## Development

```bash
make install                  # .venv (server + desktop) + npm ci (frontend)
make check                    # what CI runs, for all three components
make -C server test           # fast loop: memory + sqlite
make -C server serve          # the API on http://127.0.0.1:8000/docs
```

Each component has its own `Makefile` and README:
[server/](server/README.md) · [desktop/](desktop/README.md) ·
[frontend/](frontend/README.md). Contributor commands, generated files, and
gotchas are in [AGENTS.md](AGENTS.md). Design decisions and technical notes are
in [docs/DESIGN.md](docs/DESIGN.md);

## Not here yet

Investment lots and cost basis · loan amortisation · JSON import/export · a
dedicated tag-management page · payee and commodity management in the desktop
app · mobile client.
