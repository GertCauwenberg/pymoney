"""Tags: a free-form label attached to a transaction's legs.

A payee answers "who", an account answers "what category"; a tag answers
anything else the user cares to slice by -- a holiday, a house renovation, a
reimbursable expense.  Like payees they are a small, cached lookup table of
their own, referenced by id from :class:`~pymoney.core.transaction.Split`, so a
rename is one row and a report can group by them without a text join.

KMyMoney has the same concept (``<TAG>``), which is why the importer can carry
them across one-for-one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .entity import Entity
from .errors import ValidationError

__all__ = ["Tag"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Tag(Entity):
    """A label put on one or more legs of a transaction."""

    name: str = ""
    notes: str = ""
    colour: str | None = None
    """An optional ``#rrggbb`` swatch; KMyMoney stores one, so we keep it."""
    closed: bool = False
    """Hidden from the pickers, kept for the history that still references it."""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("tag name must not be empty")

    @property
    def key(self) -> str:
        return self.name.strip().casefold()

    def __str__(self) -> str:
        return self.name
