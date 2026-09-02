"""Performance characteristics, asserted deterministically.

Wall-clock assertions are flaky on shared CI runners, so this module does not
make any. Instead it pins the two things that actually cause a ledger to get
slow, both of which are exact and reproducible:

1. **Statement counts.** An N+1 query is the classic way a fast page becomes a
   slow one. Posting a transaction, reading a register or computing a balance
   each have a fixed budget here, and exceeding it fails the build.
2. **Query plans.** An index that stops being used is invisible until someone
   complains. These tests read ``EXPLAIN`` and insist the planner is using the
   index the schema exists to provide.

``benchmarks/bench_store.py`` has the numbers; this file keeps them true.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, text

from pymoney.core import AccountType, Commodity, LegSpec, ManualClock
from pymoney.store import create_engine, create_schema, open_book

HOT_INDEX = "ix_splits_book_id_account_id_post_date"


# Transaction control is not a query. We emit BEGIN ourselves on SQLite (pysqlite
# will not do it before a SAVEPOINT), so it turns up here and would otherwise
# pollute every budget.
_CONTROL = ("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE")


class StatementCounter:
    """Counts the SQL an operation issues, separating queries from plumbing."""

    def __init__(self, engine) -> None:
        self.statements: list[str] = []
        self.control: list[str] = []
        self.commits = 0
        event.listen(engine, "before_cursor_execute", self._on_execute)
        event.listen(engine, "commit", self._on_commit)
        self._engine = engine

    def _on_execute(self, conn, cursor, statement, params, context, many) -> None:
        if statement.lstrip().upper().startswith(_CONTROL):
            self.control.append(statement)
        else:
            self.statements.append(statement)

    def _on_commit(self, conn) -> None:
        self.commits += 1

    @property
    def savepoints(self) -> int:
        return sum(s.lstrip().upper().startswith("SAVEPOINT") for s in self.control)

    def reset(self) -> None:
        self.statements.clear()
        self.control.clear()
        self.commits = 0

    def __len__(self) -> int:
        return len(self.statements)

    def describe(self) -> str:
        return "\n".join(f"  {s.splitlines()[0][:100]}" for s in self.statements)

    def close(self) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on_execute)
        event.remove(self._engine, "commit", self._on_commit)


@pytest.fixture
def ledger(tmp_path: Path):
    """A small file-backed ledger with a few hundred postings."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'perf.sqlite'}")
    create_schema(engine)
    book = open_book(
        engine,
        name="Perf",
        base_currency=Commodity.currency("EUR"),
        clock=ManualClock(),
    )
    assets = book.add_account("Assets", AccountType.ASSET, placeholder=True)
    banks = [
        book.add_account(f"Bank {i}", AccountType.CHECKING, parent=assets)
        for i in range(4)
    ]
    categories = [book.add_account(f"Cat {i}", AccountType.EXPENSE) for i in range(8)]
    with book.bulk():
        for i in range(400):
            book.post(
                date(2026, 1, 1) + timedelta(days=i // 4),
                [
                    LegSpec.of(banks[i % 4].id, f"-{(i % 97) + 1}.00"),
                    LegSpec.of(categories[i % 8].id),
                ],
            )
    counter = StatementCounter(engine)
    yield book, banks, categories, counter
    counter.close()
    book.close()
    engine.dispose()


def plan_for(book, sql: str) -> str:
    with book.store.engine.connect() as conn:
        rows = conn.execute(text(f"EXPLAIN QUERY PLAN {sql}")).all()
    return " | ".join(str(r[-1]) for r in rows)


class TestStatementBudgets:
    """A fixed number of queries per operation, whatever the ledger's size."""

    def test_posting_a_transaction(self, ledger):
        book, banks, categories, counter = ledger
        counter.reset()
        book.post(
            date(2026, 6, 1),
            [LegSpec.of(banks[0].id, "-12.34"), LegSpec.of(categories[0].id)],
        )
        # id check, insert transaction, insert splits, insert changes, update book
        assert len(counter) <= 5, f"too many statements:\n{counter.describe()}"
        assert counter.commits == 1

    def test_a_plain_command_needs_no_savepoint(self, ledger):
        """Outside a batch the transaction itself is the unit of isolation."""
        book, banks, categories, counter = ledger
        counter.reset()
        book.post(
            date(2026, 6, 2),
            [LegSpec.of(banks[0].id, "-1.00"), LegSpec.of(categories[0].id)],
        )
        assert counter.savepoints == 0

    def test_bulk_mode_isolates_each_command_with_a_savepoint(self, ledger):
        """The price of letting one bad row be skipped instead of fatal."""
        book, banks, categories, counter = ledger
        counter.reset()
        with book.bulk():
            for i in range(5):
                book.post(
                    date(2026, 8, 1) + timedelta(days=i),
                    [
                        LegSpec.of(banks[0].id, f"-{i + 1}.00"),
                        LegSpec.of(categories[0].id),
                    ],
                )
        assert counter.savepoints == 5
        assert counter.commits == 1

    def test_bulk_mode_commits_once(self, ledger):
        """An importer must not pay a commit per row."""
        book, banks, categories, counter = ledger
        counter.reset()
        with book.bulk():
            for i in range(20):
                book.post(
                    date(2026, 7, 1) + timedelta(days=i),
                    [
                        LegSpec.of(banks[0].id, f"-{i + 1}.00"),
                        LegSpec.of(categories[0].id),
                    ],
                )
        assert counter.commits == 1
        # ...and no per-command book-row update either.
        assert sum("UPDATE books" in s for s in counter.statements) == 1

    def test_a_balance_is_one_query(self, ledger):
        book, banks, _, counter = ledger
        counter.reset()
        book.balance(banks[0].id)
        assert len(counter) == 1, counter.describe()
        assert "sum" in counter.statements[0].lower()

    def test_every_balance_is_still_one_query(self, ledger):
        """The whole-book aggregate must not become one query per account."""
        book, _, _, counter = ledger
        counter.reset()
        balances = book.balances_by_account()
        assert len(balances) > 10
        assert len(counter) == 1, counter.describe()

    def test_a_register_is_two_queries(self, ledger):
        """One for the transactions, one for their splits. Never N+1."""
        book, banks, _, counter = ledger
        counter.reset()
        rows = book.register(banks[0].id)
        assert len(rows) == 100
        assert len(counter) == 2, counter.describe()

    def test_a_windowed_register_is_also_two_queries(self, ledger):
        book, banks, _, counter = ledger
        counter.reset()
        book.register(banks[0].id, start=date(2026, 2, 1), end=date(2026, 2, 28))
        assert len(counter) == 2, counter.describe()

    def test_a_balance_sheet_does_not_scale_with_accounts(self, ledger):
        """Reports are built from pushed-down aggregates, not per-row lookups."""
        from pymoney.core import balance_sheet

        book, _, _, counter = ledger
        counter.reset()
        balance_sheet(book, book.default_policy(date(2026, 12, 31)))
        assert len(counter) <= 3, counter.describe()

    def test_loading_a_transaction_is_two_queries(self, ledger):
        book, _, _, counter = ledger
        transaction_id = book.store.transaction_ids()[0]
        counter.reset()
        book.transaction(transaction_id)
        assert len(counter) == 2, counter.describe()

    def test_a_sync_page_is_bounded(self, ledger):
        """Paging must not drag the whole change log back each time."""
        book, _, _, counter = ledger
        counter.reset()
        page = book.pull(0, max_revisions=5)
        assert len(page) == 5
        assert len(counter) <= 2 + 5 * 2, counter.describe()

    def test_cached_tables_cost_nothing_to_read(self, ledger):
        """Commodities, accounts and payees are cached; reading them is free."""
        book, _, _, counter = ledger
        counter.reset()
        book.accounts()
        book.commodities()
        book.payees()
        book.path(book.accounts()[0])
        book.roots()
        assert len(counter) == 0, counter.describe()


class TestQueryPlans:
    """The planner must actually use the indexes the schema declares."""

    def test_the_balance_aggregate_uses_the_covering_index(self, ledger):
        """Covering matters: without `quantity_minor` in the index, SQLite walks
        the index and then fetches every row, which loses to a table scan."""
        book, *_ = ledger
        plan = plan_for(
            book,
            "SELECT account_id, sum(quantity_minor) FROM splits "
            "WHERE book_id = x'00' GROUP BY account_id",
        )
        assert "COVERING INDEX" in plan, plan
        assert HOT_INDEX in plan, plan

    def test_a_single_account_balance_uses_the_index(self, ledger):
        book, *_ = ledger
        plan = plan_for(
            book,
            "SELECT account_id, sum(quantity_minor) FROM splits "
            "WHERE book_id = x'00' AND account_id = x'00' "
            "AND post_date <= '2026-12-31' GROUP BY account_id",
        )
        assert HOT_INDEX in plan, plan
        assert "SCAN splits" not in plan, plan

    def test_the_register_window_uses_the_index(self, ledger):
        book, *_ = ledger
        plan = plan_for(
            book,
            "SELECT transaction_id FROM splits WHERE book_id = x'00' "
            "AND account_id = x'00' AND post_date >= '2026-02-01' "
            "AND post_date <= '2026-02-28'",
        )
        assert HOT_INDEX in plan, plan

    def test_the_change_log_scan_uses_the_revision_index(self, ledger):
        book, *_ = ledger
        plan = plan_for(
            book,
            "SELECT revision FROM changes WHERE book_id = x'00' AND revision > 5 "
            "ORDER BY revision, seq",
        )
        assert "ix_changes_book_id_revision_seq" in plan, plan
        # Ordered by the index, so no sort step.
        assert "TEMP B-TREE" not in plan, plan

    def test_transactions_by_date_use_the_date_index(self, ledger):
        book, *_ = ledger
        plan = plan_for(
            book,
            "SELECT id FROM transactions WHERE book_id = x'00' "
            "AND post_date >= '2026-02-01' ORDER BY post_date, id",
        )
        assert "ix_transactions_book_id_post_date" in plan, plan

    def test_price_lookup_uses_the_pair_index(self, ledger):
        book, *_ = ledger
        plan = plan_for(
            book,
            "SELECT num, den FROM prices WHERE book_id = x'00' "
            "AND base_id = x'00' AND quote_id = x'00' "
            "ORDER BY effective_date DESC",
        )
        assert "ix_prices_book_id_base_id_quote_id_effective_date" in plan, plan

    def test_splits_of_a_transaction_use_an_index(self, ledger):
        book, *_ = ledger
        plan = plan_for(book, "SELECT * FROM splits WHERE transaction_id = x'00'")
        assert "ix_splits_transaction_id" in plan, plan
        assert "SCAN splits" not in plan, plan

    def test_imported_id_lookup_is_indexed(self, ledger):
        """Import dedup is per row, so it must not scan."""
        book, *_ = ledger
        plan = plan_for(
            book,
            "SELECT id FROM transactions WHERE book_id = x'00' AND imported_id = 'X'",
        )
        assert "SCAN transactions" not in plan, plan


class TestTenantIsolationIsIndexed:
    def test_every_large_table_is_indexed_by_book(self):
        """Decision #7: multi-tenant from day one. A tenant filter that is not
        indexed is a tenant filter that will not survive the second tenant."""
        from pymoney.store import schema

        for table in (schema.splits, schema.transactions, schema.changes):
            leading = {
                next(iter(index.columns)).name
                for index in table.indexes
                if len(index.columns)
            }
            assert "book_id" in leading, f"{table.name} has no book-leading index"


class TestPagesCostAPage:
    """A page of a register or a transaction list must not hydrate the ledger."""

    def test_register_first_page_without_a_window(self, ledger):
        """The web app's default view: no dates, first page. It used to load
        every transaction of the account and slice in Python."""
        book, banks, _categories, counter = ledger
        counter.reset()
        rows = book.register(banks[0].id, limit=10)
        assert len(rows) == 10
        fetched = [s for s in counter.statements if "FROM transactions" in s]
        assert all("LIMIT" in s for s in fetched), counter.describe()
        assert len(counter) <= 2, counter.describe()

    def test_register_page_after_a_cursor(self, ledger):
        book, banks, _categories, counter = ledger
        first = book.register(banks[0].id, limit=10)
        last = first[-1]
        counter.reset()
        second = book.register(
            banks[0].id,
            after=(last.date, last.transaction.id, last.split.id),
            limit=10,
        )
        assert len(second) == 10
        assert second[0].transaction.date >= last.date
        assert len(counter) <= 2, counter.describe()
        assert all("LIMIT" in s for s in counter.statements if "FROM transactions" in s)

    def test_transaction_list_page(self, ledger):
        book, _banks, _categories, counter = ledger
        counter.reset()
        page = book.transactions(limit=20)
        assert len(page) == 20
        assert all("LIMIT" in s for s in counter.statements if "FROM transactions" in s)
        cursor = (page[-1].date, page[-1].id)
        counter.reset()
        following = book.transactions(after=cursor, limit=20)
        assert len(following) == 20
        assert following[0].id not in {t.id for t in page}
        assert all("LIMIT" in s for s in counter.statements if "FROM transactions" in s)

    def test_a_sync_page_reads_only_its_slice_of_the_log(self, ledger):
        """With only a row cap (the API default), pull() used to read the whole
        change log on every page and look each transaction up one by one."""
        book, _banks, _categories, counter = ledger
        counter.reset()
        page = book.pull(0, max_rows=50)
        # The cap is on log rows; a command touching one entity twice collapses.
        assert 40 <= page.rows < 60
        log_reads = [s for s in counter.statements if "FROM changes" in s]
        # The first read is capped; the second only completes its last revision.
        assert log_reads and "LIMIT" in log_reads[0], counter.describe()
        assert all("revision = ?" in s for s in log_reads[1:]), counter.describe()
        # One read of the log (plus completing its last revision), one for the
        # transactions, one for their splits; not one per change.
        assert len(counter) <= 5, counter.describe()
