"""Helpers shared by the book routers: revision headers and keyset cursors."""

from __future__ import annotations

import base64
import json
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Response, status

from ...core.book import Book

__all__ = [
    "REVISION_HEADER",
    "decode_cursor",
    "encode_cursor",
    "page_size",
    "set_revision",
]

REVISION_HEADER = "X-PyMoney-Revision"
# Starlette renamed this status constant; 422 is the number either way.
UNPROCESSABLE = 422


def set_revision(response: Response, book: Book) -> None:
    """Stamp every response with the book's revision.

    A client gets its sync watermark from any call it happens to make, instead of
    having to poll ``/changes`` to find out whether it is stale.
    """
    response.headers[REVISION_HEADER] = str(book.revision)


def page_size(requested: int | None, default: int, maximum: int) -> int:
    if requested is None:
        return default
    if requested < 1:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail="limit must be at least 1",
        )
    return min(requested, maximum)


def encode_cursor(payload: dict[str, Any]) -> str:
    """An opaque keyset cursor.

    Opaque because it is ours to change; a keyset rather than an offset because
    rows inserted while a client pages must not make it skip or repeat one.
    """
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="malformed cursor; pass back exactly what next_cursor gave you",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="malformed cursor"
        )
    return payload


def cursor_key(payload: dict[str, Any]) -> tuple[date, int, int]:
    """Parse a register cursor into its sort key."""
    try:
        return (
            date.fromisoformat(payload["d"]),
            UUID(payload["t"]).int,
            UUID(payload["s"]).int,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="malformed cursor"
        ) from exc
