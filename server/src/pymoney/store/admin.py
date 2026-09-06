"""Removing a whole book.

One transaction, children before parents, grants included. Used by
``DELETE /books/{id}`` and by the desktop client when it rebuilds a replica.
Irreversible by design: the change log goes with the book, because a log of a
ledger that no longer exists would be an audit trail of nothing.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Connection, delete, select

from . import identity, schema

__all__ = ["book_exists", "delete_book"]


def book_exists(conn: Connection, book_id: UUID) -> bool:
    return (
        conn.execute(
            select(schema.books.c.id).where(schema.books.c.id == book_id)
        ).scalar_one_or_none()
        is not None
    )


def delete_book(conn: Connection, book_id: UUID) -> bool:
    """Delete every row of the book. False if there was no such book."""
    if not book_exists(conn, book_id):
        return False
    for table in (
        schema.splits,
        schema.transactions,
        schema.prices,
        schema.payees,
        schema.accounts,
        schema.commodities,
        schema.changes,
        schema.command_results,
        schema.import_mappings,
        identity.book_grants,
    ):
        conn.execute(delete(table).where(table.c.book_id == book_id))
    conn.execute(delete(schema.books).where(schema.books.c.id == book_id))
    return True
