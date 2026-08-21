# PyMoney — core domain library

Double-entry accounting that runs everywhere. This repository currently contains
**Phase 1 only**: the pure-Python domain layer. No database, no network, no UI.

```
src/pymoney/core/
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
    commands.py     the intents clients send
    book.py         the aggregate: one tenant's ledger
    reports.py      balance sheet, income statement, holdings
```

## Status

| | |
|---|---|
| Tests | 506 passing |
| Coverage | 100% statements, 100% branches |
| Type checking | `mypy` clean |
| Lint | `ruff` clean |
| Dependencies | none (stdlib only) |

## Development

```bash
make install        # venv + editable install with dev extras
make check          # exactly what CI runs: lint, types, tests
make test           # fast loop (50 Hypothesis examples per property)
make test-thorough  # 5000 examples; use this when touching rounding
make help           # everything else
```

Optionally `pip install pre-commit && pre-commit install` for the fast checks on
commit.

Three Hypothesis profiles, because the property tests are the only real check on
the rounding invariants: `dev` (50 examples, the default), `ci` (500,
derandomised so a CI failure is reproducible) and `thorough` (5000).

### Containers

Nothing to deploy yet — the core is a library — but the infrastructure is in
place and exercised by CI:

```bash
cp .env.example .env            # then change the password
docker compose up -d            # PostgreSQL only; that is all that exists
make image-test                 # build the image and run the suite inside it
```

`compose.yaml` has three services. `db` is real and needed from Phase 2, where
the store layer must pass this same suite against PostgreSQL as well as SQLite;
its init script creates a separate `pymoney_test` database so tests can never
touch development data. `tests` runs the suite in the shipped image. `api` is
wired but **not implemented** — it exists so the environment, dependency
ordering and port binding are settled before the code lands; its command is
currently the component inventory, which honestly reports `pymoney.api` as
missing.

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
is the naive value-column sum) · budgets · loan amortisation · importers (OFX,
CSV, KMyMoney XML) · attachments · tags. Each is a separate, bounded piece of
work on top of this layer.

## What Phase 2 has to honour

The SQLAlchemy layer must reproduce this module's behaviour test for test. The
things most likely to be got wrong:

- `Fraction` → two integer columns, not `NUMERIC`. Prices are exact.
- `Amount` → integer minor units plus a commodity reference. Never a float.
- Revisions are assigned by the book, monotonic per `book_id`. In SQL this is a
  per-book counter bumped in the same transaction as the write.
- A command must be one database transaction. The undo log in `Book` exists only
  because there is no database to roll back yet.
- Indexes to mirror the in-memory ones: `(book_id, account_id, date)` on splits,
  `(book_id, base, quote, effective_date, as_of)` on prices,
  `(book_id, revision)` on the change log.
