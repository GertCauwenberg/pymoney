# PyMoney design notes

Why the code is shaped the way it is. Contributor commands and gotchas are in
[AGENTS.md](../AGENTS.md); the file and module maps are in
[CODEMAPS](CODEMAPS/INDEX.md).

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

## The rounding guarantee

`book_legs` promises three things, enforced by the property tests in
`server/tests/test_posting.py` for arbitrary inputs:

1. booked values sum to exactly zero;
2. no leg is distorted by more than one minor unit;
3. a leg whose exact value is already representable is **never** adjusted — the
   rounding error always lands on a leg that was inexact to begin with.

Point 3 is the one users notice: pay a 100.00 EUR invoice from a USD account and
the 100.00 stays 100.00.

## Quantity vs. value

Every split carries both. `quantity` is in the account's own commodity (10
shares, −1500 EUR); `value` is in the transaction currency and is the column
that must balance. For single-currency entries they are identical and
`Transaction.validate` enforces that.

## A stock split is the one transaction with a single leg

"Values sum to zero" is the real invariant; "at least two legs" is its usual
*consequence*. A four-for-one split costs and earns nothing — it just multiplies
the share count — so it is one leg, a change in `quantity`, a `value` of zero,
`action` set to `SPLIT`. `RecordStockSplit` takes a *ratio* (new-for-old: `2`,
or `Fraction(1, 3)` for a reverse split), books the delta against the holding as
of the split date, and — where a price is already on file — records a
`PriceSource.SPLIT` one adjusted by the same ratio. It is recorded and deleted,
not edited.

## Posting picks the currency; editing keeps it

A new transaction's currency is chosen on the form (it defaults to the book's).
Once the transaction exists the control is read-only, because its splits'
`value`s are already denominated in it. Give a leg in another commodity both its
value and its quantity and `PostTransaction` mints a `PriceSource.TRANSACTION`
rate for the day; give it only a value and the leg is valued from the book's
price history. A foreign leg with neither a quantity nor any usable price is
refused.

## Signs are stored raw

KMyMoney-style: a credit card you owe 500 on has a balance of −500, income of
3000 is stored as −3000. `AccountClass.report_sign` flips it for display. This
means `sum(all balances) == 0` always holds, which is a cheap and very effective
integrity check.

## A command is one revision

However many rows it touches. Merging a payee across 400 transactions is a
single sync step, not 400 — and `Book.pull()` delivers it as one
`RevisionBatch`, never split across pages. `max_rows` is a *soft* cap for
exactly that reason. Creating a book is a command too (`InitialiseBook`,
revision 1). No-op commands don't burn a revision.

## Commodity snapshots are references

Entities embed a `Commodity` object for convenience, but the registry is
authoritative. `minor_unit_factor` is immutable by design. An account's
commodity can only be changed while it has no postings; `SetAccountCommodity`
otherwise refuses and points at a new account plus `MergeAccounts`, or a
transfer.

## Reports are reproducible

They read no clock and pick no default price. Supply `knowledge_time` in the
policy and you get the numbers the report would have shown on that date. Every
report hands back the `Conversion` objects it used.

## Persistence

The domain does not know the store exists. `SqlStore` implements
`pymoney.core.store.Store`, so the same `Book` runs over memory or over a
database. `pytest --stores=memory,sqlite,sqlite-file,postgres` runs the entire
behavioural suite against every backend.

Three decisions carry most of the weight:

**`post_date` is denormalised onto `splits`.** Every register, balance and
report asks "this account, up to this date", and with the date on the split that
is answered from one index without touching `transactions`. Splits are only
ever written as part of their transaction, so the copy cannot drift.

**The hot index is covering.** `ix_splits_book_id_account_id_post_date`
includes `quantity_minor`, so the aggregate
`SELECT account_id, SUM(quantity_minor) … GROUP BY account_id` is answered from
the index alone. Measured at 50k transactions: every balance 173 ms → 16 ms,
one account's balance 9.1 ms → 1.0 ms, for 0.6% more disk.

**Small tables are cached, large tables are queried.** Commodities, accounts,
payees and prices are tiny and read constantly, so the store holds them in
memory; transactions, splits and the change log are never cached. This assumes
the store is the only writer — decision #14 — and `reload()` exists for when
that has to be re-established.

## One writer per book

The API keeps one `Book` object per book id for the life of the process, with
one lock per book held across every request touching it (decision #14). The
store does **not** hold a database connection between commands: one is borrowed
from the pool for the duration of a unit of work and returned at once.

The cost is explicit: requests against the *same* book are serialised; different
books run concurrently. Commands take single-digit milliseconds.

## What the web and desktop clients have to honour

- The store is the **only writer** (decision #14). Its caches assume it. Run
  the API single-worker; it must own its database.
- A command is one database transaction and one revision. `book.bulk()` widens
  the transaction but not the revision granularity — and it trades per-command
  atomicity for throughput, which is right for an importer and wrong for a UI.
- The wire format must carry `commodity_id`, not a nested commodity. Serialising
  the embedded copy would hand replicas a snapshot that goes stale.
- **Amounts and rates are never JSON floats.** An amount crosses the wire as a
  string or an integer; a JSON float is rejected, not rounded.
- Every write accepts `Idempotency-Key`; every response carries
  `X-PyMoney-Revision`.

## Performance

`server/benchmarks/bench_store.py` measures it; `server/tests/test_performance.py`
pins statement counts (an N+1 fails the build) and query plans (an index that
stops being used fails the build). SQLite, file-backed, 135 accounts, in
milliseconds:

| ledger size | 1 balance | 1 month register | all balances | balance sheet | sync page | 1 txn by id |
|---|---|---|---|---|---|---|
| 25,000 | 0.6 | 6.4 | 8.4 | 12.0 | 1.0 | 0.4 |
| 50,000 | 0.9 | 6.1 | 14.7 | 20.8 | 1.0 | 0.4 |
| 100,000 | 1.8 | 6.7 | 28.5 | 39.8 | 1.0 | 0.4 |
| 200,000 | 4.1 | 10.2 | 71.3 | 112.8 | 1.2 | 0.4 |

A windowed register, a sync page and a transaction lookup are flat. A balance
and a balance sheet are linear — an as-of-today balance is a sum over all
history. Writes: 724 transactions/second one command at a time, 1,598/second
inside `book.bulk()`.

Deliberate non-goals: running-balance snapshots (would make an as-of-today
balance O(1)); value-column aggregation pushdown (`value_balance` still goes
through the register).

## Library tour

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
