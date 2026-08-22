"""How long does a real migration take?

    python benchmarks/bench_kmymoney.py --transactions 50000

Generates a synthetic KMyMoney file of a given size and imports it, so the
answer to "will this take hours?" is a number rather than a hope. A twenty-year
ledger is perhaps 50,000 transactions; a heavy user with downloaded bank feeds
might have 200,000.
"""

from __future__ import annotations

import argparse
import gzip
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pymoney.core import Commodity, ManualClock
from pymoney.importers.kmymoney import import_kmymoney
from pymoney.store import create_engine, create_schema, open_book

START = date(2006, 1, 1)


def generate(path: Path, *, transactions: int, accounts: int, categories: int) -> None:
    """Write a plausible .kmy: a few accounts, many categories, long history.

    One transaction in twenty gets a rational amount that does not fit in cents,
    which is what forces the importer's rounding machinery to do real work.
    """
    rng = random.Random(17)
    banks = [f"A{i:06d}" for i in range(1, accounts + 1)]
    cats = [f"A{i:06d}" for i in range(accounts + 1, accounts + categories + 1)]
    payees = [f"P{i:06d}" for i in range(1, 51)]

    parts: list[str] = []
    parts.append('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE KMYMONEY-FILE>\n')
    parts.append("<KMYMONEY-FILE>\n")
    parts.append(
        ' <FILEINFO><CREATION_DATE date="2006-01-01"/>'
        '<LAST_MODIFIED_DATE date="2026-01-01"/><VERSION id="1"/>'
        '<FIXVERSION id="5"/></FILEINFO>\n'
    )
    parts.append(f' <PAYEES count="{len(payees)}">\n')
    for index, payee in enumerate(payees):
        parts.append(f'  <PAYEE id="{payee}" name="Payee {index}" email=""/>\n')
    parts.append(" </PAYEES>\n")

    parts.append(f' <ACCOUNTS count="{len(banks) + len(cats) + 1}">\n')
    for bank in banks:
        parts.append(
            f'  <ACCOUNT id="{bank}" name="Account {bank}" type="1" currency="EUR"'
            f' parentaccount="AStd::Asset" opened="2006-01-01"/>\n'
        )
    for category in cats:
        parts.append(
            f'  <ACCOUNT id="{category}" name="Category {category}" type="13"'
            f' currency="EUR" parentaccount="AStd::Expense" opened="2006-01-01"/>\n'
        )
    parts.append(
        '  <ACCOUNT id="A999999" name="Opening" type="16" currency="EUR"'
        ' parentaccount="AStd::Equity" opened="2006-01-01"/>\n'
    )
    parts.append(" </ACCOUNTS>\n")

    parts.append(f' <TRANSACTIONS count="{transactions}">\n')
    for i in range(transactions):
        when = START + timedelta(days=i // 20)
        bank = rng.choice(banks)
        category = rng.choice(cats)
        payee = rng.choice(payees)
        if i % 20 == 0:
            # A third of a euro: exact for KMyMoney, not for us.
            numerator, denominator = 1000, 300
        else:
            numerator, denominator = rng.randint(100, 50000), 100
        flag = 2 if i % 3 == 0 else 0
        parts.append(
            f'  <TRANSACTION id="T{i:018d}" postdate="{when}" entrydate="{when}"'
            f' commodity="EUR" memo="Entry {i}">\n   <SPLITS>\n'
            f'    <SPLIT id="S0001" account="{bank}" payee="{payee}"'
            f' shares="-{numerator}/{denominator}" value="-{numerator}/{denominator}"'
            f' reconcileflag="{flag}" memo="" action="" number="" bankid=""'
            f' price="1/1" reconciledate=""/>\n'
            f'    <SPLIT id="S0002" account="{category}" payee="{payee}"'
            f' shares="{numerator}/{denominator}" value="{numerator}/{denominator}"'
            f' reconcileflag="0" memo="" action="" number="" bankid=""'
            f' price="1/1" reconciledate=""/>\n'
            f"   </SPLITS>\n  </TRANSACTION>\n"
        )
    parts.append(" </TRANSACTIONS>\n")
    parts.append(' <KEYVALUEPAIRS><PAIR key="kmm-baseCurrency" value="EUR"/>')
    parts.append("</KEYVALUEPAIRS>\n")
    parts.append(' <SECURITIES count="0"/>\n')
    parts.append(
        ' <CURRENCIES count="1"><CURRENCY id="EUR" saf="100" scf="100" symbol="E"'
        ' type="2" name="Euro"/></CURRENCIES>\n'
    )
    parts.append(' <PRICES count="0"/>\n')
    parts.append("</KMYMONEY-FILE>\n")

    path.write_bytes(gzip.compress("".join(parts).encode()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transactions", type=int, default=50_000)
    parser.add_argument("--accounts", type=int, default=8)
    parser.add_argument("--categories", type=int, default=120)
    parser.add_argument("--keep", action="store_true", help="keep the generated file")
    args = parser.parse_args()

    source = Path("/tmp/pymoney-bench.kmy")
    target = Path("/tmp/pymoney-bench-import.sqlite")
    for suffix in ("", "-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)

    started = time.perf_counter()
    generate(
        source,
        transactions=args.transactions,
        accounts=args.accounts,
        categories=args.categories,
    )
    generated = time.perf_counter() - started
    size = source.stat().st_size / 1e6

    engine = create_engine(f"sqlite+pysqlite:///{target}")
    create_schema(engine)
    book = open_book(engine, base_currency=Commodity.currency("EUR"), clock=ManualClock())

    started = time.perf_counter()
    report = import_kmymoney(source, book, options=None)
    elapsed = time.perf_counter() - started

    print(f"\ngenerated {args.transactions:,} transactions in {generated:.1f}s")
    print(f"  file size (gzipped): {size:.1f} MB")
    print(
        f"\nimported in {elapsed:.1f}s  "
        f"({args.transactions / max(elapsed, 1e-9):,.0f} transactions/second)"
    )
    print(f"  accounts:            {report.imported.get('accounts', 0):,}")
    print(f"  transactions:        {report.imported.get('transactions', 0):,}")
    print(f"  splits:              {report.imported.get('splits', 0):,}")
    print(f"  rounding fixes:      {len(report.adjustments):,}")
    print(f"  database size:       {target.stat().st_size / 1e6:.1f} MB")
    print(f"  result:              {'OK' if report.ok else 'FAILED'}")

    started = time.perf_counter()
    problems = book.check_integrity()
    print(
        f"\n  check_integrity:     {time.perf_counter() - started:.1f}s, "
        f"{len(problems)} problem(s)"
    )

    book.close()
    engine.dispose()
    if not args.keep:
        source.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
