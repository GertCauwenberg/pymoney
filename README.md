# PyMoney

Double-entry accounting that runs everywhere: the domain model, persistence
over SQLite and PostgreSQL, a KMyMoney importer, the HTTP API, a web frontend
and a desktop client.

Three components, three directories, each deployable -- and checked -- on its
own:

```
server/     the Python package `pymoney-core`: the domain, the stores, quotes,
            the importer and the HTTP API. Its own pyproject, tests, Dockerfile,
            Alembic configuration and the shipped API contract (docs/openapi.json).
desktop/    the Python package `pymoney-desktop`: the PySide6 client. Depends on
            server/ (it speaks the API's schemas and, offline, runs the API's read
            models on a local replica). Its own pyproject, tests and PyInstaller spec.
frontend/   the npm workspace: @pymoney/core (platform-agnostic client, types
            generated from the server's contract) and @pymoney/web (React), plus
            the Playwright end-to-end suite. Its own Dockerfile (nginx).

Makefile            delegates: `make check` checks all three
compose.yaml        the local stack: PostgreSQL, the server image, the suite
scripts/i18n.py     the one cross-component tool: the Dutch catalogue must cover both clients
```

Inside `server/src/pymoney`:

```
core/                           the domain. pure stdlib, no dependencies
    ids.py          UUIDv7, monotonic
    clock.py        injectable clock (UTC instants)
    entity.py       id / book_id / created_at / updated_at / revision
    errors.py       exception hierarchy
    rounding.py     exact rounding modes + the zero-sum allocator
    commodity.py    currencies and securities, uniformly
    amount.py       Amount (booked) vs RationalAmount (exact) vs Balances
    account.py      typed, hierarchical accounts
    payee.py        payees, with merge support
    tag.py          free-form labels put on a transaction's legs
    transaction.py  Transaction + Split, and their invariants
    posting.py      LegSpec -> booked splits (the rounding problem)
    price.py        bitemporal prices, valuation policy, conversion graph
    changelog.py    revisions, tombstones, audit trail
    sync.py         revision batches and pages: the pull protocol
    settings.py     book-level settings as a syncable entity
    store.py        the storage contract, and the in-memory implementation
    commands.py     the intents clients send
    handlers/       command handlers, one module per aggregate
    book.py         the aggregate: consistency boundary, dispatcher, read model
    reports.py      balance sheet, income statement, holdings; net worth over time, income/expenses by period
    recurrence.py   RFC 5545 subset: the k-th occurrence as a pure function
    schedule.py     Schedule = a date-less template + a recurrence + a cursor

store/                          persistence. SQLAlchemy Core, no ORM mapping
    types.py        portable UUID / JSON / timezone-aware timestamp columns
    schema.py       tables and indexes
    mapper.py       domain objects <-> rows
    sql.py          SqlStore: the Store contract over a database
    engine.py       engine creation, SQLite pragmas, open_book
    migrate.py      apply the migrations from an installed package (no alembic.ini)
    grants.py       who may see a book; the command line for books made outside the API
    migrations/     Alembic

quotes/                           price quotes
    provider.py     the QuoteProvider interface: one request in, exact quotes out
    ecb.py          ECB daily reference rates (currencies, exact decimals)
    yahoo.py        Yahoo Finance closes (securities; floats, rounded to Yahoo's own precision)
    registry.py     built-ins plus plugins: entry points or PYMONEY_QUOTE_PLUGINS
    service.py      plan, fetch, de-duplicate, store as Prices; the run report
    scheduler.py    the periodic thread inside the API process
    __main__.py     the command line, for cron and backfills

importers/kmymoney/               migration from KMyMoney (once)
    model.py        the file's own structures, unintepreted
    parser.py       .kmy reading: gzip, XML, streamed transactions
    importer.py     model -> book, including the rounding reconciliation
    report.py       what was done, approximated, and skipped
    __main__.py     the command line

importers/statement/              bank statements (CSV), every week
    mapping.py      which column is which; lenient, exact value parsing
    source.py       sniff the delimiter, header and preamble; suggest a mapping
    entry.py        a mapping applied: canonical rows, each with its dedup key
    matching.py     payee + category from the description; the new-payee guess
    proposals.py    new / duplicate / transfer, read-only, for the review screen
    commit.py       the reviewed rows, posted in one book.bulk()

api/                              the HTTP API
    schemas.py      wire models, deliberately not the domain objects
    security.py     password hashing, session tokens, idempotency keys
    registry.py     one open Book per book id, one lock per book
    deps.py         who is calling, what they may do, which book
    uploads.py      chunked, size-limited read shared by the upload endpoints
    blobs.py        attachment bytes on disk, one file per attachment
    errors.py       domain errors -> status codes, in one place
    testing.py      the in-process API and HTTP client both test suites drive
    routers/        one module per resource
    app.py          the application factory
```

And `desktop/src/pymoney_desktop`:

```
client.py       LedgerClient: the one interface the UI talks to
transport.py    HTTP as one function, swappable for a test
remote.py       online: every write a request; a revision-invalidated read cache
offline.py      a local SQLite replica, an outbox, replay; rejections rebuild
replica.py      server sync batches -> local rows
drafts.py       transaction form logic without Qt (the twin of the web's ledger.ts)
ui/             the windows, dialogs, charts and the theme
```

## Status

| | |
|---|---|
| Tests | 3,000+ — the behavioural suite runs against every storage backend |
| Backends | memory, SQLite (memory and file), PostgreSQL — all run the full suite |
| Coverage | 99% statements and branches |
| Type checking | `mypy` clean, no `type: ignore` in the domain |
| Lint | `ruff` clean |
| Dependencies | none for `pymoney.core`; SQLAlchemy + Alembic (and `defusedxml`, for the importer) for `pymoney.store` |

## Development

```bash
make install                  # one .venv for server + desktop, npm for the frontend
make check                    # what CI runs, for all three components
make -C server help           # the server's targets: test, test-all, serve, migrate, openapi, image...
make -C server test           # fast loop: memory + sqlite
make -C server test-all       # every backend, PostgreSQL included
make -C server bench-scaling  # does the ledger stay fast as it grows?
make -C server test-thorough  # 5000 Hypothesis examples; when touching rounding
make -C desktop help          # the desktop's: test, run, build
make -C frontend help         # the frontend's: dev, test, check, e2e
```

Every component has its own `Makefile`, `check` target and coverage gate; the
root Makefile only delegates. The two Python packages share one virtualenv in a
checkout (`.venv` at the root) because the desktop depends on the server, but
they are separate distributions: the server image never installs Qt, and the
desktop suite never runs inside it.

Optionally `pip install pre-commit && pre-commit install` for the fast checks on
commit.

Three Hypothesis profiles, because the property tests are the only real check on
the rounding invariants: `dev` (50 examples, the default), `ci` (500,
derandomised so a CI failure is reproducible) and `thorough` (5000).

### Containers

```bash
cp .env.example .env                                        # then change the password
docker compose up -d                                        # PostgreSQL
docker compose --profile api run --rm api python -m pymoney.store.migrate
docker compose --profile api up -d                          # the API on 127.0.0.1:8000
make -C server image-test                                   # run the server suite inside its image
```

`compose.yaml` has three services. `db` is PostgreSQL; its init script creates
a separate `pymoney_test` database so tests can never touch development data.
`tests` runs the suite in the shipped image. `api` runs uvicorn with a single
worker — the per-book caches and locks live in the process, so a second worker
would write behind the first's back.

Migrations are a deployment step, never a start-up side effect (two replicas
starting together would race through the same DDL). `python -m
pymoney.store.migrate` builds the Alembic configuration from the installed
package, so it works inside the image where there is no `alembic.ini`; `make
migrate` runs the same thing from a checkout.

Registration is closed by default in the compose environment. Either open it
(`PYMONEY_ALLOW_REGISTRATION=true`) while the household signs up, or create a
book some other way — the KMyMoney importer's `--owner you@example.org`, or
`python -m pymoney.store.grants BOOK_ID you@example.org owner` afterwards. A
book nobody holds a grant on is invisible to every user, by design.

### Attachments and state on disk

Receipts, invoices and scans attached to a transaction are the one thing the
server keeps **outside the database**: the bytes are plain files under
`PYMONEY_ATTACHMENTS_DIR` (`/var/lib/pymoney/attachments` in the image), one per
attachment, in a per-book subdirectory; the `attachments` table holds only the
link and the metadata (name, type, size, SHA-256, who and when). The uploader is
not trusted — the read is chunked against `PYMONEY_MAX_ATTACHMENT_MB` (the
`Content-Length` header is ignored), the type must be a common image format or a
PDF, and a magic-byte sniff catches a mislabelled file. The server stores the
bytes as received and does no image processing. Every route needs a grant on the
book; `viewer` to list or download, `editor` to add or remove.

A receipt can be chosen *while entering* a transaction, which is when you are
most likely to have it: the row has a foreign key to the transaction, so the
client holds the files and uploads them the instant the transaction is posted
and has an id. A desktop that is offline queues the transaction as usual and
attaches the receipts once it reconnects, from the transaction window — the
blobs only ever exist server-side, so there is nowhere to queue them.

The API container runs **read-only** (`read_only: true`, only `/tmp` and the
attachments volume writable), so the directory has to be a mounted volume or the
blobs vanish on the next `docker compose up`. `compose.yaml` mounts the named
volume `attachments`; `compose-production.yaml` uses `pymoney-attachments`,
overridable with a bind mount (`PYMONEY_ATTACHMENTS_VOLUME=/srv/pymoney/receipts`)
if you would rather keep them in a directory you already manage. Deleting a
transaction removes its attachments (rows and blobs); deleting a book removes
its whole blob directory.

**Backups.** A database dump is no longer the whole picture. Two things must be
captured *together*, or the metadata and the files drift apart:

1. the database (`pg_dump` for PostgreSQL, or the SQLite file) — accounts,
   transactions, the change log, and the attachment rows;
2. `PYMONEY_ATTACHMENTS_DIR` — the blobs those rows point at.

Order does not matter much (a blob with no row is ignored; a row whose blob is
missing serves a clear `410`, and the metadata still shows what it was), but
skipping the directory loses every receipt, and skipping the database loses
which transaction each one belonged to. A consistent snapshot is: dump the
database, then copy the directory — any file added in between is an extra blob,
not a broken reference. The directory only grows with what users upload; it is
hash-named and safe to rsync incrementally.

### CI

`.github/workflows/ci.yml` has a job group per component. Server: lint +
format + contract, `mypy`, the suite on Python 3.12 and 3.13 with the `ci`
Hypothesis profile and the coverage gate, PostgreSQL, and a container job that
builds both image targets, runs the suite inside the test image (the server
alone -- no Qt, nothing skipped) and validates `compose.yaml`. Desktop: lint,
types, the offscreen Qt suite with its own coverage gate. Frontend: generated
types, typecheck, tests, build, and the browser end-to-end suite. Shared: the
translation catalogue. A single aggregate `CI` check is the one to require in
branch protection. There is a weekly scheduled run: the core has no runtime
dependencies, but the dev tools do, and a new ruff or mypy release should not
first show up on someone's unrelated pull request.

## Five-minute tour

```python
from datetime import date
from fractions import Fraction
from pymoney.core import AccountType, Book, Commodity, LegSpec, balance_sheet

book = Book(base_currency=Commodity.currency("EUR", symbol="€"))

assets   = book.add_account("Assets", AccountType.ASSET, placeholder=True)
checking = book.add_account("Checking", AccountType.CHECKING, parent=assets)
food     = book.add_account("Groceries", AccountType.EXPENSE)
opening  = book.add_account("Opening Balances", AccountType.EQUITY)

book.post(date(2026, 1, 1),
          [LegSpec.of(checking.id, "2500.00"), LegSpec.of(opening.id)],
          memo="Opening balance")

# One leg left blank is the residual: it absorbs whatever balances the entry.
book.post(date(2026, 1, 5),
          [LegSpec.of(checking.id, "-84.20"), LegSpec.of(food.id)],
          memo="Supermarket")

book.balance(checking.id)          # Amount(EUR 2415.80)
book.path(food.id)                 # 'Groceries'
book.revision                      # 6
balance_sheet(book, book.default_policy(date(2026, 1, 31))).net_worth
```

Splitting a bill three ways keeps every cent:

```python
third = Fraction(10, 3)
book.post(date(2026, 1, 6), [
    LegSpec.of(checking.id, "-10.00"),
    LegSpec.of(a.id, third), LegSpec.of(b.id, third), LegSpec.of(c.id, third),
])
# booked values: -1000, 334, 333, 333  -> sums to exactly zero
```

## The decisions, and where they live

| # | Decision | Where |
|---|---|---|
| 1 | Account / Transaction / Split, ≥2 splits, values sum to zero | `transaction.py`, `Transaction.validate` |
| 2 | Currencies and securities are both "commodities" | `commodity.py`, `price.py` |
| 3 | Rates as `num`/`den`; booked amounts as integer minor units | `price.py`, `amount.py` |
| 4 | Banker's rounding by default, overridable per transaction | `rounding.py`, `Transaction.rounding` |
| 5 | UUIDv7 everywhere, `updated_at` + per-book `revision` | `ids.py`, `entity.py` |
| 6 | Server-authoritative: clients send commands, get results | `commands.py`, `Book.execute` |
| 7 | `book_id` on every row from day one | `entity.py` |
| 8 | Python 3.12+, stdlib only in the core | `pyproject.toml` |
| 9 | Prices are bitemporal: `effective_date` + `as_of` | `price.py` |
| 10 | Post dates are plain dates; audit stamps are UTC instants | `clock.py`, `Transaction.date` |
| 11 | Reconciled data is immutable; everything is audited | `ReconcileState`, `changelog.py` |
| 12 | Reports take an explicit `ValuationPolicy` | `price.py`, `reports.py` |
| 13 | Renames, moves and merges are first-class commands | `MergeAccounts`, `MergePayees` |
| 14 | One writer per store | `Book` is the only mutator; `_put`/`_drop`/`_set_book_field` assert |

## Persistence

The domain does not know the store exists. `SqlStore` implements
`pymoney.core.store.Store`, so the same `Book` — same invariants, same commands,
same tests — runs over memory or over a database. That promise is mechanised:
`pytest --stores=memory,sqlite,sqlite-file,postgres` runs **the entire
behavioural suite against every backend**, 367 tests × 4.

Three decisions carry most of the weight:

**`post_date` is denormalised onto `splits`.** Every register, balance and report
asks "this account, up to this date", and with the date on the split that is
answered from one index without touching `transactions` at all. Splits are only
ever written as part of their transaction, so the copy cannot drift — and there
is a test asserting it stays in step after a date edit.

**The hot index is covering.** `ix_splits_book_id_account_id_post_date` includes
`quantity_minor`, which is never filtered on — it is there so the aggregate
`SELECT account_id, SUM(quantity_minor) … GROUP BY account_id` is answered from
the index alone. Without it SQLite walks the index and then fetches every row,
which is *slower than the table scan it was avoiding*. Measured at 50k
transactions: every balance in the book 173 ms → 16 ms, one account's balance
9.1 ms → 1.0 ms, for 0.6% more disk.

**Small tables are cached, large tables are queried.** Commodities, accounts,
payees and prices are tiny and read constantly, so the store holds them in
memory; transactions, splits and the change log are never cached. This assumes
the store is the only writer — which is decision #14 — and `reload()` exists for
when that has to be re-established.

### Running the backends

```bash
pytest                                  # memory + sqlite (the default)
pytest --stores=postgres                # needs PYMONEY_TEST_DATABASE_URL,
                                        # or falls back to a throwaway pgserver
make up                                 # PostgreSQL via compose
make -C server migrate                  # python -m pymoney.store.migrate, $PYMONEY_DATABASE_URL
```

A few tests are memory-only, and deliberately so: `check_integrity` looks for
damage like a split pointing at no account, and a relational store makes that
*unrepresentable*. Those skip with an explanation rather than pretending to pass.

## Importing from KMyMoney

```bash
python -m pymoney.importers.kmymoney money.kmy --inspect   # what is in there?
python -m pymoney.importers.kmymoney money.kmy --dry-run   # what would happen?
python -m pymoney.importers.kmymoney money.kmy --database ledger.sqlite --owner you@example.org
```

`--owner` grants that login owner access in the API; without it the imported
book exists but nobody can see it (`python -m pymoney.store.grants` fixes that
later).

A user does not need the command line at all: the web app's books page has
**Import from KMyMoney**, which uploads the file to `POST /books/import/kmymoney`.
The server makes a new book from it, imports, and grants the uploader `owner`,
in one request -- a failed import leaves no book behind -- and returns the same
report the command line prints (`PYMONEY_MAX_UPLOAD_MB`, default 50, bounds the
file).

The guarantee: the same accounts exist, and **every balance matches the figure
KMyMoney displayed, to the minor unit.** The importer verifies that itself, on
your data, and says so in the report.

Reads gzipped or plain XML; refuses GPG-encrypted files with the `gpg --decrypt`
command line rather than asking for your passphrase. Since anyone with an
account can upload one, the XML goes through `defusedxml` (no entity expansion,
no external entities) and a gzip stream is capped on its *decompressed* size. Transactions are streamed,
so memory does not depend on how long you have been using KMyMoney. Measured:
**772 transactions/second**, so a 50,000-entry ledger takes about a minute and a
200,000-entry one about four.

### The rounding problem, which is the whole difficulty

KMyMoney stores split amounts as **unbounded rationals**; PyMoney stores them as
**integer minor units** (decision #3). Both are deliberate, and the consequence
is arithmetic: *summing rounded amounts is not the same as rounding a summed
amount.* Three splits of `1000/3` come to exactly 1000 in KMyMoney, which
displays 1000.00. Booked individually they are 333.33 × 3 = 999.99.

So the importer measures the drift per account, posts a correcting entry against
a dedicated equity account, and reports every one:

```
  rounding adjustments: 2 account(s), 2 minor unit(s) in total
      Assets:Dollar Wallet: imported 50.99, KMyMoney shows 51.00,
          adjusted by 0.01 USD over 5 split(s)
      Assets:Brokerage:Apple: imported 12.9999, KMyMoney shows 13.0000,
          adjusted by 0.0001 AAPL over 4 split(s)
```

Two things worth knowing:

**It happens less than you would expect.** The value column is booked by the
zero-sum allocator, which only floors or ceils and never disturbs a leg that was
already exact — so a bill split three ways comes to 10.00 without any help. Drift
survives only where a *quantity* is rounded independently of the value column:
foreign-currency holdings and fractional shares.

**The correction is dated at the end of the ledger.** A balance queried
*mid-history* can therefore still be a minor unit away from KMyMoney's. Closing
that would need a correction wherever the drift crosses a unit, which is a lot of
register noise for a cent. `--no-rounding-adjustments` reports the differences and
posts nothing, if you would rather see the raw sums.

### What does not come across

Budgets, saved reports, cost centres, online
banking jobs, and per-account settings like `PreferredAccount`. None of it is
dropped quietly — the report counts every one. Also reported: KMyMoney types we
narrow (a certificate of deposit becomes a savings account), colons stripped from
account names (we use them as the path separator), duplicate payee (and tag)
names disambiguated, and any transaction that could not be stored at all.

Tags *do* come across: KMyMoney's `<TAG>` definitions become our tags, and the
`<TAG id="…">` link on a split is re-hung on the same leg, id for id.

An import is one unit of work, so a failure leaves no half-imported book — but a
single malformed transaction is skipped and reported rather than fatal, which is
what `book.bulk()`'s per-command savepoints are for. A second import of the same
file is refused up front, before anything is created, on the strength of the
`imported_id` prefix.

## Importing bank statements (CSV)

The KMyMoney importer is the one-time migration. The other half of a real book
is getting *next week's* download in, into an account that already has a
history, a chart of accounts and a list of payees to reconcile it against.
`pymoney.importers.statement` does that, and both clients have it — the web
app's **Import CSV** on a register, the desktop's toolbar button.

**Every bank's CSV is different, so the mapping is the feature.** Which column
is the date, which is the amount (one signed column, or separate debit and
credit), whether it is written `1,234.56` or `1.234,56`, how many summary lines
sit above the header, which column (or columns, joined) carry the merchant
text. The server sniffs all of it and *suggests* a mapping; the user confirms
it once, and it is [remembered per account](#the-remembered-mapping) so the
next download opens straight on the review screen. A grouping mark on the
wrong side of the decimal point — the commonest way an import goes quietly
100× wrong — is refused, not read. The two text roles — the description the
payee matcher reads, and the memo the transaction keeps — fall back to each
other, so a file with one descriptive column is mapped once and every posted
row carries the bank's own wording, matched or not.

**A statement is in the account's currency, not the book's.** Import a HUF
current account into a EUR book and every transaction is denominated in HUF;
a category held in EUR is a genuine conversion, booked at the rate the book
holds for the statement date — either stored direction, triangulated if need
be. A row for which no rate exists is reported, not fatal: add the price and
import the file again (it is idempotent, so the rows that did post are
skipped the second time).

**Nothing is posted without a review.** Each row comes back classified:

| | |
|---|---|
| **new** | not seen before — post it |
| **duplicate** | a transaction with this row's dedup key is already in the book (an overlapping download), *or* one in this account with the same amount on the same day (you typed it in before the statement arrived) |
| **transfer** | the mirror image is already recorded against another of your own cash accounts, and posting this would count the money twice |

Duplicates and recorded transfers default to *skip*; the rest to *post*, with a
proposed payee and category the reviewer can override per row. The guesses
follow the order the domain's `Payee` was built for — `match_keys` and
`default_account_id` have carried a comment about import matching since before
there was an importer. A `match_keys` hit picks the payee; its
`default_account_id`, or failing that the category it was last filed under,
picks the account; no match at all offers a cleaned-up name (`"POS TESCO
STORES 2243 LEEDS GB"` → *Tesco Stores 2243 Leeds*) as a new payee. The commit
re-classifies under the book's lock — the
far side of a transfer may have posted in another tab since the preview — and
writes the lot in one `book.bulk()`: all-or-nothing against the database, a
malformed row reported rather than fatal, N rows naming the same new payee
creating one payee.

**Duplicate detection is the load-bearing promise.** The dedup key is a
`csv:`-prefixed hash (distinct from the KMyMoney importer's `kmm:`), carried
into the transaction's `imported_id` where the domain's
`uq_transactions_book_id_imported_id` constraint enforces it. Where the bank
gives each line its own reference number — map that column — overlapping
downloads are free. Without one, the key falls back to (date, amount,
description, order in the file): exact for a clean re-import, best-effort for a
download whose window overlaps the last. Re-importing the same file posts
nothing and does not burn a revision.

**The upload, three times.** `inspect` → `preview` → `commit` are three
stateless calls; the file is uploaded each time. There is no half-built import
for the server to keep alive between requests, and a reviewer's decision can
only steer the soft fields — the date and the amount are the bank's, not
theirs. The desktop's offline client sends all three straight to the server
(the duplicate and transfer checks need the book the server holds, not a
replica a write behind), so importing a statement needs the connection exactly
as creating a book does.

### The remembered mapping

`import_mappings` is a table beside `command_results`: one row per account,
holding the last `ColumnMapping` used. Like the idempotency record it is not
part of the ledger — never synced, never in the change log, a book with none
behaves exactly as one with them — so it lives in `pymoney.store`, managed
through three functions (`get` / `save` / `forget`) rather than a command. A
successful import saves the mapping it used; `GET`/`PUT
/accounts/{id}/import-mapping` reads and writes it directly, for a mapping
built or fixed without importing in the same breath. Deleting the account
forgets it, since nothing else refers to it.

## Scheduled / recurring transactions

Rent, a salary, a subscription -- the other half of daily entry, and the thing
KMyMoney users lose on the way in if it is not here. A `Schedule` is a
**date-less transaction template**, a **recurrence** (a small exact subset of
RFC 5545 -- daily / weekly / monthly-by-date-or-nth-weekday / yearly, every
*interval* units, ending after *N* or on a date), and a **cursor**:
`occurrences_done`, the one number that says where in the series we are.
Everything else -- `next_due`, "is it finished", "how many are overdue" -- is
derived from it, and `recurrence.nth(k)` is a pure function of the start and
*k*, so the cursor is always recomputable and the monthly 31st clamps to the
28th and comes *back* to the 31st without drifting.

**Nothing posts on a timer.** Given "one writer per book" and an offline
desktop, server-side auto-posting would mean either a second writer or money
appearing behind the user's back. Instead it is *due-on-sight*: any client --
web, desktop, online or on a replica -- calls `GET
/books/{id}/schedules/due`, shows "3 scheduled transactions are due", and
posts each one on confirmation through `POST /schedules/{id}/post`. That is an
ordinary command: one revision for the posted transaction *and* the advanced
cursor together, an `Idempotency-Key` so a retry is a no-op, and a per-occurrence
`legs` override for the month the figure is different. `auto_post` only lets a
client skip the confirmation click; the server still never acts on its own.

**Two clients, no double post.** The posted transaction is stamped
`imported_id = sched:<schedule id>:<date>` (the same uniqueness the CSV
importer leans on). When the second spouse confirms the rent -- or an offline
replay lands after the web already posted it -- the command finds that
transaction already there, advances its own cursor to match, and returns it.
No shared key, no merge, no duplicate. The offline client pins both the
occurrence date and the transaction id in its outbox entry so the replay is
byte-for-byte the same row.

It is a full syncable entity: `EntityKind.SCHEDULE`, a cached `schedules`
table (small, read by the due panel) with migration `46cb494dbd9e`, a typed
`schedules` list in every sync batch, replica support and offline outbox ops.
The KMyMoney importer maps `<SCHEDULED_TX>` across -- the recurrence code to
the nearest rule it can express, `autoEnter` to `auto_post`, `fixed="0"` to an
estimate -- and reports the ones it can only approximate or cannot express.

The web app has **Settings → Scheduled** (a two-sided template builder and a
recurrence editor) and a **due banner** on every book page; the desktop has
the same, and both post through the idempotent endpoint.

## The HTTP API

```bash
make -C server serve    # http://127.0.0.1:8000/docs
make -C server openapi  # regenerate server/docs/openapi.json
```

`server/docs/openapi.json` is committed and CI fails if it drifts from the code,
so a client generated from it is never wrong about the server.

**Amounts and rates are never JSON floats.** `0.1` is not one tenth, so an amount
crosses the wire as a string (`"12.34"`, `"10/3"`) or an integer and a JSON float
is *rejected*, not rounded. Amounts come back as exact minor units plus a
formatted string; rates as an exact `num`/`den` pair plus a decimal approximation
marked as such. That is the Phase 1 rule about `to_fraction`, carried to the edge.

**Writes are commands.** `POST /transactions` takes legs as exact rationals and
the server decides how they book — which leg absorbs a rounding remainder is not
something a client can work out without reimplementing the allocator. Structural
operations each get their own endpoint (`/accounts/{id}/rename`, `/move`,
`/merge`, `/closed`, `/commodity`) because each has its own rules and its own way
of failing; one generic PATCH would be five unrelated failure modes behind a body
where every field is optional.

**Finding a transaction** is `GET /transactions/search`: a word from a memo or
number, an exact amount or a range (matched against a leg's quantity in its own
commodity *or* its value in the transaction currency, so 84.20 finds the
supermarket entry either way you think of it), a date range, an account (with or
without its sub-accounts), a payee, a tag -- any combination, all ANDed, newest
first, with `has_more` when the limit cut the list short. The predicate is
pushed down to SQL in exact integer arithmetic and held to the same answers as
the Python reference implementation by the store contract tests. Both clients
have a search screen over it (the web app's **Search** tab; the desktop's
Ctrl+F), and a hit opens the transaction.

**Tags** (`tag.py`, `/books/{id}/tags`) are the free-form axis a payee and an
account cannot capture: a holiday, a renovation, a reimbursable run of
expenses. They are put on a *leg*, not the whole transaction, so one entry can
split a shared bill between "Holiday" and nothing at all. Like payees they are
a small, cached, id-referenced lookup table -- a rename is one row -- and they
are joined to legs through `split_tags` rather than a JSON column, so "every
transaction with this tag" is an indexed query that SQLite and PostgreSQL agree
on. Both transaction forms type-ahead over the existing tags and create a new
one inline (the twin of the new-payee flow); a tag a transaction still wears
cannot be deleted, only closed to hide it from the pickers.

**Every write accepts `Idempotency-Key`.** Any string, hashed deterministically
-- together with the caller's identity, so one user's key can never replay
another's request -- into a command id. A replay returns the original result
with `Idempotency-Replayed: true` and does not burn a revision. The record is
written in the command's own database transaction (`command_results`), so a
retry that arrives after the server restarted is still answered rather than
applied twice; and a key reused for a *different* payload is refused with a
409, because answering it with the first request's result would hide a client
bug behind a success.

**Every response carries `X-PyMoney-Revision`**, so a client learns it is stale
from whatever call it happened to make. `GET /books/{id}/changes?since=N` returns
whole revision batches — `limit_rows` is a *soft* cap that never splits a
command, because half a merge is a state the server was never in.

### Auth

Session cookies, plus a bearer token for clients that cannot hold cookies
(desktop, mobile). Cookie-authenticated *writes* also require `X-CSRF-Token`,
because a cookie travels automatically and so proves nothing about who started
the request; a bearer client needs no CSRF token because it has no ambient
credentials. Passwords are scrypt with per-user salts and the parameters stored
alongside each hash; session tokens are stored only as a SHA-256 digest, so a
leaked database yields no live sessions.

Per-book grants are `viewer` / `editor` / `owner`: a viewer reads, an editor
also posts and edits transactions, reconciles them (up to `RECONCILED`, which can
be walked back) and adds payees, an owner also changes the chart of accounts
(which reshapes every report the household shares), marks splits `FROZEN` (which
cannot be walked back, so it is nobody else's to do, and is never accepted on a
new posting), renames the book and manages sharing. They are keyed on a *subject string*
rather than a user id — which is what lets OIDC slot in later and map a token's
`sub` claim onto the same grants. An email subject is normalised the way logins
are (stripped, casefolded), so `Bob@Example.org` and `bob@example.org` are one
person; anything without an `@` is opaque and matched exactly. A book always
keeps at least one owner: the last one can be neither revoked nor demoted, by the
API or the command line, until another owner exists. A book you hold no grant on is a 404, not a
403: whether it exists is itself information.

### Accounts: getting in, getting out

Registering sends an **activation link** (`PYMONEY_REQUIRE_ACTIVATION`, on by
default): the account exists but cannot log in until the link is followed, and
a never-activated account is deleted after `PYMONEY_PURGE_UNACTIVATED_AFTER_HOURS`
(a week) by the maintenance job, so a mistyped address does not stay reserved.
A **forgotten password** is reset through an emailed link; using it ends every
other session of the account. Both links are single use, short-lived, and stored
only as digests -- like session tokens, a leaked database yields none of them.
`/forgot-password` and `/resend-activation` answer 202 whether or not the address
is registered; the email is the only channel that confirms anything. Registering
an address that is still pending activation does not replace the pending account
(anyone could type a stranger's address); it sends the activation link again.

Everything a stranger can call is **throttled**: logins per client address and
per account, and the three endpoints that send email per client address and per
address written to (`PYMONEY_AUTH_RATE_*`, a 429 with `Retry-After` beyond the
limit). An unknown address costs a login attempt the same scrypt derivation as a
wrong password, so the response time does not say which addresses exist. Behind a
reverse proxy set `PYMONEY_CLIENT_IP_HEADER=X-Forwarded-For`, or every user
shares the proxy's bucket.

Email goes through `PYMONEY_MAIL_BACKEND`: `console` (the default: the link is
in the server log, which is fine for one person on a laptop and a startup
warning everywhere else), `smtp` (`PYMONEY_SMTP_*`), `file` (one JSON line per
message, for the browser tests) or `memory`. Links point at
`PYMONEY_PUBLIC_URL`, the web app's address.

**Deleting a book** is `DELETE /books/{id}` for an owner, with the book's exact
name in the body as confirmation; everything goes, change log and grants
included, and there is no undo. **Deleting an account** (`DELETE /auth/me`, with
the password) is refused while the user owns a book -- a book with no owner
could never be administered again -- and otherwise removes the login, its
sessions and its grants on other people's books.

### One writer per book

The API keeps **one `Book` object per book id** for the life of the process, with
**one lock per book held across every request** touching it. That is not a
workaround — it is decision #14. The store caches the small tables, so reopening
per request would reload them every time.

The store does **not** hold a database connection between commands. One is
borrowed from the pool for the duration of a unit of work, or for a single read,
and returned at once; the alternative — a connection pinned for the life of the
book — left a read transaction open between requests, which on SQLite made the
next write fail with `database is locked` as soon as any other connection had
written, and cost a pool slot per open book.

The cost is explicit: requests against the *same* book are serialised; different
books run concurrently. For a personal finance server that is the right trade
(commands take single-digit milliseconds).

## Performance

`server/benchmarks/bench_store.py` measures it; `server/tests/test_performance.py` keeps it
true. The tests make no wall-clock assertions — those are flaky on shared
runners — and instead pin **statement counts** (an N+1 fails the build) and
**query plans** (an index that stops being used fails the build).

SQLite, file-backed, 135 accounts, figures in milliseconds:

| ledger size | 1 balance | 1 month register | all balances | balance sheet | sync page | 1 txn by id |
|---|---|---|---|---|---|---|
| 25,000 | 0.6 | 6.4 | 8.4 | 12.0 | 1.0 | 0.4 |
| 50,000 | 0.9 | 6.1 | 14.7 | 20.8 | 1.0 | 0.4 |
| 100,000 | 1.8 | 6.7 | 28.5 | 39.8 | 1.0 | 0.4 |
| 200,000 | 4.1 | 10.2 | 71.3 | 112.8 | 1.2 | 0.4 |

Reading that table is the point. A windowed register, a sync page and a
transaction lookup are **flat** — they cost the same at 200k as at 25k, which is
what the indexes are for. A balance and a balance sheet are **linear**, and
inherently so: an as-of-today balance is a sum over all history. Indexes cannot
fix that; running-balance snapshots would, and that is a deliberate non-goal for
now (see below).

Writes: 724 transactions/second one command at a time, 1,598/second inside
`book.bulk()`. The difference is the per-command commit and book-row update,
which an importer should not pay. A 50,000-transaction migration takes about 30
seconds.

## Things worth knowing before you build on this

**The rounding guarantee.** `book_legs` promises three things, and the property
tests in `server/tests/test_posting.py` enforce them for arbitrary inputs:

1. booked values sum to exactly zero;
2. no leg is distorted by more than one minor unit;
3. a leg whose exact value is already representable is **never** adjusted — the
   rounding error always lands on a leg that was inexact to begin with.

Point 3 is the one users notice: pay a 100.00 EUR invoice from a USD account and
the 100.00 stays 100.00.

**Quantity vs. value.** Every split carries both. `quantity` is in the account's
own commodity (10 shares, −1500 EUR); `value` is in the transaction currency and
is the column that must balance. For single-currency entries they are identical
and `Transaction.validate` enforces that.

**A stock split is the one transaction with a single leg.** "Values sum to
zero" is the real invariant; "at least two legs" is its usual *consequence*. A
four-for-one split costs and earns nothing — it just multiplies the share count
— so it is one leg, a change in `quantity`, a `value` of zero, `action` set to
`SPLIT`. `Transaction.validate` admits exactly that shape (a lone leg that is
neither free nor a quantity change is still rejected). `RecordStockSplit` takes
a *ratio* (new-for-old: `2`, or `Fraction(1, 3)` for a reverse split), books the
delta against the holding as of the split date, and — where a price is already
on file — records a `PriceSource.SPLIT` one adjusted by the same ratio, so a
report does not read the position as N times richer the morning the extra
shares land. KMyMoney writes a split as precisely this single-`<SPLIT>`
transaction; the importer used to drop it as "only one split" and now keeps it.
It is recorded and deleted, not edited: get the ratio wrong and you delete it
and record it again.

**Posting picks the currency; editing keeps it.** A new transaction's currency
is chosen on the form (it defaults to the book's, which is what the register you
started from uses); once the transaction exists the control is read-only,
because its splits' `value`s are already denominated in it and reinterpreting
them is never what an edit means. Give a leg in another commodity both its value
and its quantity and `PostTransaction` mints a `PriceSource.TRANSACTION` rate for
the day from the two; give it only a value and the leg is valued from the book's
price history — the rate effective on the post date, or the most recent one
before it (logged as a warning). A foreign leg with neither a quantity nor any
usable price is refused: there is nothing to value it with. An importer that
already carries its source's prices turns all of this off
(`price_foreign_legs=False`).

**Signs are stored raw,** KMyMoney-style: a credit card you owe 500 on has a
balance of −500, income of 3000 is stored as −3000. `AccountClass.report_sign`
flips it for display. This means `sum(all balances) == 0` always holds, which is
a cheap and very effective integrity check.

**A command is one revision,** however many rows it touches. Merging a payee
across 400 transactions is a single sync step, not 400 — and `Book.pull()`
delivers it as one `RevisionBatch`, never split across pages. `max_rows` is a
*soft* cap for exactly that reason: half a merge is a state the server was never
in.

**Creating a book is a command too** (`InitialiseBook`, revision 1). Otherwise
the base currency would exist on the server but be invisible to a replica
pulling from revision 0.

**Commodity snapshots are references.** Entities embed a `Commodity` object for
convenience, but the registry is authoritative. `minor_unit_factor` is immutable
by design, so arithmetic can never be reinterpreted; display fields can change,
and every amount the book hands out is re-bound to the current snapshot. The
Phase 2 wire format must carry `commodity_id`, not a nested copy.

**An account's commodity can only be changed while it has no postings.** After
that, changing it would silently reinterpret history (1,000 JPY becoming
1,000 EUR), so `SetAccountCommodity` refuses and points at the safe route:
a new account plus `MergeAccounts`, or a transfer.

**No-op commands don't burn a revision.** Setting a field to the value it
already has returns the current revision and records nothing, so replicas don't
re-sync for nothing.

**Reports are reproducible.** They read no clock and pick no default price.
Supply `knowledge_time` in the policy and you get the numbers the report would
have shown on that date, even if corrected quotes have arrived since. Every
report hands back the `Conversion` objects it used.

## Not here yet

investment lots and cost basis (`Holding.cost`
is the naive value-column sum) · budgets · loan amortisation · other import
formats (OFX, QIF) · attachments · a dedicated tag-management page (tags are
created and attached from the transaction form, and searched on; renaming or
retiring one is an API call away but has no screen yet) · the desktop app's
account, payee and commodity management (the web app has them under
**Settings**; see [The web frontend](#the-web-frontend)).

Two performance items are also deliberate non-goals for now:

**Running-balance snapshots.** Would make an as-of-today balance O(1) instead of
O(history). Worth doing when a real book gets big enough to notice; not worth the
invalidation complexity before then.

**A register over sub-accounts in several commodities** shows a running balance
all the same: a *valuation* in the parent account's commodity, every leg
translated at one rate -- the window's end date, or today -- exactly as the
income statement translates a period. The response marks it `translated`, lists
the rates used, and names any commodity it could not price, at which point the
running balance stops rather than pretends. One rate rather than each leg's own
date keeps the figure reproducible and keeps paging cheap: opening, closing and
the balance at a cursor stay aggregates times a rate.

**Value-column aggregation pushdown.** `value_balance` still goes through the
register. It is used by one report over a handful of accounts, so it has not
earned a second covering index yet.

And from the importer: **per-point rounding corrections** (see above), and
reading KMyMoney's SQL storage backend rather than only `.kmy` files.

## Quotes

```bash
python -m pymoney.quotes --list-providers
python -m pymoney.quotes --url ledger.sqlite                 # what is new since last time
python -m pymoney.quotes --url ledger.sqlite --start 2020-01-01   # backfill
```

A commodity whose `quote_source` names a provider gets priced automatically.
Two providers ship: **`ecb`** for currencies (the European Central Bank's daily
reference rates, published as decimal text and therefore stored exactly; always
against EUR, so a book kept in another currency needs a EUR commodity and the
valuation engine triangulates) and **`yahoo`** for securities (the commodity's
code is the Yahoo symbol; Yahoo hands out binary floats, which are rounded to
the precision Yahoo itself declares for the instrument and stored as that
decimal). A third kind is yours: subclass `QuoteProvider` -- one `fetch`
method, a page of code -- and register it through the
`pymoney.quote_providers` entry point or `PYMONEY_QUOTE_PLUGINS=module:Class`.

Fetched prices are ordinary prices: `source` is the provider's name, `as_of`
is the instant of the fetch (decision #9 -- knowledge time *is* `fetched_at`),
and the rate is the exact rational the provider built from its text. A quote
identical to one already stored for that day and source is skipped, so runs
are idempotent; a different one is a new row, never an edit, so a report pinned
to an earlier `knowledge_time` still shows what was known then. A price the
user entered by hand is never touched, and the default valuation policy ranks
it above any fetched quote for the same day.

The API server runs the service on a schedule (`PYMONEY_QUOTE_INTERVAL_MINUTES`,
default 360, `0` to disable) in a thread that takes each book's lock exactly as
a request would -- but only to *plan* and to *store*, never across the network
calls: a provider may take a minute or not answer at all, and the book is free
while it is being asked. `POST /books/{id}/quotes/fetch` runs the same three
steps on demand with an optional backfill range, `GET /books/{id}/quotes/last` shows the last report,
and `GET /quote-providers` lists what `quote_source` may be set to. The command
line is for cron and one-off backfills **when the server is not running**
against the same database, since the server's caches assume they are the only
writer.

## Languages

The web app, the desktop app and the server's emails are available in English
and Dutch. One catalogue serves all three: `server/src/pymoney/i18n/nl.json`, keyed by
the English source string (so code reads as English and English needs no
catalogue), copied into the frontend by `scripts/i18n.py` the way
`server/docs/openapi.json` is. `make i18n-check` -- and CI -- fails when a string the
code asks for (`t("…")` in the web app, `_("…")` in the desktop,
`translate("…")` on the server) has no Dutch translation, or the catalogue has
a stale key, or the frontend's copy is behind. Placeholders (`Delete {name}?`)
are substituted after the lookup, so a translator can move them.

The language is **stored with the user** (`users.locale`, `PATCH /auth/me`,
and a `locale` on registration so the activation email is already right).
Before login the web app uses this browser's last choice or the browser's
language; once the user is known, the account's setting wins and any change is
saved to it -- so the desktop and the web app agree. Numbers follow the locale
too (`1.234,56` in Dutch), through `Intl` on the web and the locale-aware
formatter on the desktop. Adding a language is a new JSON file beside `nl.json`
plus its code in `pymoney.i18n.SUPPORTED` and the frontend's `LOCALES`.

## The desktop app

```bash
make -C desktop install              # installs ../server too, into the shared .venv
python -m pymoney_desktop            # or: make -C desktop run
```

A PySide6 client with the web app's core functionality -- books, the account
tree with balances, registers (with the reconciliation column), posting, editing
and deleting transactions with payees created on the spot, search (Ctrl+F),
reconciliation (**Mark** for one row, **Reconcile…** to tick a whole statement
and mark it in one command; FROZEN offered to owners only, as on the web, and
queued and replayed like every other write when offline), the due banner and **Scheduled…** for recurring transactions, **Import CSV…** for a
bank statement (the same stacked wizard as the web app; like creating a book it
needs the connection, since the duplicate and transfer checks run against the
book the server holds), and under **Reports** the balance sheet, income
statement and holdings with the same options as on the web (valuation date or
period presets, reporting currency, closed accounts, strict mode, the rates used
and what could not be valued, CSV export with the same columns) -- and one thing
the web app cannot have: a mode that works without the server. Offline, the
reports run the very same view code against the local replica, so the numbers
are the ones the server would give.

**One interface, two implementations.** The UI talks to
`pymoney_desktop.LedgerClient` and nothing else. Its wire types are the API's
own pydantic schemas, so the desktop and the web frontend speak the identical
contract.

- `RemoteClient` holds nothing but a read cache. Every write is an HTTP request
  to the same API the web frontend uses, and fails the moment the network does.
  The cache is kept honest by the `X-PyMoney-Revision` header every response
  carries: a newer revision -- this client's write or someone else's -- drops
  the book's entries, so no timer is needed.
- `OfflineClient` keeps a **local SQLite replica** of each book (a `Book` over
  the same `SqlStore` the server runs, fed by `GET /changes` batches), applies
  every write to it at once, and queues the write in an **outbox**. Reads never
  need the server. A write is validated locally by the very same domain rules
  the server enforces -- an unbalanced entry is refused in the living room, not
  on the train back -- and ids are chosen by the client (hence the optional
  `id` on the create endpoints), so the server's row and the local one are one
  row when the command is replayed.

**Replay, and what a conflict looks like.** On reconnect the outbox replays in
order, each command with the idempotency key it was queued with, so a dropped
acknowledgement cannot post twice. A 2xx removes the entry. A 4xx is a
*rejection*: the server, which is authoritative, disagrees with what the replica
accepted -- typically because someone else changed the book in between (the
account was deleted, the transaction reconciled). The entry is removed and
recorded, the replica is **rebuilt from the server**, and the remaining entries
are applied to it again so the screen keeps showing what is still pending. The
status bar says "2 rejected…" and the dialog behind it says what and why.
Nothing is merged, ever. A 5xx or no connection stops the replay; the entries
stay and are tried next time. A pull follows every replay.

The login dialog has the choice ("Keep a local copy and work offline"); the
replica lives in the platform's app-data directory. `desktop/tests/test_desktop_clients.py`
runs both clients against the real API in-process, with a transport that can be
unplugged, through the whole story: work offline, replay, dropped ack,
rejection and rebuild, cold start offline. `desktop/tests/test_desktop_ui.py` drives the
real window offscreen (`pytest-qt`) through posting, editing, deleting,
reconnecting and a rejection.

### Packaging: PyInstaller

`make -C desktop build` runs `desktop/packaging/pymoney-desktop.spec` and produces
`dist/PyMoney/`, a one-folder build.

## The web frontend

```bash
make -C server serve              # the API
make -C server seed-demo          # optional demo book
make -C frontend install dev      # http://localhost:5173
```

`frontend/` is an npm workspace with two packages. `@pymoney/core` is the
platform-agnostic client -- types generated from `docs/openapi.json`, the HTTP
client with cookie+CSRF and bearer-token modes, exact (BigInt) money formatting,
period presets and React Query hooks -- with no DOM dependency, so the mobile
and desktop apps to come reuse it whole. `@pymoney/web` is the React web app
built on it: the three reports (balance sheet, income statement and holdings,
each with currency translation, a traceable list of the rates used, CSV export
and print styles), the **Charts** tab (income and expenses by category as pies,
net worth over time as a line, holdings by position as a pie, and the
spreadsheet view of income and expenses per month, quarter or year -- all drawn
from the same report payloads, in plain SVG, with a legend or table a reader
without the picture can use), drill-down from any report row to the account's register and
from any register row to the transaction, and -- by role -- posting, editing and
deleting transactions with payees created on the spot, **Import CSV** on any
register (the four-step wizard of [Importing bank statements](#importing-bank-statements-csv):
file, columns, review, report), and under **Settings**:
payees (add, edit, merge duplicates, delete the unused), scheduled transactions, commodities and prices,
and fetching quotes (editors), the chart of accounts and sharing (owners), and
the book's **History** -- who changed what, and when; a transaction page shows
its own. Posting is quick: choosing a payee fills its default account and the
amount of its last transaction into blank fields. Reconciling takes the
statement's ending balance and says live whether the ticked entries match it. `make -C frontend check` is what CI runs; it fails if the
generated types no longer match the committed OpenAPI document. `make -C frontend e2e` runs
the Playwright walkthroughs -- a real browser against the real API and app,
from registering to posting and deleting a transaction; see
[frontend/README.md](frontend/README.md#end-to-end-tests).

See [frontend/README.md](frontend/README.md).

## What the web and desktop clients have to honour

- The store is the **only writer** (decision #14). Its caches assume it. A second
  process writing the same book needs either a different design or `reload()` on
  every read, and the API server must therefore own its database.
- A command is one database transaction and one revision. `book.bulk()` widens
  the transaction but not the revision granularity — and it trades per-command
  atomicity for throughput, which is right for an importer and wrong for a UI.
- The wire format must carry `commodity_id`, not a nested commodity. Entities
  embed a `Commodity` object for convenience; serialising the copy would hand
  replicas a snapshot that goes stale.
- `SqlStore.put` takes `created=` from the command rather than asking the
  database. It is not a hint to be verified: if it is wrong, the constraint
  violation is the correct outcome.
