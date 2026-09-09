"""Tags: the free-form labels a transaction's legs can wear.

A small named lookup resource, shaped like payees: created once, referenced by
id, renamed in place. There is no merge -- a tag is cheaper to retire (close
it) than a payee is -- and a tag cannot be deleted while a transaction still
wears it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ...core import commands as cmd
from ...core.commands import UNSET
from ..deps import IdempotencyKey, ReadableBook, WritableBook
from ..schemas import TagIn, TagOut, TagPatch
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}/tags", tags=["tags"])


@router.get("", response_model=list[TagOut], summary="List tags")
def list_tags(
    book: ReadableBook,
    response: Response,
    search: str | None = Query(
        default=None, description="Case-insensitive substring of the name."
    ),
    include_closed: bool = Query(
        default=True, description="Closed tags are hidden from pickers."
    ),
) -> list[TagOut]:
    set_revision(response, book)
    needle = (search or "").strip().casefold()
    return [
        TagOut.of(t)
        for t in book.tags(include_closed=include_closed)
        if not needle or needle in t.name.casefold()
    ]


@router.post(
    "",
    response_model=TagOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tag",
)
def create_tag(
    body: TagIn, book: WritableBook, response: Response, key: IdempotencyKey
) -> TagOut:
    result = book.execute(
        cmd.CreateTag(
            name=body.name,
            notes=body.notes,
            colour=body.colour,
            closed=body.closed,
            id=body.id,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return TagOut.of(result.entity)


@router.get("/{tag_id}", response_model=TagOut, summary="One tag")
def get_tag(tag_id: UUID, book: ReadableBook, response: Response) -> TagOut:
    set_revision(response, book)
    return TagOut.of(book.tag(tag_id))


@router.patch("/{tag_id}", response_model=TagOut, summary="Update a tag")
def update_tag(
    tag_id: UUID,
    body: TagPatch,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> TagOut:
    fields = body.model_dump(exclude_unset=True)
    result = book.execute(
        cmd.UpdateTag(
            tag_id=tag_id,
            name=fields.get("name", UNSET),
            notes=fields.get("notes", UNSET),
            colour=fields.get("colour", UNSET),
            closed=fields.get("closed", UNSET),
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return TagOut.of(result.entity)


@router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unused tag",
)
def delete_tag(tag_id: UUID, book: WritableBook, key: IdempotencyKey) -> None:
    book.execute(cmd.DeleteTag(tag_id=tag_id, command_id=key))


__all__ = ["router"]
