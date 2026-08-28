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

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from ...core import commands as cmd
from ...core.amount import Amount
from ...core.commands import UNSET
from ...core.posting import LegSpec
from ..deps import IdempotencyKey, ReadableBook, WritableBook
from ..schemas import (
    AmountOut,
    LegIn,
    ReconcileIn,
    ReconcileStateName,
    RegisterPage,
    RegisterRowOut,
    RegisterTotals,
    SetSplitStateIn,
    TransactionIn,
    TransactionOut,
    TransactionPage,
    TransactionUpdateIn,
    to_fraction,
)
from ._shared import cursor_key, decode_cursor, encode_cursor, page_size, set_revision

router = APIRouter(prefix="/books/{book_id}", tags=["transactions"])


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
) -> TransactionOut:
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
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return TransactionOut.of(result.entity)


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
)
def update_transaction(
    transaction_id: UUID,
    body: TransactionUpdateIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> TransactionOut:
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
)
def set_split_state(
    transaction_id: UUID,
    split_id: UUID,
    body: SetSplitStateIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> TransactionOut:
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
)
def reconcile_account(
    account_id: UUID,
    body: ReconcileIn,
    book: WritableBook,
    key: IdempotencyKey,
) -> None:
    """One statement, one revision, whatever the number of splits."""
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

    rows = book.register(
        account_id,
        start=start,
        end=end,
        include_children=include_children,
        after=after,
        limit=size + 1,
    )
    has_more = len(rows) > size
    rows = rows[:size]

    # Opening balance: everything strictly before the window. Answered by the
    # pushed-down aggregate, not by walking the ledger.
    account = book.account(account_id)
    opening: Amount | None = None
    running: Amount | None = None
    if not include_children:
        if start is None:
            opening = Amount.zero(book.live(account.commodity))
        else:
            opening = book.balance(account_id, as_of=start - timedelta(days=1))
        # The running balance continues from wherever the previous page
        # stopped, which is the balance *at the cursor*, not at the start of
        # the window -- or every page would start counting from zero again.
        running = opening if after is None else book.balance_at(account_id, after)
    items: list[RegisterRowOut] = []
    single_commodity = len({r.quantity.commodity.id for r in rows}) <= 1
    for row in rows:
        if running is not None and single_commodity:
            running = running + row.quantity
        items.append(
            RegisterRowOut(
                transaction_id=row.transaction.id,
                split_id=row.split.id,
                date=row.date,
                memo=row.memo,
                payee_id=row.split.payee_id or row.transaction.payee_id,
                account_id=row.account_id,
                quantity=AmountOut.of(row.quantity),
                value=AmountOut.of(row.value),
                reconcile_state=ReconcileStateName.of(row.reconcile_state),
                number=row.split.number,
                running_balance=(
                    AmountOut.of(running)
                    if running is not None and single_commodity
                    else None
                ),
            )
        )

    closing = book.balance(account_id, as_of=end) if not include_children else None
    next_cursor = (
        encode_cursor(
            {
                "d": str(rows[-1].date),
                "t": str(rows[-1].transaction.id),
                "s": str(rows[-1].split.id),
            }
        )
        if has_more and rows
        else None
    )
    return RegisterPage(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
        totals=RegisterTotals(
            opening=None if opening is None else AmountOut.of(opening),
            closing=None if closing is None else AmountOut.of(closing),
        ),
    )


__all__ = ["router"]
