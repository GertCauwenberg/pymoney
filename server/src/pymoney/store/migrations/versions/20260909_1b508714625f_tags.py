"""tags

Free-form labels on a transaction's legs. ``tags`` is a ledger table -- it is
in the change log and syncs to replicas, like payees. ``split_tags`` is the
junction: `split_id` is not a foreign key, because splits are rewritten
wholesale with their transaction, so these rows are owned and replaced by
`transaction_id`.

Revision ID: 1b508714625f
Revises: 46cb494dbd9e
Created: 2026-09-09 08:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Autogenerate renders our custom column types by their full module path.
import pymoney.store.types

revision: str = "1b508714625f"
down_revision: str | None = "46cb494dbd9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("book_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_key", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("colour", sa.String(length=16), nullable=True),
        sa.Column("closed", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", pymoney.store.types.UtcDateTime(), nullable=False),
        sa.Column("updated_at", pymoney.store.types.UtcDateTime(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.id"],
            name=op.f("fk_tags_book_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("book_id", "name_key", name="uq_tags_book_id_name_key"),
    )
    op.create_table(
        "split_tags",
        sa.Column("book_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("transaction_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("split_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("tag_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.id"],
            name=op.f("fk_split_tags_book_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("fk_split_tags_tag_id")
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_split_tags_transaction_id"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("split_id", "tag_id", name="uq_split_tags_split_id_tag_id"),
    )
    with op.batch_alter_table("split_tags", schema=None) as batch_op:
        batch_op.create_index(
            "ix_split_tags_book_id_tag_id", ["book_id", "tag_id"], unique=False
        )
        batch_op.create_index(
            "ix_split_tags_transaction_id", ["transaction_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("split_tags", schema=None) as batch_op:
        batch_op.drop_index("ix_split_tags_transaction_id")
        batch_op.drop_index("ix_split_tags_book_id_tag_id")
    op.drop_table("split_tags")
    op.drop_table("tags")
