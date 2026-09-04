"""``GET /changes`` -- the pull half of the replica protocol.

The unit is a *revision batch*: everything one command did, with the entities
already resolved. A command can touch hundreds of rows (merging a payee across
400 transactions) and a replica must never see half of one, so ``limit_rows`` is
a **soft** cap: paging stops on a revision boundary, and a single command bigger
than the cap is still returned whole.

Entities arrive in typed lists rather than a heterogeneous array, so a client can
apply them table by table and the OpenAPI document actually describes the
payload.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Response

from ...core.changelog import EntityKind
from ..deps import ReadableBook
from ..schemas import (
    AccountOut,
    BookOut,
    ChangeOut,
    CommodityOut,
    DeletionOut,
    PayeeOut,
    PriceOut,
    SyncBatchOut,
    SyncPageOut,
    TransactionOut,
)
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}", tags=["sync"])


def _batch(book: Any, batch: Any) -> SyncBatchOut:
    out = SyncBatchOut(
        revision=batch.revision,
        at=batch.at,
        command=batch.command,
        actor=batch.actor,
        changes=[
            ChangeOut(
                revision=c.revision,
                at=c.at,
                kind=c.kind.value,
                entity_id=c.entity_id,
                op=c.op.value,
                command=c.command,
                actor=c.actor,
                note=c.note,
            )
            for c in batch.changes
        ],
        deletions=[
            DeletionOut(kind=c.kind.value, id=c.entity_id) for c in batch.deletions
        ],
    )
    for entity in batch.upserts_of(EntityKind.COMMODITY):
        out.commodities.append(CommodityOut.of(entity))
    for entity in batch.upserts_of(EntityKind.ACCOUNT):
        out.accounts.append(AccountOut.of(entity, book.path(entity.id)))
    for entity in batch.upserts_of(EntityKind.PAYEE):
        out.payees.append(PayeeOut.of(entity))
    for entity in batch.upserts_of(EntityKind.TRANSACTION):
        out.transactions.append(TransactionOut.of(entity))
    for entity in batch.upserts_of(EntityKind.PRICE):
        out.prices.append(PriceOut.of(entity))
    for entity in batch.upserts_of(EntityKind.BOOK):
        out.book = BookOut.of(entity, book.revision)
    return out


@router.get(
    "/changes",
    response_model=SyncPageOut,
    summary="Everything that changed above a revision",
)
def changes(
    book: ReadableBook,
    response: Response,
    since: int = Query(
        default=0,
        ge=0,
        description="Your current watermark. 0 for a full initial pull.",
    ),
    limit_revisions: int | None = Query(
        default=None, ge=1, le=1000, description="Hard cap on commands returned."
    ),
    limit_rows: int | None = Query(
        default=2000,
        ge=1,
        description=(
            "Soft cap on rows. A revision is never split, so one command larger "
            "than this is still returned whole."
        ),
    ),
) -> SyncPageOut:
    set_revision(response, book)
    page = book.pull(since, max_revisions=limit_revisions, max_rows=limit_rows)
    return SyncPageOut(
        from_revision=page.from_revision,
        to_revision=page.to_revision,
        head=page.head,
        complete=page.complete,
        batches=[_batch(book, batch) for batch in page.batches],
    )


@router.get(
    "/changes/{revision}",
    response_model=SyncBatchOut,
    summary="One revision, as an atomically applicable batch",
)
def revision_batch(revision: int, book: ReadableBook, response: Response) -> SyncBatchOut:
    set_revision(response, book)
    return _batch(book, book.revision_batch(revision))


@router.get(
    "/integrity",
    response_model=list[str],
    summary="Re-verify every invariant",
)
def integrity(book: ReadableBook, response: Response) -> list[str]:
    """Cheap insurance after a bulk load. An empty list is a clean bill."""
    set_revision(response, book)
    return list(book.check_integrity())


@router.get(
    "/history",
    response_model=list[ChangeOut],
    summary="Who changed what, and when",
)
def history(
    book: ReadableBook,
    response: Response,
    entity_id: UUID | None = Query(
        default=None,
        description="Only the changes to one entity (a transaction, an account...).",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[ChangeOut]:
    """The change log as people read it: newest first. Every command is one
    revision; the note says what a rename, move or merge did. The full
    protocol-shaped version, with the entities, is GET /changes."""
    set_revision(response, book)
    if entity_id is not None:
        changes = book.changelog.for_entity(entity_id)
    else:
        # Every revision has at least one row, so the last `limit` revisions
        # hold at least `limit` rows: no need to read the whole log.
        changes = book.changelog.since(max(0, book.revision - limit))
    newest_first = sorted(changes, key=lambda c: (c.revision, c.at), reverse=True)[:limit]
    return [
        ChangeOut(
            revision=c.revision,
            at=c.at,
            kind=c.kind.value,
            entity_id=c.entity_id,
            op=c.op.value,
            command=c.command,
            actor=c.actor,
            note=c.note,
        )
        for c in newest_first
    ]


__all__ = ["router"]
