"""normalise grant subjects

Grants are now matched the way logins are: an email subject is stripped and
casefolded (see ``pymoney.store.identity.normalize_subject``). A grant written
before this as ``Bob@Example.org`` would never match ``bob@example.org`` again,
so rewrite the existing rows. Where one book already holds the same address in
two spellings, the stronger role wins and the other row goes.

Revision ID: b7d2c4e8a1f3
Revises: 4e1b6aaeebfb
Created: 2026-08-30 16:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "b7d2c4e8a1f3"
down_revision: str | None = "4e1b6aaeebfb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RANK = {"viewer": 0, "editor": 1, "owner": 2}


def upgrade() -> None:
    if context.is_offline_mode():
        # A data migration needs to read the rows it rewrites; a SQL script
        # cannot. Say so in the script rather than emit nothing silently. The
        # grant CLI and the API normalise on write, so new rows are right; run
        # this migration online once to fix old ones.
        op.execute(
            "-- b7d2c4e8a1f3: normalising existing book_grants.subject values needs "
            "a live connection; run `python -m pymoney.store.migrate` against the "
            "database to apply this step."
        )
        return
    conn = op.get_bind()
    grants = sa.table(
        "book_grants",
        sa.column("book_id"),
        sa.column("subject", sa.String),
        sa.column("role", sa.String),
    )
    rows = conn.execute(
        sa.select(grants.c.book_id, grants.c.subject, grants.c.role)
    ).all()

    # The spelling every row of a (book, address) should end up with, and the
    # strongest role held under any spelling.
    wanted: dict[tuple[object, str], str] = {}
    for book_id, subject, role in rows:
        if "@" not in subject:
            continue  # an opaque identifier: case is significant, leave it
        key = (book_id, subject.strip().casefold())
        if key not in wanted or _RANK[role] > _RANK[wanted[key]]:
            wanted[key] = role

    for book_id, subject, _role in rows:
        if "@" not in subject:
            continue
        normalised = subject.strip().casefold()
        if subject == normalised:
            continue
        taken = conn.execute(
            sa.select(sa.func.count())
            .select_from(grants)
            .where(grants.c.book_id == book_id, grants.c.subject == normalised)
        ).scalar_one()
        if taken:
            conn.execute(
                sa.delete(grants).where(
                    grants.c.book_id == book_id, grants.c.subject == subject
                )
            )
        else:
            conn.execute(
                sa.update(grants)
                .where(grants.c.book_id == book_id, grants.c.subject == subject)
                .values(subject=normalised)
            )

    for (book_id, normalised), role in wanted.items():
        conn.execute(
            sa.update(grants)
            .where(grants.c.book_id == book_id, grants.c.subject == normalised)
            .values(role=role)
        )


def downgrade() -> None:
    # The original capitalisation is not recoverable and nothing depends on it:
    # the normalised rows match the same logins under the old code too.
    pass
