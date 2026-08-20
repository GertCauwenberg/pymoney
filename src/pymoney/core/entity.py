"""Common identity and audit fields (decisions #5, #10, #11).

Every mutable row in PyMoney carries:

* ``id``          -- UUIDv7, time-ordered, globally unique
* ``book_id``     -- tenant scope, present from day one
* ``created_at``  -- UTC instant, server clock
* ``updated_at``  -- UTC instant, server clock, monotonic per row
* ``revision``    -- server-assigned monotonic integer per book

``revision`` is the sync backbone: ``GET /changes?since=<revision>`` is a range
scan, and a client knows it has a consistent view once it has seen every row up
to the book's current revision.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from uuid import UUID

from .ids import new_id

__all__ = ["EPOCH", "Entity"]

EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)
"""Sentinel stamp for entities not yet accepted by a Book."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Entity:
    """Base for all persisted domain entities."""

    id: UUID = field(default_factory=new_id)
    book_id: UUID | None = None
    created_at: datetime = EPOCH
    updated_at: datetime = EPOCH
    revision: int = 0

    def stamped(self, *, at: datetime, revision: int, created: bool = False) -> Entity:
        """Return a copy with fresh audit stamps. Applied by the Book only."""
        if created:
            return replace(self, created_at=at, updated_at=at, revision=revision)
        return replace(self, updated_at=at, revision=revision)
