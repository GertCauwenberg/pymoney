"""import mappings

The per-account CSV column mapping, remembered so next week's statement opens
ready to review. Not a ledger table: not synced, not in the change log.

Revision ID: 74db1db402c4
Revises: b7d2c4e8a1f3
Created: 2026-09-05 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Autogenerate renders our custom column types by their full module path.
import pymoney.store.types

revision: str = "74db1db402c4"
down_revision: str | None = "b7d2c4e8a1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_mappings",
        sa.Column("book_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("account_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("mapping", pymoney.store.types.JsonDict(), nullable=False),
        sa.Column("updated_at", pymoney.store.types.UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.id"],
            name=op.f("fk_import_mappings_book_id"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "book_id", "account_id", name="uq_import_mappings_book_id_account_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("import_mappings")
