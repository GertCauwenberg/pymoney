"""The link between a transaction and its receipt/invoice blobs.

One row per uploaded file; the bytes themselves live on the filesystem (see
:mod:`pymoney.api.blobs`). Not a ledger table: attachments are never synced and
never in the change log -- they exist server-side only, and a client fetches
them on demand. Kept here, beside :mod:`pymoney.store.grants` and
:mod:`pymoney.store.import_mappings`, so the store owns its non-domain tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Connection, delete, select

from ..core.ids import new_id
from . import schema

__all__ = [
    "AttachmentRow",
    "add_attachment",
    "attachments_for_transaction",
    "delete_attachment",
    "get_attachment",
]


@dataclass(frozen=True, slots=True)
class AttachmentRow:
    id: UUID
    book_id: UUID
    transaction_id: UUID
    filename: str
    content_type: str
    byte_size: int
    sha256: str
    uploaded_by: str
    uploaded_at: datetime


def _row(r: object) -> AttachmentRow:
    return AttachmentRow(
        id=r.id,  # type: ignore[attr-defined]
        book_id=r.book_id,  # type: ignore[attr-defined]
        transaction_id=r.transaction_id,  # type: ignore[attr-defined]
        filename=r.filename,  # type: ignore[attr-defined]
        content_type=r.content_type,  # type: ignore[attr-defined]
        byte_size=r.byte_size,  # type: ignore[attr-defined]
        sha256=r.sha256,  # type: ignore[attr-defined]
        uploaded_by=r.uploaded_by,  # type: ignore[attr-defined]
        uploaded_at=r.uploaded_at,  # type: ignore[attr-defined]
    )


def add_attachment(
    conn: Connection,
    *,
    book_id: UUID,
    transaction_id: UUID,
    filename: str,
    content_type: str,
    byte_size: int,
    sha256: str,
    uploaded_by: str,
    attachment_id: UUID | None = None,
) -> AttachmentRow:
    now = datetime.now(timezone.utc)
    attachment_id = attachment_id or new_id()
    conn.execute(
        schema.attachments.insert().values(
            id=attachment_id,
            book_id=book_id,
            transaction_id=transaction_id,
            filename=filename,
            content_type=content_type,
            byte_size=byte_size,
            sha256=sha256,
            uploaded_by=uploaded_by,
            uploaded_at=now,
        )
    )
    return AttachmentRow(
        id=attachment_id,
        book_id=book_id,
        transaction_id=transaction_id,
        filename=filename,
        content_type=content_type,
        byte_size=byte_size,
        sha256=sha256,
        uploaded_by=uploaded_by,
        uploaded_at=now,
    )


def attachments_for_transaction(
    conn: Connection, book_id: UUID, transaction_id: UUID
) -> list[AttachmentRow]:
    rows = conn.execute(
        select(schema.attachments)
        .where(
            schema.attachments.c.book_id == book_id,
            schema.attachments.c.transaction_id == transaction_id,
        )
        .order_by(schema.attachments.c.uploaded_at, schema.attachments.c.id)
    )
    return [_row(r) for r in rows]


def get_attachment(
    conn: Connection, book_id: UUID, attachment_id: UUID
) -> AttachmentRow | None:
    r = conn.execute(
        select(schema.attachments).where(
            schema.attachments.c.book_id == book_id,
            schema.attachments.c.id == attachment_id,
        )
    ).one_or_none()
    return None if r is None else _row(r)


def delete_attachment(conn: Connection, book_id: UUID, attachment_id: UUID) -> bool:
    result = conn.execute(
        delete(schema.attachments).where(
            schema.attachments.c.book_id == book_id,
            schema.attachments.c.id == attachment_id,
        )
    )
    return bool(result.rowcount)
