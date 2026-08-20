"""Book-level settings as a syncable entity.

A book has state of its own -- its name, its reporting currency -- and that
state has to travel the same road as everything else: changed only by a command,
stamped with a revision, recorded in the change log (decisions #5, #6).
Otherwise a replica silently disagrees with the server about which currency the
reports are in.

Modelling it as a single-row entity keeps the sync protocol uniform: a
``BOOK`` change resolves to a :class:`BookSettings` snapshot exactly as an
``ACCOUNT`` change resolves to an :class:`~pymoney.core.account.Account`.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .entity import Entity

__all__ = ["BookSettings"]


@dataclass(frozen=True, slots=True, kw_only=True)
class BookSettings(Entity):
    """Immutable snapshot of a book's own fields.

    ``id`` is the book's id, so the settings row is trivially addressable and
    there is never more than one per book.
    """

    name: str
    base_currency_id: UUID | None = None

    def __str__(self) -> str:
        return f"{self.name} (base {self.base_currency_id})"
