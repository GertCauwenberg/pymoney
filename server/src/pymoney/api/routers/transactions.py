"""Transactions and registers.

``POST /transactions`` is a command, not a CRUD create: the body is legs
expressed as exact rationals and the server decides how they book. That is the
only honest shape -- the client cannot know where a rounding remainder lands
without reimplementing the allocator, so it does not try.

Splits are never patched individually. An update replaces the whole set of legs,
because a partial edit can leave a transaction unbalanced and an unbalanced
transaction must not exist even transiently.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Engine

from ...core import commands as cmd
from ...core.commands import UNSET
from ...core.posting import LegSpec
from ...core.store import TransactionSearch
from ..deps import (
    CurrentUser,
    IdempotencyKey,
    ReadableBook,
    WritableBook,
    get_engine,
    role_on,
)
from ..schemas import (
    Exact,
    LegIn,
    ReconcileIn,
    ReconcileStateName,
    RegisterPage,
    SetSplitStateIn,
    TransactionIn,
    TransactionOut,
    TransactionPage,
    TransactionSearchOut,
    TransactionUpdateIn,
    to_fraction,
)
from ..views import register_page, search_transactions
from ._shared import cursor_key, decode_cursor, encode_cursor, page_size, set_revision

router = APIRouter(prefix="/books/{book_id}", tags=["transactions"])

# Starlette renamed this status constant; 422 is the number either way.
UNPROCESSABLE = 422

FROZEN_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {
        "description": (
            "Only an owner may mark a split FROZEN: it can never be undone, and "
            "the transaction can never again be edited or deleted by anyone."
        )
    },
}


def _guard_frozen(
    states: Iterable[ReconcileStateName],
    book_id: UUID,
    actor: CurrentUser,
    engine: Engine,
    *,
    posting: bool = False,
) -> None:
    """FROZEN is the one reconciliation state with no way back (decision #11),
    so it is not an editor's to set: an editor -- or a buggy client -- would be
    able to make a transaction that nobody, owner included, can ever touch
    again. Closing a period is the owner's call, and it is done to transactions
    that exist: a new posting arrives no more than RECONCILED."""
    if ReconcileStateName.FROZEN not in set(states):
        return
    if posting:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=(
                "a transaction cannot be posted FROZEN; post it, then freeze it "
                "through the reconcile endpoints"
            ),
        )
    if role_on(engine, book_id, actor.subject) != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "marking a split FROZEN is irreversible and needs owner on the "
                "book; an editor may go as far as RECONCILED"
            ),
        )


def _legs(legs: list[LegIn]) -> tuple[LegSpec, ...]:
    return tuple(
        LegSpec(
            account_id=leg.account_id,
            value=None if leg.value is None else to_fraction(leg.value),
            quantity=None if leg.quantity is None else to_fraction(leg.quantity),
            price=None if leg.price is None else to_fraction(leg.price),
            memo=leg.memo,
            payee_id=leg.payee_id,
            action=leg.action,
            number=leg.number,
            reconcile_state=leg.reconcile_state.to_domain(),
            reconcile_date=leg.reconcile_date,
            tag_ids=tuple(leg.tag_ids),
        )
        for leg in legs
    )


@router.get("/transactions", response_model=TransactionPage, summary="List transactions")
def list_transactions(
    book: ReadableBook,
    response: Response,
    account_id: UUID | None = Query(default=None),
    payee_id: UUID | None = Query(default=None),
    start: date | None = Query(default=None, description="Inclusive post date."),
    end: date | None = Query(default=None, description="Inclusive post date."),
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
) -> TransactionPage:
    set_revision(response, book)
    size = page_size(limit, 100, 500)
    position = decode_cursor(cursor)
    after = None
    if position is not None:
        try:
            after = (date.fromisoformat(position["d"]), UUID(position["t"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="malformed cursor"
            ) from exc
    # One more than the page, to know whether there is a next one.
    window = book.transactions(
        account_id=account_id,
        payee_id=payee_id,
        start=start,
        end=end,
        after=after,
        limit=size + 1,
    )
    has_more = len(window) > size
    window = window[:size]
    next_cursor = (
        encode_cursor({"d": str(window[-1].date), "t": str(window[-1].id)})
        if has_more and window
        else None
    )
    return TransactionPage(
        items=[TransactionOut.of(t) for t in window],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/transactions",
    response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Post a transaction",
)
def post_transaction(
    body: TransactionIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(get_engine)],
) -> TransactionOut:
    _guard_frozen(
        (leg.reconcile_state for leg in body.legs), book.id, actor, engine, posting=True
    )
    result = book.execute(
        cmd.PostTransaction(
            date=body.date,
            legs=_legs(body.legs),
            currency_id=body.currency_id,
            memo=body.memo,
            payee_id=body.payee_id,
            number=body.number,
            rounding=body.rounding,
            imbalance_account_id=body.imbalance_account_id,
            imported_id=body.imported_id,
            id=body.id,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return TransactionOut.of(result.entity)


@router.get(
    "/transactions/search",
    response_model=TransactionSearchOut,
    summary="Find transactions",
)
def search_transactions_route(
    book: ReadableBook,
    response: Response,
    q: str | None = Query(
        default=None,
        max_length=200,
        description="Case-insensitive substring of a memo or number, on the transaction or any split.",
    ),
    amount_min: Exact | None = Query(
        default=None,
        description="Smallest absolute amount, major units; matches a split's quantity or value.",
    ),
    amount_max: Exact | None = Query(
        default=None, description="Largest absolute amount."
    ),
    amount: Exact | None = Query(
        default=None, description="Exactly this amount: shorthand for min = max."
    ),
    start: date | None = Query(default=None, description="Inclusive post date."),
    end: date | None = Query(default=None, description="Inclusive post date."),
    account_id: UUID | None = Query(default=None),
    include_children: bool = Query(
        default=False, description="The account's whole subtree."
    ),
    payee_id: UUID | None = Query(default=None),
    tag_id: UUID | None = Query(
        default=None, description="A tag any of the transaction's legs wears."
    ),
    limit: int | None = Query(default=None, ge=1),
) -> TransactionSearchOut:
    """Every criterion narrows; they combine with AND. At least one is needed.
    Results come newest first, at most `limit` (default 100, at most 500); a
    `has_more` of true means the search should be narrowed."""
    set_revision(response, book)
    if amount is not None:
        amount_min = amount_max = amount
    query = TransactionSearch(
        text=q,
        amount_min=None if amount_min is None else to_fraction(amount_min),
        amount_max=None if amount_max is None else to_fraction(amount_max),
        payee_id=payee_id,
        tag_id=tag_id,
        start=start,
        end=end,
    )
    return search_transactions(
        book,
        query,
        account_id=account_id,
        include_children=include_children,
        limit=page_size(limit, 100, 500),
    )


@router.get(
    "/transactions/{transaction_id}",
    response_model=TransactionOut,
    summary="One transaction",
)
def get_transaction(
    transaction_id: UUID, book: ReadableBook, response: Response
) -> TransactionOut:
    set_revision(response, book)
    return TransactionOut.of(book.transaction(transaction_id))


@router.patch(
    "/transactions/{transaction_id}",
    response_model=TransactionOut,
    summary="Update a transaction",
    responses=FROZEN_RESPONSES,
)
def update_transaction(
    transaction_id: UUID,
    body: TransactionUpdateIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(get_engine)],
) -> TransactionOut:
    if body.legs is not None:
        _guard_frozen((leg.reconcile_state for leg in body.legs), book.id, actor, engine)
    fields = body.model_dump(exclude_unset=True)
    result = book.execute(
        cmd.UpdateTransaction(
            transaction_id=transaction_id,
            date=fields.get("date", UNSET),
            legs=_legs(body.legs) if body.legs is not None else UNSET,
            memo=fields.get("memo", UNSET),
            payee_id=fields.get("payee_id", UNSET),
            number=fields.get("number", UNSET),
            currency_id=fields.get("currency_id", UNSET),
            rounding=fields.get("rounding", UNSET),
            imbalance_account_id=body.imbalance_account_id,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return TransactionOut.of(result.entity)


@router.delete(
    "/transactions/{transaction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a transaction",
)
def delete_transaction(
    transaction_id: UUID, book: WritableBook, key: IdempotencyKey
) -> None:
    book.execute(cmd.DeleteTransaction(transaction_id=transaction_id, command_id=key))


@router.post(
    "/transactions/{transaction_id}/splits/{split_id}/reconcile",
    response_model=TransactionOut,
    summary="Set one split's reconciliation state",
    responses=FROZEN_RESPONSES,
)
def set_split_state(
    transaction_id: UUID,
    split_id: UUID,
    body: SetSplitStateIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(get_engine)],
) -> TransactionOut:
    _guard_frozen((body.state,), book.id, actor, engine)
    result = book.execute(
        cmd.SetSplitReconcileState(
            transaction_id=transaction_id,
            split_id=split_id,
            state=body.state.to_domain(),
            on=body.on,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return TransactionOut.of(result.entity)


@router.post(
    "/accounts/{account_id}/reconcile",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reconcile a batch of splits against a statement",
    responses=FROZEN_RESPONSES,
)
def reconcile_account(
    account_id: UUID,
    body: ReconcileIn,
    book: WritableBook,
    key: IdempotencyKey,
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(get_engine)],
) -> None:
    """One statement, one revision, whatever the number of splits."""
    _guard_frozen((body.state,), book.id, actor, engine)
    book.execute(
        cmd.ReconcileAccount(
            account_id=account_id,
            split_ids=tuple(body.split_ids),
            state=body.state.to_domain(),
            statement_date=body.statement_date,
            command_id=key,
        )
    )


@router.get(
    "/accounts/{account_id}/register",
    response_model=RegisterPage,
    summary="An account register, paginated",
)
def register(
    account_id: UUID,
    book: ReadableBook,
    response: Response,
    start: date | None = Query(default=None, description="Inclusive post date."),
    end: date | None = Query(default=None, description="Inclusive post date."),
    include_children: bool = Query(default=False),
    cursor: str | None = Query(
        default=None, description="Opaque keyset cursor from a previous page."
    ),
    limit: int | None = Query(default=None, ge=1),
) -> RegisterPage:
    set_revision(response, book)
    size = page_size(limit, 100, 500)
    position = decode_cursor(cursor)
    after = None
    if position is not None:
        when, transaction_int, split_int = cursor_key(position)
        after = (when, UUID(int=transaction_int), UUID(int=split_int))

    return register_page(
        book,
        account_id,
        start=start,
        end=end,
        include_children=include_children,
        after=after,
        size=size,
    )


__all__ = ["router"]
