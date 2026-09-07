"""schedules

Scheduled / recurring transactions: a date-less template, a recurrence rule,
and a cursor. A ledger table -- it is in the change log and syncs to replicas.

Revision ID: 46cb494dbd9e
Revises: 74db1db402c4
Created: 2026-09-06 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Autogenerate renders our custom column types by their full module path.
import pymoney.store.types

revision: str = "46cb494dbd9e"
down_revision: str | None = "74db1db402c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("book_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("recurrence", pymoney.store.types.JsonDict(), nullable=False),
        sa.Column("template", pymoney.store.types.JsonDict(), nullable=False),
        sa.Column("occurrences_done", sa.Integer(), nullable=False),
        sa.Column("next_due", sa.Date(), nullable=True),
        sa.Column("last_occurrence", sa.Date(), nullable=True),
        sa.Column("auto_post", sa.Boolean(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("created_at", pymoney.store.types.UtcDateTime(), nullable=False),
        sa.Column("updated_at", pymoney.store.types.UtcDateTime(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.id"],
            name=op.f("fk_schedules_book_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedules")),
    )
    op.create_index("ix_schedules_book_id_next_due", "schedules", ["book_id", "next_due"])


def downgrade() -> None:
    op.drop_index("ix_schedules_book_id_next_due", table_name="schedules")
    op.drop_table("schedules")
