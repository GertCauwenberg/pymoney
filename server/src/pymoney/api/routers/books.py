"""Books, and who may see them.

Creating a book grants the creator ``owner`` in the same breath: a book nobody
can read is not a useful outcome of a POST.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import Engine, select

from ...core.commodity import Commodity
from ...store import identity, write_transaction
from ...store.admin import delete_book as delete_book_rows
from ...store.grants import LastOwnerError, grant, revoke
from ...store.identity import normalize_subject
from ..deps import (
    CurrentUser,
    OwnedBook,
    ReadableBook,
    get_engine,
    get_registry,
    readable_books,
    role_on,
)
from ..registry import BookRegistry
from ..schemas import BookIn, BookOut, DeleteBookIn, GrantIn, GrantOut
from ._shared import set_revision

# Starlette renamed this status constant; 422 is the number either way.
UNPROCESSABLE = 422

router = APIRouter(tags=["books"])


@router.get("/books", response_model=list[BookOut], summary="Books you can see")
def list_books(
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(get_engine)],
    registry: Annotated[BookRegistry, Depends(get_registry)],
) -> list[BookOut]:
    roles = readable_books(engine, actor.subject)
    out = []
    # A grant cannot outlive its book: book_grants.book_id is a foreign key with
    # ON DELETE CASCADE, so there is nothing to skip here.
    for book_id, role in sorted(roles.items(), key=lambda item: str(item[0])):
        with registry.open(book_id) as book:
            out.append(BookOut.of(book.settings, book.revision, role))
    return out


@router.post(
    "/books",
    response_model=BookOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a book",
)
def create_book(
    body: BookIn,
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(get_engine)],
    registry: Annotated[BookRegistry, Depends(get_registry)],
) -> BookOut:
    currency = Commodity.currency(
        body.base_currency_code,
        name=body.base_currency_name or body.base_currency_code,
        scale=body.base_currency_scale,
        symbol=body.base_currency_symbol,
    )
    # The owner grant is part of creating the book (see BookRegistry.create):
    # a book nobody can see is not a useful outcome of a POST.
    book = registry.create(
        name=body.name, base_currency=currency, actor=actor.subject, owner=actor.subject
    )
    return BookOut.of(book.settings, book.revision, "owner")


@router.get("/books/{book_id}", response_model=BookOut, summary="One book")
def get_book(
    book_id: UUID,
    book: ReadableBook,
    actor: CurrentUser,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
) -> BookOut:
    set_revision(response, book)
    return BookOut.of(
        book.settings, book.revision, role_on(engine, book_id, actor.subject)
    )


@router.patch("/books/{book_id}", response_model=BookOut, summary="Rename a book")
def rename_book(
    body: BookIn,
    book: OwnedBook,
) -> BookOut:
    from ...core import commands as cmd

    book.execute(cmd.RenameBook(name=body.name))
    return BookOut.of(book.settings, book.revision, "owner")


@router.delete(
    "/books/{book_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a book, irreversibly",
    responses={422: {"description": "The body did not name the book."}},
)
def delete_book(
    book_id: UUID,
    body: DeleteBookIn,
    book: OwnedBook,
    engine: Annotated[Engine, Depends(get_engine)],
    registry: Annotated[BookRegistry, Depends(get_registry)],
) -> None:
    """Everything goes: accounts, transactions, prices, the change log, and
    every grant. There is no undo, which is why the request has to repeat the
    book's name -- a client cannot do this by accident."""
    if body.name.strip() != book.name:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=f"to delete this book, send its exact name ({book.name!r}) to confirm",
        )
    # Under the registry's lock (held by the dependency above), so no request
    # is using the Book while its rows go; then forget it, so the registry never
    # hands out the closed object again. A later request finds no book: 404.
    with write_transaction(engine) as conn:
        delete_book_rows(conn, book_id)
    registry.forget(book_id)


# -- access control --------------------------------------------------------
@router.get(
    "/books/{book_id}/grants",
    response_model=list[GrantOut],
    summary="Who can see this book",
)
def list_grants(
    book_id: UUID,
    book: OwnedBook,
    engine: Annotated[Engine, Depends(get_engine)],
) -> list[GrantOut]:
    del book
    with engine.connect() as conn:
        rows = conn.execute(
            select(identity.book_grants)
            .where(identity.book_grants.c.book_id == book_id)
            .order_by(identity.book_grants.c.subject)
        )
        return [
            GrantOut(book_id=row.book_id, subject=row.subject, role=row.role)
            for row in rows
        ]


@router.put(
    "/books/{book_id}/grants",
    response_model=GrantOut,
    summary="Grant or change access",
)
def put_grant(
    book_id: UUID,
    body: GrantIn,
    book: OwnedBook,
    engine: Annotated[Engine, Depends(get_engine)],
) -> GrantOut:
    del book
    subject = normalize_subject(body.subject)
    if not subject:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail="subject must not be empty",
        )
    try:
        with write_transaction(engine) as conn:
            grant(conn, book_id, subject, body.role)
    except LastOwnerError as exc:
        # Demoting the only owner orphans the book as surely as revoking them.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return GrantOut(book_id=book_id, subject=subject, role=body.role)


@router.delete(
    "/books/{book_id}/grants/{subject}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke access",
)
def delete_grant(
    book_id: UUID,
    subject: str,
    book: OwnedBook,
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(get_engine)],
) -> None:

    del book, actor
    try:
        with write_transaction(engine) as conn:
            revoke(conn, book_id, subject)
    except LastOwnerError as exc:
        # A book with no owner can never be administered again.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


__all__ = ["router"]
