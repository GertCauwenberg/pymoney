"""attachments

Receipts, invoices and scans pinned to a transaction. Only the link and the
metadata live here; the bytes are files on disk (see pymoney.api.blobs). Not a
ledger table: never synced, never in the change log.

Revision ID: b2d3f5061728
Revises: 1b508714625f
Created: 2026-09-09 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Autogenerate renders our custom column types by their full module path.
import pymoney.store.types

revision: str = "b2d3f5061728"
down_revision: str | None = "1b508714625f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("book_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("transaction_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by", sa.String(length=255), nullable=False),
        sa.Column("uploaded_at", pymoney.store.types.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.id"],
            name=op.f("fk_attachments_book_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_attachments_transaction_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attachments")),
    )
    op.create_index("ix_attachments_transaction_id", "attachments", ["transaction_id"])
    op.create_index("ix_attachments_book_id", "attachments", ["book_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_book_id", table_name="attachments")
    op.drop_index("ix_attachments_transaction_id", table_name="attachments")
    op.drop_table("attachments")
