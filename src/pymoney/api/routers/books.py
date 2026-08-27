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
from ...store import identity
from ...store.grants import grant
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
from ..schemas import BookIn, BookOut, GrantIn, GrantOut
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
    book = registry.create(name=body.name, base_currency=currency, actor=actor.subject)
    with engine.begin() as conn:
        grant(conn, book.id, actor.subject, "owner")
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
    subject = body.subject.strip()
    if not subject:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail="subject must not be empty",
        )
    with engine.begin() as conn:
        grant(conn, book_id, subject, body.role)
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

    del book
    with engine.begin() as conn:
        owners = (
            conn.execute(
                select(identity.book_grants.c.subject).where(
                    identity.book_grants.c.book_id == book_id,
                    identity.book_grants.c.role == "owner",
                )
            )
            .scalars()
            .all()
        )
        if subject in owners and len(owners) == 1:
            # A book with no owner can never be administered again.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="this is the last owner; grant another owner first",
            )
        conn.execute(
            identity.book_grants.delete().where(
                identity.book_grants.c.book_id == book_id,
                identity.book_grants.c.subject == subject,
            )
        )
    del actor


__all__ = ["router"]
