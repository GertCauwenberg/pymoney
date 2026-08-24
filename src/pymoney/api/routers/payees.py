"""Payees, and merging them.

Merge is its own endpoint rather than a DELETE-and-reassign, because it is one
command and therefore one revision: a client syncing a 400-transaction merge sees
a single atomic step (decision #13).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ...core import commands as cmd
from ...core.commands import UNSET
from ..deps import IdempotencyKey, ReadableBook, WritableBook
from ..schemas import MergePayeesIn, PayeeIn, PayeeOut, PayeePatch
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}/payees", tags=["payees"])


@router.get("", response_model=list[PayeeOut], summary="List payees")
def list_payees(
    book: ReadableBook,
    response: Response,
    search: str | None = Query(
        default=None, description="Case-insensitive substring of the name."
    ),
) -> list[PayeeOut]:
    set_revision(response, book)
    needle = (search or "").strip().casefold()
    return [
        PayeeOut.of(p) for p in book.payees() if not needle or needle in p.name.casefold()
    ]


@router.post(
    "",
    response_model=PayeeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a payee",
)
def create_payee(
    body: PayeeIn, book: WritableBook, response: Response, key: IdempotencyKey
) -> PayeeOut:
    result = book.execute(
        cmd.CreatePayee(
            name=body.name,
            notes=body.notes,
            address=body.address,
            match_keys=tuple(body.match_keys),
            default_account_id=body.default_account_id,
            command_id=key,
        )
    )
    set_revision(response, book)
    return PayeeOut.of(result.entity)


@router.get("/{payee_id}", response_model=PayeeOut, summary="One payee")
def get_payee(payee_id: UUID, book: ReadableBook, response: Response) -> PayeeOut:
    set_revision(response, book)
    return PayeeOut.of(book.payee(payee_id))


@router.patch("/{payee_id}", response_model=PayeeOut, summary="Update a payee")
def update_payee(
    payee_id: UUID,
    body: PayeePatch,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> PayeeOut:
    fields = body.model_dump(exclude_unset=True)
    result = book.execute(
        cmd.UpdatePayee(
            payee_id=payee_id,
            name=fields.get("name", UNSET),
            notes=fields.get("notes", UNSET),
            address=fields.get("address", UNSET),
            match_keys=(tuple(fields["match_keys"]) if "match_keys" in fields else UNSET),
            default_account_id=fields.get("default_account_id", UNSET),
            command_id=key,
        )
    )
    set_revision(response, book)
    return PayeeOut.of(result.entity)


@router.post(
    "/{payee_id}/merge",
    response_model=PayeeOut,
    summary="Merge other payees into this one",
)
def merge_payees(
    payee_id: UUID,
    body: MergePayeesIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> PayeeOut:
    result = book.execute(
        cmd.MergePayees(
            source_ids=tuple(body.source_ids),
            target_id=payee_id,
            keep_match_keys=body.keep_match_keys,
            command_id=key,
        )
    )
    set_revision(response, book)
    return PayeeOut.of(result.entity)


@router.delete(
    "/{payee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unused payee",
)
def delete_payee(payee_id: UUID, book: WritableBook, key: IdempotencyKey) -> None:
    book.execute(cmd.DeletePayee(payee_id=payee_id, command_id=key))


__all__ = ["router"]
