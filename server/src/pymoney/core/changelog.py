"""The change log (decisions #5, #6, #11, #13).

Every mutation the book accepts appends one or more :class:`Change` records
under a single new revision.  Three things fall out of this for free:

* **Sync.** ``changes_since(revision)`` is the pull half of the replica
  protocol.  A client that has seen revision *N* asks for everything above it.
* **Audit.** Who changed what, when, and under which command.  Accounting apps
  are judged on trust (decision #11), and "the log is the truth" is much easier
  to trust than reconstructing history from mutable rows.
* **Tombstones.** Deletes are recorded explicitly, so a replica learns that a
  row is gone rather than merely failing to see it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

__all__ = ["Change", "ChangeLog", "ChangeOp", "EntityKind"]


class ChangeOp(StrEnum):
    """What happened to a row.

    ``DELETE`` entries are tombstones: a replica needs to be told that a row is
    gone, not merely fail to see it.
    """

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class EntityKind(StrEnum):
    """Which table a change refers to."""

    BOOK = "BOOK"
    COMMODITY = "COMMODITY"
    ACCOUNT = "ACCOUNT"
    PAYEE = "PAYEE"
    TAG = "TAG"
    TRANSACTION = "TRANSACTION"
    PRICE = "PRICE"
    SCHEDULE = "SCHEDULE"


@dataclass(frozen=True, slots=True, kw_only=True)
class Change:
    """One row-level change, stamped with the revision that produced it."""

    revision: int
    at: datetime
    kind: EntityKind
    entity_id: UUID
    op: ChangeOp
    command: str = ""
    """Name of the command that caused this change."""
    command_id: UUID | None = None
    """Client-supplied idempotency key, if any."""
    actor: str = ""
    """Who did it; filled in by the server's auth layer."""
    note: str = ""
    """Human-readable detail, e.g. "merged into <id>"."""

    @property
    def is_delete(self) -> bool:
        return self.op is ChangeOp.DELETE

    def __str__(self) -> str:
        return f"r{self.revision} {self.op} {self.kind} {self.entity_id}"


class ChangeLog:
    """Append-only, revision-ordered list of changes."""

    __slots__ = ("_changes",)

    def __init__(self, changes: Iterable[Change] = ()) -> None:
        self._changes: list[Change] = list(changes)

    def append(self, change: Change) -> Change:
        if self._changes and change.revision < self._changes[-1].revision:
            raise ValueError(
                f"change log is append-only: revision {change.revision} "
                f"precedes {self._changes[-1].revision}"
            )
        self._changes.append(change)
        return change

    def extend(self, changes: Iterable[Change]) -> None:
        for change in changes:
            self.append(change)

    def discard_after(self, length: int) -> None:
        """Drop trailing entries, leaving ``length`` of them.

        The one exception to append-only, and only for a store rolling back an
        aborted unit of work: those changes describe mutations that never
        happened, so keeping them would be a lie.
        """
        if length > len(self._changes):
            raise ValueError(
                f"cannot grow the log by discarding: have {len(self._changes)}, "
                f"asked to keep {length}"
            )
        del self._changes[length:]

    def since(self, revision: int) -> tuple[Change, ...]:
        """All changes strictly after ``revision``, in order.

        The pull half of the sync protocol.  Because revisions are assigned by
        the book and never reused, this is a suffix scan.
        """
        return tuple(c for c in self._changes if c.revision > revision)

    def at(self, revision: int) -> tuple[Change, ...]:
        """All changes belonging to one revision (i.e. to one command)."""
        return tuple(c for c in self._changes if c.revision == revision)

    def for_entity(self, entity_id: UUID) -> tuple[Change, ...]:
        return tuple(c for c in self._changes if c.entity_id == entity_id)

    def tombstones(self, since: int = 0) -> tuple[Change, ...]:
        """Deletes above ``since``, so a replica can drop rows it still holds."""
        return tuple(c for c in self.since(since) if c.is_delete)

    @property
    def head(self) -> int:
        """The highest revision in the log, or 0 when empty."""
        return self._changes[-1].revision if self._changes else 0

    def __len__(self) -> int:
        return len(self._changes)

    def __iter__(self) -> Iterator[Change]:
        return iter(self._changes)

    def __getitem__(self, index: int) -> Change:
        return self._changes[index]

    def __repr__(self) -> str:
        return f"ChangeLog({len(self._changes)} changes, head=r{self.head})"
