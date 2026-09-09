"""Command handlers for tags.

A tag is a small named lookup entity, like a payee: created once, referenced by
id from any number of splits, renamed in a single row.  It cannot be deleted
while a transaction still wears it -- there is no "merge" to fall back on, so a
blind delete would silently orphan history."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .. import commands as cmd
from ..changelog import EntityKind
from ..errors import ConflictError
from ..ids import new_id
from ..tag import Tag

if TYPE_CHECKING:  # pragma: no cover
    from ..book import Book


def create_tag(book: Book, command: cmd.CreateTag) -> Tag:
    existing = book.find_tag(command.name)
    if existing is not None:
        raise ConflictError(f"tag {command.name!r} already exists as {existing.name!r}")
    tag = Tag(
        id=command.id or new_id(),
        book_id=book.id,
        name=command.name,
        notes=command.notes,
        colour=command.colour,
        closed=command.closed,
    )
    if book.store.exists(EntityKind.TAG, tag.id):
        raise ConflictError(f"tag {tag.id} already exists")
    return book.put(EntityKind.TAG, tag, created=True)


def update_tag(book: Book, command: cmd.UpdateTag) -> Tag:
    current = book.tag(command.tag_id)
    updated = current
    if not isinstance(command.name, cmd.Unset):
        other = book.find_tag(command.name)
        if other is not None and other.id != current.id:
            raise ConflictError(f"tag {command.name!r} already exists")
        updated = replace(updated, name=command.name)
    for field_name in ("notes", "colour", "closed"):
        value = getattr(command, field_name)
        if not isinstance(value, cmd.Unset):
            updated = replace(updated, **{field_name: value})
    if updated == current:
        return current
    return book.put(EntityKind.TAG, updated)


def delete_tag(book: Book, command: cmd.DeleteTag) -> Tag:
    tag = book.tag(command.tag_id)
    users = book.store.transactions_for_tag(tag.id)
    if users:
        raise ConflictError(
            f"tag {tag.name!r} is on {len(users)} transaction(s); remove it "
            f"from them first, or close the tag to hide it"
        )
    return book.drop(EntityKind.TAG, tag.id)
