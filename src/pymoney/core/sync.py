"""The pull side of the replica protocol (decisions #5, #6, #13).

``changes_since`` hands back a flat list of row-level :class:`Change` records.
That is the right storage shape, but it is the wrong *delivery* shape, because a
single command can touch hundreds of rows and a client must never see half of
one.  Merging a payee across 400 transactions is one atomic fact about the
ledger; a replica that applied 200 of those rows would be showing a state the
server was never in.

So the wire unit is a :class:`RevisionBatch`: everything one command did, with
the entities already resolved, and a :class:`SyncPage` is a run of whole
batches.  Pagination counts rows but always stops on a revision boundary, so
``max_rows`` is a *soft* limit -- correctness first, payload size second.

The batch also collapses duplicates: if a command touched one entity twice, the
client gets its final state once, and an entity created and deleted within the
same revision is reported only as a deletion.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from .changelog import Change, ChangeOp, EntityKind

__all__ = ["RevisionBatch", "SyncPage", "build_batch", "pull", "revision_batch"]


@dataclass(frozen=True, slots=True, kw_only=True)
class RevisionBatch:
    """Everything one command changed, as one atomically applicable unit."""

    revision: int
    at: datetime
    command: str
    command_id: UUID | None = None
    actor: str = ""
    changes: tuple[Change, ...] = ()
    """The raw row-level records, in the order they were applied."""
    upserts: tuple[Any, ...] = ()
    """Current state of every entity the batch created or updated, deduplicated."""
    deletions: tuple[Change, ...] = ()
    """Tombstones: one per entity that no longer exists after this revision."""

    @property
    def rows(self) -> int:
        """How many rows a client has to apply."""
        return len(self.upserts) + len(self.deletions)

    @property
    def kinds(self) -> tuple[EntityKind, ...]:
        """Which tables this batch touches, in a stable order."""
        seen = {c.kind for c in self.changes}
        return tuple(k for k in EntityKind if k in seen)

    def upserts_of(self, kind: EntityKind) -> tuple[Any, ...]:
        """The upserted entities of one kind, for a client applying table by table."""
        ids = {c.entity_id for c in self.changes if c.kind is kind}
        return tuple(e for e in self.upserts if e.id in ids)

    def deletions_of(self, kind: EntityKind) -> tuple[UUID, ...]:
        return tuple(c.entity_id for c in self.deletions if c.kind is kind)

    def __len__(self) -> int:
        return self.rows

    def __str__(self) -> str:
        return (
            f"r{self.revision} {self.command}: "
            f"{len(self.upserts)} upsert(s), {len(self.deletions)} deletion(s)"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncPage:
    """A contiguous run of revision batches, safe to apply in order."""

    from_revision: int
    """The watermark the client asked from; batches are all above it."""
    to_revision: int
    """The client's new watermark once it has applied this page."""
    head: int
    """The book's current revision, so the client knows how far behind it is."""
    batches: tuple[RevisionBatch, ...] = ()

    @property
    def complete(self) -> bool:
        """True when the client is fully caught up after applying this page."""
        return self.to_revision >= self.head

    @property
    def rows(self) -> int:
        return sum(b.rows for b in self.batches)

    @property
    def changes(self) -> tuple[Change, ...]:
        """Every row-level change in the page, flattened."""
        return tuple(c for b in self.batches for c in b.changes)

    @property
    def is_empty(self) -> bool:
        return not self.batches

    def __len__(self) -> int:
        return len(self.batches)

    def __iter__(self):
        return iter(self.batches)

    def __str__(self) -> str:
        suffix = "" if self.complete else f" (behind by {self.head - self.to_revision})"
        return (
            f"SyncPage r{self.from_revision}..r{self.to_revision} of r{self.head}: "
            f"{len(self.batches)} batch(es), {self.rows} row(s){suffix}"
        )


def build_batch(
    revision: int,
    changes: Iterable[Change],
    resolve: Any,
) -> RevisionBatch:
    """Assemble one batch from the changes of a single revision.

    :param resolve: callable mapping a :class:`Change` to its current entity, or
        ``None`` if the row is gone.  The *final* op per entity decides whether
        it lands in ``upserts`` or ``deletions``, which is what makes the batch
        idempotent: applying it twice is the same as applying it once.
    """
    ordered = list(changes)
    if not ordered:
        raise ValueError(f"revision {revision} has no changes")

    first = ordered[0]
    final: dict[UUID, Change] = {}
    for change in ordered:
        final[change.entity_id] = change

    upserts: list[Any] = []
    deletions: list[Change] = []
    for change in final.values():
        if change.op is ChangeOp.DELETE:
            deletions.append(change)
            continue
        entity = resolve(change)
        if entity is None:  # pragma: no cover - defensive: a live row must resolve
            deletions.append(change)
        else:
            upserts.append(entity)

    return RevisionBatch(
        revision=revision,
        at=first.at,
        command=first.command,
        command_id=first.command_id,
        actor=first.actor,
        changes=tuple(ordered),
        upserts=tuple(upserts),
        deletions=tuple(deletions),
    )


def revision_batch(book: Any, revision: int) -> RevisionBatch:
    """Everything the command at ``revision`` changed, entities included.

    One revision is one command, so this is the unit a replica should apply
    atomically: a merge across 400 transactions arrives whole or not at all.

    :raises NotFoundError: if no command produced that revision.
    """
    from .errors import NotFoundError

    changes = book.changelog.at(revision)
    if not changes:
        raise NotFoundError(f"no revision {revision} in this book")
    return build_batch(revision, changes, book.entity_for_change)


def pull(
    book: Any,
    since: int = 0,
    *,
    max_revisions: int | None = None,
    max_rows: int | None = None,
) -> SyncPage:
    """Fetch everything above ``since`` as whole revision batches.

    This is the shape the ``GET /changes`` endpoint should have.

    :param max_revisions: hard cap on the number of commands returned.
    :param max_rows: *soft* cap on rows. A revision is never split, so the page
        stops after the first batch that reaches the cap -- a single command
        bigger than ``max_rows`` is still returned whole, because half of it is
        not a valid state.
    """
    from .errors import ValidationError

    head = book.revision
    if since < 0:
        raise ValidationError(f"sync watermark must not be negative: {since}")
    if since > head:
        raise ValidationError(
            f"sync watermark r{since} is ahead of this book (r{head}); the client "
            f"is talking to the wrong book or it was restored from a backup"
        )

    # Ask the store for only as much of the log as the page can hold. Without
    # this, a client paging through a long history re-reads the entire change log
    # on every request.
    by_revision: dict[int, list[Change]] = {}
    for change in book.changes_since(
        since, max_revisions=max_revisions, max_rows=max_rows
    ):
        by_revision.setdefault(change.revision, []).append(change)

    # Resolve every entity the page will need in one go, rather than one
    # transaction lookup per change.
    selected: list[int] = []
    rows = 0
    for revision in sorted(by_revision):
        if max_revisions is not None and len(selected) >= max_revisions:
            break
        if max_rows is not None and rows >= max_rows:
            break
        selected.append(revision)
        rows += len({c.entity_id for c in by_revision[revision]})
    entities = book.entities_for_changes(
        c
        for revision in selected
        for c in by_revision[revision]
        if c.op is not ChangeOp.DELETE
    )

    def resolve(change: Change) -> Any:
        found = entities.get(change.entity_id)
        return book.entity_for_change(change) if found is None else found

    batches: list[RevisionBatch] = []
    watermark = since
    for revision in selected:
        batches.append(build_batch(revision, by_revision[revision], resolve))
        watermark = revision

    return SyncPage(
        from_revision=since, to_revision=watermark, head=head, batches=tuple(batches)
    )
