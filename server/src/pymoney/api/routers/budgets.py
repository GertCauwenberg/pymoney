"""Budgets: what an account is *allowed* to move, month by month.

One plan per account. ``PUT`` writes the whole of it; ``PATCH`` writes the
months named and leaves the rest alone, which is what the period-table editors
do -- one cell is a one-entry patch, and the "same every month" input fills the
months on screen without disturbing the ones that are not. A ``null`` amount
removes that month; a plan left with no months is removed. ``DELETE`` removes
one whatever it holds.

The budget *report* is the period table with a plan and a remaining on every
budgeted row; it lives under ``/reports`` with the others.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from ...core import commands as cmd
from ..deps import IdempotencyKey, ReadableBook, WritableBook
from ..schemas import BudgetIn, BudgetMonthsIn, BudgetOut, to_fraction
from ..views import budget_out
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}", tags=["budgets"])


@router.get("/budgets", response_model=list[BudgetOut], summary="List budgets")
def list_budgets(book: ReadableBook, response: Response) -> list[BudgetOut]:
    set_revision(response, book)
    return [budget_out(b) for b in book.budgets()]


@router.get(
    "/accounts/{account_id}/budget",
    response_model=BudgetOut,
    summary="One account's budget",
)
def get_budget(account_id: UUID, book: ReadableBook, response: Response) -> BudgetOut:
    set_revision(response, book)
    book.account(account_id)
    budget = book.budget_for(account_id)
    if budget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no budget for this account"
        )
    return budget_out(budget)


@router.put(
    "/accounts/{account_id}/budget",
    response_model=BudgetOut,
    summary="Set an account's whole budget",
)
def put_budget(
    account_id: UUID,
    body: BudgetIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> BudgetOut:
    result = book.execute(
        cmd.SetBudget(
            account_id=account_id,
            months={ym: to_fraction(a) for ym, a in body.months.items()},
            currency_id=body.currency_id,
            replace=True,
            id=body.id,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return budget_out(result.entity)


@router.patch(
    "/accounts/{account_id}/budget",
    response_model=BudgetOut,
    summary="Set months of an account's budget",
)
def patch_budget(
    account_id: UUID,
    body: BudgetMonthsIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> BudgetOut:
    """Write the months named and leave the rest of the plan alone.

    A `null` amount removes that month. Clearing every month a plan holds
    removes the budget; to remove it regardless of what is in it, use DELETE.
    """
    result = book.execute(
        cmd.SetBudget(
            account_id=account_id,
            months={
                ym: None if a is None else to_fraction(a) for ym, a in body.months.items()
            },
            currency_id=body.currency_id,
            replace=False,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return budget_out(result.entity)


@router.delete(
    "/accounts/{account_id}/budget",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an account's budget",
)
def delete_budget(account_id: UUID, book: WritableBook, key: IdempotencyKey) -> None:
    book.execute(cmd.DeleteBudget(account_id=account_id, command_id=key))


__all__ = ["router"]
