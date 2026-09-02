"""Storage benchmark: how does the ledger behave as it grows?

    python benchmarks/bench_store.py --transactions 20000
    python benchmarks/bench_store.py --backend postgres --drop-indexes

The point is not a leaderboard. It is to answer two questions with numbers:

1. Are the hot operations -- register, balance, reports, sync -- independent of
   ledger size, or do they degrade? Anything that scans the whole ledger to
   answer a question about one account will show up here as a rising line.
2. Do the declared indexes actually do anything? ``--drop-indexes`` runs the
   same measurements with them removed, which is the only honest way to justify
   keeping them.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pymoney.core import (
    AccountType,
    Commodity,
    LegSpec,
    ManualClock,
    balance_sheet,
    income_statement,
)
from pymoney.store import (
    create_engine,
    create_schema,
    metadata,
    open_book,
)

START = date(2010, 1, 1)
# Transactions per day. Held constant as the ledger grows, so a query over a
# fixed date window covers a fixed number of rows: otherwise "bigger ledger" and
# "denser ledger" are confounded and a flat query looks linear.
PER_DAY = 20


class Timer:
    """Collects timings and prints them as a table."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, float, str]] = []

    @contextmanager
    def measure(self, label: str, note: str = ""):
        started = time.perf_counter()
        yield
        self.rows.append((label, time.perf_counter() - started, note))

    def repeat(self, label: str, fn: Callable[[], Any], times: int = 5) -> None:
        """Median of several runs, for queries fast enough to be noisy."""
        samples = []
        for _ in range(times):
            started = time.perf_counter()
            fn()
            samples.append(time.perf_counter() - started)
        self.rows.append((label, statistics.median(samples), f"median of {times}"))

    def report(self, title: str) -> None:
        print(f"\n{title}")
        print("-" * 78)
        for label, seconds, note in self.rows:
            unit = f"{seconds * 1000:9.1f} ms" if seconds < 1 else f"{seconds:9.2f} s "
            print(f"  {label:<44} {unit}  {note}")


def build_chart(book: Any, *, accounts: int, categories: int) -> dict[str, Any]:
    assets = book.add_account("Assets", AccountType.ASSET, placeholder=True)
    expenses = book.add_account("Expenses", AccountType.EXPENSE, placeholder=True)
    income = book.add_account("Income", AccountType.INCOME, placeholder=True)
    equity = book.add_account("Opening", AccountType.EQUITY)
    banks = [
        book.add_account(f"Account {i}", AccountType.CHECKING, parent=assets)
        for i in range(accounts)
    ]
    leaves = []
    for group in range(max(1, categories // 5)):
        parent = book.add_account(
            f"Group {group}", AccountType.EXPENSE, parent=expenses, placeholder=True
        )
        leaves += [
            book.add_account(f"Cat {group}.{leaf}", AccountType.EXPENSE, parent=parent)
            for leaf in range(5)
        ]
    salary = book.add_account("Salary", AccountType.INCOME, parent=income)
    return {
        "banks": banks,
        "leaves": leaves,
        "salary": salary,
        "equity": equity,
        "assets": assets,
        "expenses": expenses,
    }


def post_many(
    book: Any,
    chart: dict[str, Any],
    count: int,
    seed: int = 7,
    bulk: bool = False,
    offset: int = 0,
) -> None:
    if bulk:
        with book.bulk():
            post_many(book, chart, count, seed, offset=offset)
        return
    rng = random.Random(seed)
    for i in range(count):
        bank = rng.choice(chart["banks"])
        category = rng.choice(chart["leaves"])
        book.post(
            START + timedelta(days=(offset + i) // PER_DAY),
            [
                LegSpec.of(bank.id, f"-{rng.randint(1, 99999) / 100:.2f}"),
                LegSpec.of(category.id),
            ],
            memo=f"txn {i}",
        )


def drop_indexes(engine: Any) -> list[str]:
    """Remove every declared index, to measure what they were worth."""
    dropped = []
    with engine.begin() as conn:
        for table in metadata.tables.values():
            for index in table.indexes:
                try:
                    index.drop(conn)
                    dropped.append(index.name)
                except Exception as exc:  # pragma: no cover - dialect differences
                    print(f"  (could not drop {index.name}: {exc})")
    return dropped


def run_scaling(args: argparse.Namespace, engine: Any, book: Any) -> None:
    """Measure the hot queries as the ledger grows.

    The number that matters is not any single timing, it is the *slope*. A
    register or a balance asks about one account, so its cost should be flat as
    the ledger grows; anything that rises is scanning data it has no business
    reading. This is the table that justifies the indexes -- and the one that
    would catch a future change quietly removing them.
    """
    chart = build_chart(book, accounts=args.accounts, categories=args.categories)
    bank = chart["banks"][0]
    sizes = [int(x) for x in args.scaling.split(",")]
    header = (
        f"{'ledger size':>12} {'1 balance':>12} {'1 month reg':>13} "
        f"{'all balances':>14} {'bal sheet':>12} {'pull 20':>10} {'1 txn':>9}"
    )
    print(f"\nscaling on {args.backend}{' | NO INDEXES' if args.drop_indexes else ''}")
    print("-" * len(header))
    print(header)

    def median(fn: Callable[[], Any], times: int = 5) -> float:
        samples = []
        for _ in range(times):
            started = time.perf_counter()
            fn()
            samples.append(time.perf_counter() - started)
        return statistics.median(samples) * 1000

    posted = 0
    for target in sizes:
        post_many(book, chart, target - posted, seed=posted + 1, bulk=True, offset=posted)
        posted = target
        policy = book.default_policy(date(2099, 12, 31))
        one_id = book.store.transaction_ids()[0]
        # A fixed window near the start of history: the same number of rows at
        # every ledger size, so this column should not move at all.
        window = (START + timedelta(days=30), START + timedelta(days=60))
        # Bound the loop variables explicitly; late binding in a benchmark
        # would silently measure the wrong thing.
        row = [
            median(lambda b=bank.id: book.balance(b)),
            median(
                lambda a=bank.id, lo=window[0], hi=window[1]: book.register(
                    a, start=lo, end=hi
                )
            ),
            median(book.balances_by_account, 3),
            median(lambda p=policy: balance_sheet(book, p), 3),
            median(lambda: book.pull(0, max_revisions=20)),
            median(lambda i=one_id: book.transaction(i)),
        ]
        cells = " ".join(
            f"{value:>{width}.1f}"
            for value, width in zip(row, [12, 13, 14, 12, 10, 9], strict=True)
        )
        print(f"{posted:>12,} {cells}")
    print("  (all figures milliseconds, median of 3-5 runs)")


def run(args: argparse.Namespace) -> None:
    url = args.url
    if url is None:
        if args.backend == "sqlite":
            path = Path(args.path or "/tmp/pymoney-bench.sqlite")
            path.unlink(missing_ok=True)
            Path(str(path) + "-wal").unlink(missing_ok=True)
            Path(str(path) + "-shm").unlink(missing_ok=True)
            url = f"sqlite+pysqlite:///{path}"
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
            import sqlalchemy as sa
            from conftest import postgres_url  # type: ignore[import-not-found]

            base = postgres_url()
            engine = create_engine(base)
            with engine.begin() as conn:
                conn.execute(sa.text("DROP SCHEMA IF EXISTS bench CASCADE"))
                conn.execute(sa.text("CREATE SCHEMA bench"))
            engine.dispose()
            separator = "&" if "?" in base else "?"
            url = f"{base}{separator}options=-csearch_path%3Dbench"

    engine = create_engine(url)
    create_schema(engine)
    if args.drop_indexes:
        names = drop_indexes(engine)
        print(f"dropped {len(names)} indexes")

    book = open_book(
        engine,
        name="Benchmark",
        base_currency=Commodity.currency("EUR"),
        clock=ManualClock(),
    )
    if args.scaling:
        run_scaling(args, engine, book)
        book.close()
        engine.dispose()
        return

    timer = Timer()

    with timer.measure("build chart of accounts"):
        chart = build_chart(book, accounts=args.accounts, categories=args.categories)
    total_accounts = len(book.accounts())

    label = "post {n} transactions{suffix}".format(
        n=args.transactions, suffix=" (bulk)" if args.bulk else ""
    )
    with timer.measure(label):
        post_many(book, chart, args.transactions, bulk=args.bulk)
    posted = book.store.transaction_count()
    rate = args.transactions / max(timer.rows[-1][1], 1e-9)

    bank = chart["banks"][0]
    policy = book.default_policy(date(2026, 12, 31))

    timer.repeat("balance of one account", lambda: book.balance(bank.id))
    timer.repeat("register of one account", lambda: book.register(bank.id), times=3)
    timer.repeat(
        "register, one month window",
        lambda: book.register(bank.id, start=date(2025, 1, 1), end=date(2025, 1, 31)),
    )
    timer.repeat("balances of every account", book.balances_by_account)
    timer.repeat(
        "subtree balance (Assets)",
        lambda: book.subtree_balances(chart["assets"].id),
        times=3,
    )
    timer.repeat("balance sheet", lambda: balance_sheet(book, policy), times=3)
    timer.repeat(
        "income statement",
        lambda: income_statement(book, policy, start=START),
        times=3,
    )
    timer.repeat("changes since head-10", lambda: book.changes_since(book.revision - 10))
    timer.repeat("pull 20 revisions", lambda: book.pull(0, max_revisions=20))
    one_id = book.store.transaction_ids()[0]
    timer.repeat("load one transaction by id", lambda: book.transaction(one_id))
    timer.repeat(
        "posting_count of one account", lambda: book.store.posting_count(bank.id)
    )

    with timer.measure("check_integrity (full scan)"):
        problems = book.check_integrity()

    timer.report(
        f"{args.backend} | {posted} transactions, {total_accounts} accounts"
        f"{' | NO INDEXES' if args.drop_indexes else ''}"
    )
    print(f"\n  write throughput: {rate:,.0f} transactions/second")
    print(f"  integrity problems: {len(problems)}")
    if args.backend == "sqlite" and not args.url:
        size = Path(url.split("///")[1]).stat().st_size / 1e6
        print(f"  database size: {size:.1f} MB ({size * 1e6 / posted:.0f} bytes/txn)")

    book.close()
    engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["sqlite", "postgres"], default="sqlite")
    parser.add_argument("--url", help="explicit SQLAlchemy URL, overrides --backend")
    parser.add_argument("--path", help="SQLite file path")
    parser.add_argument("--transactions", type=int, default=20_000)
    parser.add_argument("--accounts", type=int, default=10)
    parser.add_argument("--categories", type=int, default=100)
    parser.add_argument(
        "--bulk", action="store_true", help="post inside one book.bulk() block"
    )
    parser.add_argument(
        "--scaling",
        help="comma-separated ledger sizes, e.g. 10000,50000,200000; measures "
        "whether the hot queries stay flat as the ledger grows",
    )
    parser.add_argument(
        "--drop-indexes",
        action="store_true",
        help="drop every index first, to show what they are worth",
    )
    run(parser.parse_args())


if __name__ == "__main__":
    main()
