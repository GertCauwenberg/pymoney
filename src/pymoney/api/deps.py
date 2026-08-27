"""Request dependencies: who is calling, what they may do, and which book.

The chain is deliberately explicit. An endpoint that writes declares
``book: WritableBook`` and cannot accidentally be readable-only; one that reads
declares ``ReadableBook``. Authorisation is therefore visible in the signature
rather than buried in a body.

Two credentials are accepted, and the difference matters:

``Authorization: Bearer <token>``
    For the desktop and mobile clients. Not ambient, so no CSRF token is needed.

``Cookie``
    For the browser. Ambient, so any unsafe method additionally requires
    ``X-CSRF-Token`` matching the session. Without that, a form on another site
    could post a transaction using the user's cookie.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import Engine, select, update

from ..core.book import Book
from ..store import identity
from .registry import BookRegistry
from .security import hash_token, idempotency_key_to_uuid
from .settings import Settings

__all__ = [
    "Actor",
    "CurrentUser",
    "IdempotencyKey",
    "OwnedBook",
    "ReadableBook",
    "WritableBook",
    "get_engine",
    "get_registry",
    "get_settings_dep",
    "require_role",
]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_LAST_SEEN_RESOLUTION = timedelta(minutes=1)
"""How stale `last_seen_at` may get before a request pays to refresh it."""
ROLE_RANK = {"viewer": 0, "editor": 1, "owner": 2}


# ==========================================================================
# application state
# ==========================================================================
def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def get_registry(request: Request) -> BookRegistry:
    return request.app.state.registry


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


# ==========================================================================
# who is calling
# ==========================================================================
@dataclass(frozen=True, slots=True)
class Actor:
    """The authenticated caller."""

    user_id: UUID
    subject: str
    """The identity grants are keyed on: the email locally, or an OIDC subject."""
    display_name: str
    session_id: UUID
    via_cookie: bool

    @property
    def audit_name(self) -> str:
        """What lands in the change log's ``actor`` field."""
        return self.subject


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_actor(
    request: Request,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    authorization: Annotated[str | None, Header()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> Actor:
    token: str | None = None
    via_cookie = False
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    else:
        token = request.cookies.get(settings.cookie_name)
        via_cookie = token is not None
    if not token:
        raise _unauthenticated()

    now = datetime.now(timezone.utc)
    with engine.connect() as conn:
        row = conn.execute(
            select(
                identity.sessions.c.id,
                identity.sessions.c.user_id,
                identity.sessions.c.csrf_token,
                identity.sessions.c.expires_at,
                identity.sessions.c.last_seen_at,
                identity.users.c.email,
                identity.users.c.display_name,
                identity.users.c.active,
            )
            .join(
                identity.users,
                identity.users.c.id == identity.sessions.c.user_id,
            )
            .where(identity.sessions.c.token_hash == hash_token(token))
        ).one_or_none()
        if row is None or not row.active:
            raise _unauthenticated()

        # An abandoned session is as good as a stolen one, so idleness expires it
        # as surely as the clock does.
        idle = now - row.last_seen_at > timedelta(hours=settings.session_idle_hours)
        if row.expires_at <= now or idle:
            conn.execute(
                identity.sessions.delete().where(identity.sessions.c.id == row.id)
            )
            conn.commit()
            raise _unauthenticated()

        # Only touch last_seen_at when it has actually gone stale. Refreshing it
        # on every request would take a write lock each time, contending with
        # whatever command is in flight for no benefit.
        if now - row.last_seen_at > _LAST_SEEN_RESOLUTION:
            conn.execute(
                update(identity.sessions)
                .where(identity.sessions.c.id == row.id)
                .values(last_seen_at=now)
            )
            conn.commit()

    # The cookie travels automatically, so on its own it proves nothing about who
    # initiated the request.
    if (
        via_cookie
        and request.method not in SAFE_METHODS
        and (not x_csrf_token or not compare_digest(x_csrf_token, row.csrf_token))
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "missing or invalid X-CSRF-Token; cookie-authenticated writes "
                "need the csrf_token returned at login"
            ),
        )

    return Actor(
        user_id=row.user_id,
        subject=row.email,
        display_name=row.display_name,
        session_id=row.id,
        via_cookie=via_cookie,
    )


CurrentUser = Annotated[Actor, Depends(current_actor)]


# ==========================================================================
# what they may do
# ==========================================================================
def role_on(engine: Engine, book_id: UUID, subject: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            select(identity.book_grants.c.role).where(
                identity.book_grants.c.book_id == book_id,
                identity.book_grants.c.subject == subject,
            )
        ).scalar_one_or_none()


def readable_books(engine: Engine, subject: str) -> dict[UUID, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(identity.book_grants.c.book_id, identity.book_grants.c.role).where(
                identity.book_grants.c.subject == subject
            )
        )
        return {book_id: role for book_id, role in rows}


def require_role(minimum: str):
    """Build a dependency yielding a book the caller holds ``minimum`` on.

    The book is yielded inside the registry's per-book lock, so the whole request
    has exclusive use of it (see :mod:`.registry`).
    """

    def dependency(
        book_id: UUID,
        actor: CurrentUser,
        engine: Annotated[Engine, Depends(get_engine)],
        registry: Annotated[BookRegistry, Depends(get_registry)],
    ) -> Iterator[Book]:
        role = role_on(engine, book_id, actor.subject)
        if role is None:
            # Deliberately 404, not 403: whether a book exists is itself
            # information the caller has no grant to.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no book {book_id}",
            )
        if ROLE_RANK[role] < ROLE_RANK[minimum]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this needs {minimum} on the book; you have {role}",
            )
        with registry.open(book_id) as book:
            # Inside the lock, so no other request can see a half-set actor. The
            # change log is an audit trail; an entry without a name is much less
            # use than one with.
            previous = book.actor
            book.actor = actor.audit_name
            try:
                yield book
            finally:
                book.actor = previous

    return dependency


ReadableBook = Annotated[Book, Depends(require_role("viewer"))]
WritableBook = Annotated[Book, Depends(require_role("editor"))]
OwnedBook = Annotated[Book, Depends(require_role("owner"))]


# ==========================================================================
# idempotency
# ==========================================================================
def idempotency_key(
    actor: CurrentUser,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> UUID | None:
    """Turn the ``Idempotency-Key`` header into a command id.

    Replaying a key returns the original result instead of applying the command
    twice -- the mobile-client-on-a-train case. Any string is accepted and
    hashed deterministically, because clients in the wild do not all send UUIDs,
    and the hash is scoped to the caller so one user's key can never replay
    another's request on a shared book.
    """
    if not idempotency_key:
        return None
    if len(idempotency_key) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key must be at most 200 characters",
        )
    return idempotency_key_to_uuid(idempotency_key, subject=actor.subject)


IdempotencyKey = Annotated[UUID | None, Depends(idempotency_key)]
