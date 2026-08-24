"""Users, sessions and per-book grants.

Identity lives in the same database and the same Alembic chain as the ledger.
That is a deliberate choice for the target deployment: a self-hosted install is
one binary and one database, and splitting identity into a second store would buy
nothing but a second thing to back up.

It is also *only* a default. Nothing in the ledger references these tables; an
install that authenticates against OIDC leaves ``users`` empty and maps a token's
subject claim onto ``book_grants`` instead, which is why the grant's subject is a
plain string rather than a foreign key.

Two details that matter for security:

``users.password_hash``
    scrypt, with a per-user salt and the parameters recorded alongside the hash,
    so they can be raised later without invalidating existing passwords.

``sessions.token_hash``
    The session *token* is never stored. A stolen database therefore yields no
    live sessions.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from .schema import NAME, UTC, metadata
from .types import Uuid

__all__ = ["ROLES", "book_grants", "sessions", "users"]

# Ordered weakest to strongest, so a check is a comparison.
ROLES = ("viewer", "editor", "owner")

users = Table(
    "users",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("email", NAME, nullable=False),
    Column("email_key", NAME, nullable=False),
    Column("display_name", NAME, nullable=False),
    # Null for an account that authenticates some other way (OIDC), which is not
    # the same thing as an account with an empty password.
    Column("password_hash", LargeBinary(128), nullable=True),
    Column("password_salt", LargeBinary(32), nullable=True),
    Column("password_params", String(64), nullable=True),
    Column("active", Boolean, nullable=False, server_default="1"),
    Column("created_at", UTC, nullable=False),
    Column("updated_at", UTC, nullable=False),
    UniqueConstraint("email_key", name="uq_users_email_key"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    # SHA-256 of the bearer token. The token itself is never persisted.
    Column("token_hash", LargeBinary(32), nullable=False),
    # Returned at login and required in a header on unsafe requests, so a cookie
    # alone cannot be used to forge one from another site.
    Column("csrf_token", String(64), nullable=False),
    Column("created_at", UTC, nullable=False),
    Column("expires_at", UTC, nullable=False),
    Column("last_seen_at", UTC, nullable=False),
    Column("user_agent", Text, nullable=False),
    UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    Index("ix_sessions_user_id", "user_id"),
    Index("ix_sessions_expires_at", "expires_at"),
)

book_grants = Table(
    "book_grants",
    metadata,
    Column("book_id", Uuid, ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
    # A plain string, not a foreign key to `users`: with OIDC this is the token's
    # subject claim and there is no local user row to point at.
    Column("subject", NAME, nullable=False),
    Column("role", String(16), nullable=False),
    Column("created_at", UTC, nullable=False),
    UniqueConstraint("book_id", "subject", name="uq_book_grants_book_id_subject"),
    Index("ix_book_grants_subject", "subject"),
)
