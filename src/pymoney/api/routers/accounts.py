"""The account tree.

Note what is *not* here: a generic PATCH that can change anything. Renaming,
moving, closing, changing the commodity and merging each have their own endpoint,
because each has its own rules and its own way of failing -- a move can create a
cycle, a close needs its children closed first, a commodity change is refused
outright once money has been posted. Folding them into one PATCH would mean one
handler with five unrelated failure modes and a body where every field is
optional.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ...core import commands as cmd
from ...core.account import AccountClass, AccountType
from ...core.commands import UNSET
from ..deps import IdempotencyKey, ReadableBook, WritableBook
from ..schemas import (
    AccountBalanceOut,
    AccountIn,
    AccountOut,
    AccountPatch,
    AmountOut,
    MergeAccountsIn,
    MoveAccountIn,
    RenameIn,
    SetClosedIn,
    SetCommodityIn,
)
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}/accounts", tags=["accounts"])


def _out(book, account) -> AccountOut:
    return AccountOut.of(account, book.path(account.id))


@router.get("", response_model=list[AccountOut], summary="List accounts")
def list_accounts(
    book: ReadableBook,
    response: Response,
    account_class: AccountClass | None = Query(default=None),
    type: AccountType | None = Query(default=None),
    include_closed: bool = Query(default=True),
) -> list[AccountOut]:
    set_revision(response, book)
    return [
        _out(book, a)
        for a in book.accounts(
            account_class=account_class, type=type, include_closed=include_closed
        )
    ]


@router.post(
    "",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
def create_account(
    body: AccountIn, book: WritableBook, response: Response, key: IdempotencyKey
) -> AccountOut:
    result = book.execute(
        cmd.CreateAccount(
            name=body.name,
            type=body.type,
            parent_id=body.parent_id,
            commodity_id=body.commodity_id,
            description=body.description,
            code=body.code,
            placeholder=body.placeholder,
            notes=body.notes,
            institution=body.institution,
            iban=body.iban,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return _out(book, result.entity)


@router.get("/{account_id}", response_model=AccountOut, summary="One account")
def get_account(account_id: UUID, book: ReadableBook, response: Response) -> AccountOut:
    set_revision(response, book)
    return _out(book, book.account(account_id))


@router.patch(
    "/{account_id}", response_model=AccountOut, summary="Update account metadata"
)
def update_account(
    account_id: UUID,
    body: AccountPatch,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> AccountOut:
    fields = body.model_dump(exclude_unset=True)
    result = book.execute(
        cmd.UpdateAccount(
            account_id=account_id,
            description=fields.get("description", UNSET),
            code=fields.get("code", UNSET),
            notes=fields.get("notes", UNSET),
            institution=fields.get("institution", UNSET),
            iban=fields.get("iban", UNSET),
            placeholder=fields.get("placeholder", UNSET),
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return _out(book, result.entity)


@router.post("/{account_id}/rename", response_model=AccountOut, summary="Rename")
def rename_account(
    account_id: UUID,
    body: RenameIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> AccountOut:
    result = book.execute(
        cmd.RenameAccount(account_id=account_id, name=body.name, command_id=key)
    )
    set_revision(response, book, result)
    return _out(book, result.entity)


@router.post(
    "/{account_id}/move",
    response_model=AccountOut,
    summary="Re-parent, keeping the history",
)
def move_account(
    account_id: UUID,
    body: MoveAccountIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> AccountOut:
    result = book.execute(
        cmd.MoveAccount(
            account_id=account_id, new_parent_id=body.parent_id, command_id=key
        )
    )
    set_revision(response, book, result)
    return _out(book, result.entity)


@router.post("/{account_id}/closed", response_model=AccountOut, summary="Close or reopen")
def set_closed(
    account_id: UUID,
    body: SetClosedIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> AccountOut:
    result = book.execute(
        cmd.SetAccountClosed(account_id=account_id, closed=body.closed, command_id=key)
    )
    set_revision(response, book, result)
    return _out(book, result.entity)


@router.post(
    "/{account_id}/commodity",
    response_model=AccountOut,
    summary="Correct the commodity of an unused account",
)
def set_commodity(
    account_id: UUID,
    body: SetCommodityIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> AccountOut:
    result = book.execute(
        cmd.SetAccountCommodity(
            account_id=account_id, commodity_id=body.commodity_id, command_id=key
        )
    )
    set_revision(response, book, result)
    return _out(book, result.entity)


@router.post(
    "/{account_id}/merge",
    response_model=AccountOut,
    summary="Merge another account into this one",
)
def merge_accounts(
    account_id: UUID,
    body: MergeAccountsIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> AccountOut:
    result = book.execute(
        cmd.MergeAccounts(source_id=body.source_id, target_id=account_id, command_id=key)
    )
    set_revision(response, book, result)
    return _out(book, result.entity)


@router.delete(
    "/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an empty account",
)
def delete_account(account_id: UUID, book: WritableBook, key: IdempotencyKey) -> None:
    book.execute(cmd.DeleteAccount(account_id=account_id, command_id=key))


@router.get(
    "/{account_id}/balance",
    response_model=AccountBalanceOut,
    summary="Balance, on its own and with descendants",
)
def account_balance(
    account_id: UUID,
    book: ReadableBook,
    response: Response,
    as_of: date | None = Query(
        default=None, description="Balance at the end of this date. Defaults to all."
    ),
) -> AccountBalanceOut:
    set_revision(response, book)
    return AccountBalanceOut(
        account_id=account_id,
        path=book.path(account_id),
        balance=AmountOut.of(book.balance(account_id, as_of=as_of)),
        subtree=[AmountOut.of(a) for a in book.subtree_balances(account_id, as_of=as_of)],
    )


__all__ = ["router"]
