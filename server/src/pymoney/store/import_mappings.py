"""The remembered CSV column mapping for an account.

One row per account that has ever had a statement imported into it: the last
:class:`~pymoney.importers.statement.ColumnMapping` used, so the next download
needs no remapping. Not part of the ledger -- it is never synced, never in the
change log, and a book with none behaves exactly as one with them. Kept here,
beside :mod:`pymoney.store.grants`, so the store owns its own non-domain
tables and the API layer only reads and writes through these three functions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, delete, select

from . import schema

__all__ = ["forget_mapping", "get_mapping", "save_mapping"]


def get_mapping(
    conn: Connection, book_id: UUID, account_id: UUID
) -> dict[str, Any] | None:
    """The stored mapping dict for ``account_id``, or ``None`` if there is none."""
    row = conn.execute(
        select(schema.import_mappings.c.mapping).where(
            schema.import_mappings.c.book_id == book_id,
            schema.import_mappings.c.account_id == account_id,
        )
    ).scalar_one_or_none()
    return dict(row) if row is not None else None


def save_mapping(
    conn: Connection,
    book_id: UUID,
    account_id: UUID,
    mapping: dict[str, Any],
) -> None:
    """Store (replacing any earlier one) the mapping for ``account_id``."""
    now = datetime.now(timezone.utc)
    conn.execute(
        delete(schema.import_mappings).where(
            schema.import_mappings.c.book_id == book_id,
            schema.import_mappings.c.account_id == account_id,
        )
    )
    conn.execute(
        schema.import_mappings.insert().values(
            book_id=book_id,
            account_id=account_id,
            mapping=dict(mapping),
            updated_at=now,
        )
    )


def forget_mapping(conn: Connection, book_id: UUID, account_id: UUID) -> bool:
    """Drop the account's mapping; ``True`` if there was one. Called when the
    account itself is deleted, since nothing else references it."""
    result = conn.execute(
        delete(schema.import_mappings).where(
            schema.import_mappings.c.book_id == book_id,
            schema.import_mappings.c.account_id == account_id,
        )
    )
    return bool(result.rowcount)
