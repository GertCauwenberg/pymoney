"""budgets

What an account is allowed to move, month by month: a JSON map of the months
that carry a figure, in minor units. A month that is not in the map is
unbudgeted -- there is no flat default that would quietly apply to months
nobody planned. One plan per account. A ledger table (synced, in the change
log) that changes no balance.

Revision ID: c3e4061728aa
Revises: b2d3f5061728
Created: 2026-09-10 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Autogenerate renders our custom column types by their full module path.
import pymoney.store.types

revision: str = "c3e4061728aa"
down_revision: str | None = "b2d3f5061728"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("book_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("account_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column("currency_id", pymoney.store.types.Uuid(), nullable=False),
        sa.Column(
            "months", pymoney.store.types.JsonDict(), nullable=False, server_default="{}"
        ),
        sa.Column("created_at", pymoney.store.types.UtcDateTime(), nullable=False),
        sa.Column("updated_at", pymoney.store.types.UtcDateTime(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_budgets_account_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["books.id"],
            name=op.f("fk_budgets_book_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["currency_id"],
            ["commodities.id"],
            name=op.f("fk_budgets_currency_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
        sa.UniqueConstraint(
            "book_id", "account_id", name="uq_budgets_book_id_account_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("budgets")
