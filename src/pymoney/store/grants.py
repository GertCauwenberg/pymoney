"""Who may see a book: the one place grants are written.

Lives in the store package rather than the API so the importer's command line
can use it without pulling FastAPI in.

    python -m pymoney.store.grants BOOK_ID alice@example.org owner
    python -m pymoney.store.grants --list BOOK_ID

The API grants the creator ``owner`` when a book is created through it, and
owners manage the rest from the Sharing page. A book created any other way -- by
the KMyMoney importer, by a script over ``pymoney.store`` -- has no grant at all,
and is therefore invisible to every user. This is the way in: the same write the
API does, from the command line, against the same database.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Connection, Engine, select

from . import identity, schema
from .engine import DEFAULT_URL_ENV, create_engine
from .identity import ROLES

__all__ = ["grant", "grants_for", "main", "revoke"]


def grant(conn: Connection, book_id: UUID, subject: str, role: str) -> None:
    """Give ``subject`` ``role`` on ``book_id``, replacing any earlier grant.

    Runs on the caller's connection so it can share a transaction with whatever
    else has to happen atomically -- creating the book, for one.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}, not {role!r}")
    subject = subject.strip()
    if not subject:
        raise ValueError("subject must not be empty")
    existing = conn.execute(
        select(identity.book_grants.c.role).where(
            identity.book_grants.c.book_id == book_id,
            identity.book_grants.c.subject == subject,
        )
    ).scalar_one_or_none()
    if existing is None:
        conn.execute(
            identity.book_grants.insert().values(
                book_id=book_id,
                subject=subject,
                role=role,
                created_at=datetime.now(timezone.utc),
            )
        )
    elif existing != role:
        conn.execute(
            identity.book_grants.update()
            .where(
                identity.book_grants.c.book_id == book_id,
                identity.book_grants.c.subject == subject,
            )
            .values(role=role)
        )


def revoke(conn: Connection, book_id: UUID, subject: str) -> bool:
    result = conn.execute(
        identity.book_grants.delete().where(
            identity.book_grants.c.book_id == book_id,
            identity.book_grants.c.subject == subject,
        )
    )
    return bool(result.rowcount)


def grants_for(conn: Connection, book_id: UUID) -> list[tuple[str, str]]:
    """``(subject, role)`` pairs, ordered by subject."""
    rows = conn.execute(
        select(identity.book_grants.c.subject, identity.book_grants.c.role)
        .where(identity.book_grants.c.book_id == book_id)
        .order_by(identity.book_grants.c.subject)
    )
    return [(subject, role) for subject, role in rows]


def _book_exists(conn: Connection, book_id: UUID) -> bool:
    return (
        conn.execute(
            select(schema.books.c.id).where(schema.books.c.id == book_id)
        ).scalar_one_or_none()
        is not None
    )


def _engine(url: str | None) -> Engine:
    url = url or os.environ.get(DEFAULT_URL_ENV)
    if not url:
        raise SystemExit(f"no database URL: set {DEFAULT_URL_ENV} or pass --url")
    if "://" not in url:
        url = f"sqlite+pysqlite:///{url}"
    return create_engine(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pymoney.store.grants",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("book_id", type=UUID)
    parser.add_argument("subject", nargs="?", help="the email the user logs in with")
    parser.add_argument("role", nargs="?", choices=ROLES)
    parser.add_argument(
        "--url", help=f"SQLAlchemy URL or SQLite path; default ${DEFAULT_URL_ENV}"
    )
    parser.add_argument("--list", action="store_true", help="show the book's grants")
    parser.add_argument("--revoke", action="store_true", help="remove SUBJECT's grant")
    args = parser.parse_args(argv)

    if not args.list and not args.subject:
        parser.error("SUBJECT is required unless --list is given")
    if not args.list and not args.revoke and not args.role:
        parser.error("ROLE is required to grant access")

    engine = _engine(args.url)
    try:
        with engine.begin() as conn:
            if not _book_exists(conn, args.book_id):
                print(f"no book {args.book_id} in this database", file=sys.stderr)
                return 2
            if args.revoke:
                if revoke(conn, args.book_id, args.subject):
                    print(f"revoked {args.subject}")
                else:
                    print(f"{args.subject} had no grant", file=sys.stderr)
                    return 1
            elif not args.list:
                grant(conn, args.book_id, args.subject, args.role)
                print(f"{args.subject}: {args.role}")
            if args.list:
                rows = grants_for(conn, args.book_id)
                if not rows:
                    print("(no grants: nobody can see this book)")
                for subject, role in rows:
                    print(f"{subject}: {role}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
