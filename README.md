# PyMoney

Double-entry accounting that runs everywhere. So far: the domain model,
persistence over SQLite and PostgreSQL, a KMyMoney importer, the HTTP API, and a
read-only web frontend (reports, registers, transactions).

```
src/pymoney/core/               the domain. pure stdlib, no dependencies
    ids.py          UUIDv7, monotonic
    clock.py        injectable clock (UTC instants)
    entity.py       id / book_id / created_at / updated_at / revision
    errors.py       exception hierarchy
    rounding.py     exact rounding modes + the zero-sum allocator
    commodity.py    currencies and securities, uniformly
    amount.py       Amount (booked) vs RationalAmount (exact) vs Balances
    account.py      typed, hierarchical accounts
    payee.py        payees, with merge support
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
    reports.py      balance sheet, income statement, holdings

src/pymoney/store/              persistence. SQLAlchemy Core, no ORM mapping
    types.py        portable UUID / JSON / timezone-aware timestamp columns
    schema.py       tables and indexes
    mapper.py       domain objects <-> rows
    sql.py          SqlStore: the Store contract over a database
    engine.py       engine creation, SQLite pragmas, open_book
    migrate.py      apply the migrations from an installed package (no alembic.ini)
    grants.py       who may see a book; the command line for books made outside the API
    migrations/     Alembic

src/pymoney/importers/kmymoney/   migration from KMyMoney
    model.py        the file's own structures, unintepreted
    parser.py       .kmy reading: gzip, XML, streamed transactions
    importer.py     model -> book, including the rounding reconciliation
    report.py       what was done, approximated, and skipped
    __main__.py     the command line

src/pymoney/api/                  the HTTP API
    schemas.py      wire models, deliberately not the domain objects
    security.py     password hashing, session tokens, idempotency keys
    registry.py     one open Book per book id, one lock per book
    deps.py         who is calling, what they may do, which book
    errors.py       domain errors -> status codes, in one place
    routers/        one module per resource
    app.py          the application factory
```

## Status

| | |
|---|---|
| Tests | 3,000+ — the behavioural suite runs against every storage backend |
| Backends | memory, SQLite (memory and file), PostgreSQL — all run the full suite |
| Coverage | 99% statements and branches |
| Type checking | `mypy` clean, no `type: ignore` in the domain |
| Lint | `ruff` clean |
| Dependencies | none for `pymoney.core`; SQLAlchemy + Alembic for `pymoney.store` |

## Development

```bash
make install        # venv + editable install with dev extras
make check          # what CI runs: lint, types, tests (no PostgreSQL)
make test           # fast loop: memory + sqlite
make test-all       # every backend, PostgreSQL included
make bench-scaling  # does the ledger stay fast as it grows?
make test-thorough  # 5000 Hypothesis examples; when touching rounding
make help           # everything else
```

Optionally `pip install pre-commit && pre-commit install` for the fast checks on
commit.

Three Hypothesis profiles, because the property tests are the only real check on
the rounding invariants: `dev` (50 examples, the default), `ci` (500,
derandomised so a CI failure is reproducible) and `thorough` (5000).

### Refactoring note

`book.py` was 1,600 lines doing four jobs. It is now 880 and does three, all of
which belong to an aggregate: enforce the invariants, dispatch commands, answer
reads. The rest moved out — storage to `core/store.py`, the 25 command handlers
to `core/handlers/` (one module per aggregate), the pull protocol into
`core/sync.py`. Nothing about the public API changed, which the unchanged test
suite is the evidence for.

### Containers

```bash
cp .env.example .env                                        # then change the password
docker compose up -d                                        # PostgreSQL
docker compose --profile api run --rm api python -m pymoney.store.migrate
docker compose --profile api up -d                          # the API on 127.0.0.1:8000
make image-test                                             # run the suite inside the image
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

### CI

`.github/workflows/ci.yml` runs lint + format, `mypy`, the suite on Python 3.12
and 3.13 with the `ci` Hypothesis profile and the coverage gate, and a container
job that builds both image targets, runs the suite inside the test image and
validates `compose.yaml`. A single aggregate `CI` check is the one to require in
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
make migrate                            # python -m pymoney.store.migrate, $PYMONEY_DATABASE_URL
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

The guarantee: the same accounts exist, and **every balance matches the figure
KMyMoney displayed, to the minor unit.** The importer verifies that itself, on
your data, and says so in the report.

Reads gzipped or plain XML; refuses GPG-encrypted files with the `gpg --decrypt`
command line rather than asking for your passphrase. Transactions are streamed,
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

Scheduled transactions, budgets, saved reports, cost centres, tags, online
banking jobs, and per-account settings like `PreferredAccount`. None of it is
dropped quietly — the report counts every one. Also reported: KMyMoney types we
narrow (a certificate of deposit becomes a savings account), colons stripped from
account names (we use them as the path separator), duplicate payee names
disambiguated, and any transaction that could not be stored at all.

An import is one unit of work, so a failure leaves no half-imported book — but a
single malformed transaction is skipped and reported rather than fatal, which is
what `book.bulk()`'s per-command savepoints are for. A second import of the same
file is refused up front, before anything is created, on the strength of the
`imported_id` prefix.

## The HTTP API

```bash
make serve              # http://127.0.0.1:8000/docs
make openapi            # regenerate docs/openapi.json
```

`docs/openapi.json` is committed and CI fails if it drifts from the code, so a
client generated from it is never wrong about the server.

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

Per-book grants are `viewer` / `editor` / `owner`, keyed on a *subject string*
rather than a user id — which is what lets OIDC slot in later and map a token's
`sub` claim onto the same grants. A book you hold no grant on is a 404, not a
403: whether it exists is itself information.

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
(commands take single-digit milliseconds), and the place to revisit it is a read
replica, not a weaker lock.

One consequence worth knowing: **an in-memory SQLite database cannot back the
API**, because it is a single connection and the API needs two at once.
`create_app` refuses it with that explanation rather than failing later with a
baffling "cannot start a transaction within a transaction".

## Performance

`benchmarks/bench_store.py` measures it; `tests/test_performance.py` keeps it
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
tests in `tests/test_posting.py` enforce them for arbitrary inputs:

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

## Deliberately not here yet

Scheduled/recurring transactions · investment lots and cost basis (`Holding.cost`
is the naive value-column sum) · budgets · loan amortisation · importers other
than KMyMoney (OFX, CSV) · attachments · tags · any UI beyond the reports and
the read-only register (see [The web frontend](#the-web-frontend)).

Two performance items are also deliberate non-goals for now:

**Running-balance snapshots.** Would make an as-of-today balance O(1) instead of
O(history). Worth doing when a real book gets big enough to notice; not worth the
invalidation complexity before then.

**Value-column aggregation pushdown.** `value_balance` still goes through the
register. It is used by one report over a handful of accounts, so it has not
earned a second covering index yet.

And from the importer: **per-point rounding corrections** (see above), and
reading KMyMoney's SQL storage backend rather than only `.kmy` files.

## The web frontend

```bash
make serve          # the API
make seed-demo      # optional demo book
make web-install && make web-dev    # http://localhost:5173
```

`frontend/` is an npm workspace with two packages. `@pymoney/core` is the
platform-agnostic client -- types generated from `docs/openapi.json`, the HTTP
client with cookie+CSRF and bearer-token modes, exact (BigInt) money formatting,
period presets and React Query hooks -- with no DOM dependency, so the mobile
and desktop apps to come reuse it whole. `@pymoney/web` is the React web app
built on it, currently the three reports: balance sheet, income statement and
holdings, each with currency translation, a traceable list of the rates used,
CSV export and print styles -- plus read-only drill-down from any report row to
the account's register and from any register row to the transaction. `make web-check` is what CI runs; it fails if the
generated types no longer match the committed OpenAPI document.

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
