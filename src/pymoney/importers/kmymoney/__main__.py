"""Command-line KMyMoney import.

    python -m pymoney.importers.kmymoney money.kmy --database ledger.sqlite
    python -m pymoney.importers.kmymoney money.kmy --dry-run
    python -m pymoney.importers.kmymoney money.kmy --inspect

``--dry-run`` imports into a throwaway in-memory book and prints the report, so
a user can see exactly what would happen -- including every rounding correction
-- before committing a database to it. Running that first is the advice.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ...core.clock import SystemClock
from .importer import ImportOptions, import_kmymoney
from .parser import KmyFile, KmyFormatError
from .report import ImportReport

__all__ = ["main"]


def _inspect(path: Path) -> int:
    """Describe the file without importing anything."""
    source = KmyFile.read(path)
    print(f"{path}")
    print(f"  format:        {source.path.suffix or '(none)'}")
    print(f"  created:       {source.info.creation_date}")
    print(f"  last modified: {source.info.last_modified}")
    print(f"  version:       {source.info.version} (fix {source.info.fix_version})")
    print(f"  base currency: {source.base_currency_id or '(not set)'}")
    print()
    print(f"  currencies:    {len(source.currencies)}")
    print(f"  securities:    {len(source.securities)}")
    print(f"  accounts:      {len(source.accounts)}")
    print(f"  payees:        {len(source.payees)}")
    print(f"  institutions:  {len(source.institutions)}")
    print(f"  prices:        {len(source.prices)}")
    print(f"  tags:          {len(source.tags)}")
    declared = source.declared_counts.get("transactions", -1)
    print(f"  transactions:  {declared if declared >= 0 else '?'} (as declared)")
    for name in ("schedules", "budgets", "reports", "costcenters"):
        count = source.declared_counts.get(name, 0)
        if count > 0:
            print(f"  {name + ':':<14} {count} (not imported)")
    return 0


def _open_book(args: argparse.Namespace):
    """The target book: a database, or memory for a dry run."""
    from ...core.book import Book

    if args.dry_run and not args.database:
        return Book(name=args.name or "Dry run", clock=SystemClock()), None

    if not args.database:
        raise SystemExit(
            "--database is required (or use --dry-run to import into memory)"
        )
    try:
        from ...store import create_engine, create_schema, open_book
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise SystemExit(
            "the 'store' extra is not installed: pip install 'pymoney-core[store]'"
        ) from exc

    url = args.database
    if "://" not in url:
        url = f"sqlite+pysqlite:///{Path(url).expanduser()}"
    engine = create_engine(url)
    if args.create_schema:
        create_schema(engine)
    return open_book(engine, name=args.name), engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pymoney.importers.kmymoney",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", type=Path, help="the .kmy file (gzipped or plain XML)")
    parser.add_argument(
        "--database",
        help="target: a SQLAlchemy URL, or a path for a SQLite file",
    )
    parser.add_argument("--name", default="", help="name for the imported book")
    parser.add_argument(
        "--no-create-schema",
        dest="create_schema",
        action="store_false",
        help="assume the schema exists (use `alembic upgrade head` instead)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="import into memory and report, touching no database",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="describe the file and exit without importing",
    )
    parser.add_argument(
        "--no-rounding-adjustments",
        dest="adjust_rounding",
        action="store_false",
        help=(
            "report the rational/minor-unit differences but do not post "
            "correcting entries, leaving balances as the raw sum of rounded "
            "splits"
        ),
    )
    parser.add_argument(
        "--skip-prices", action="store_true", help="do not import price history"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="show every note")
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="print nothing unless it fails"
    )
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"no such file: {args.file}", file=sys.stderr)
        return 2

    try:
        if args.inspect:
            return _inspect(args.file)

        book, engine = _open_book(args)
        try:
            report: ImportReport = import_kmymoney(
                args.file,
                book,
                options=ImportOptions(
                    adjust_rounding=args.adjust_rounding,
                    skip_prices=args.skip_prices,
                    dry_run=args.dry_run and engine is None,
                ),
            )
        finally:
            book.close()
            if engine is not None:
                engine.dispose()
    except KmyFormatError as exc:
        print(f"cannot read {args.file}: {exc}", file=sys.stderr)
        return 2

    if not args.quiet or not report.ok:
        print(report.summary(verbose=args.verbose))
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
