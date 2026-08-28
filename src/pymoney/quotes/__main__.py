"""Fetch quotes from the command line.

    python -m pymoney.quotes --url sqlite+pysqlite:///ledger.sqlite
    python -m pymoney.quotes --start 2020-01-01            # backfill
    python -m pymoney.quotes --list-providers

For a cron job, or a one-off backfill, when the API server is **not** running
against the same database: the server caches the small tables and assumes it is
the only writer, so while it runs, use its own scheduler or
``POST /books/{id}/quotes/fetch`` instead.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from uuid import UUID

from .registry import ProviderRegistry
from .service import QuoteService

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pymoney.quotes",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url", help="SQLAlchemy URL or SQLite path; default $PYMONEY_DATABASE_URL"
    )
    parser.add_argument(
        "--book", type=UUID, help="one book; default: every book in the database"
    )
    parser.add_argument(
        "--start", type=date.fromisoformat, help="backfill from this date"
    )
    parser.add_argument(
        "--end", type=date.fromisoformat, help="up to this date (default today)"
    )
    parser.add_argument(
        "--plugin",
        action="append",
        default=[],
        metavar="MODULE:CLASS",
        help="load an extra provider (repeatable); $PYMONEY_QUOTE_PLUGINS works too",
    )
    parser.add_argument(
        "--list-providers", action="store_true", help="show the providers and exit"
    )
    args = parser.parse_args(argv)

    plugins = [
        p for p in os.environ.get("PYMONEY_QUOTE_PLUGINS", "").split(",") if p.strip()
    ]
    registry = ProviderRegistry.discover([*plugins, *args.plugin])
    for problem in registry.problems:
        print(f"warning: {problem}", file=sys.stderr)
    if args.list_providers:
        for provider in registry:
            print(f"{provider.name:<12} {provider.description}")
        return 0

    url = args.url or os.environ.get("PYMONEY_DATABASE_URL")
    if not url:
        raise SystemExit("no database URL: set PYMONEY_DATABASE_URL or pass --url")
    if "://" not in url:
        url = f"sqlite+pysqlite:///{url}"

    from sqlalchemy import select

    from ..store import create_engine, open_book, schema

    engine = create_engine(url)
    service = QuoteService(registry)
    failed = False
    try:
        if args.book is not None:
            ids = [args.book]
        else:
            with engine.connect() as conn:
                ids = list(conn.execute(select(schema.books.c.id)).scalars())
        for book_id in ids:
            book = open_book(engine, book_id=book_id)
            try:
                if book.base_currency_id is None:
                    continue
                report = service.run(book, start=args.start, end=args.end)
            finally:
                book.close()
            print(f"{book.name} ({book_id}): {report.summary()}")
            failed = failed or not report.ok
    finally:
        engine.dispose()
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
