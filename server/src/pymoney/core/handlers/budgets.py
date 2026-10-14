"""Command handlers for budgets.

One budget per account.  ``SetBudget`` writes a plan -- either the whole of it
or just the months named -- and creates the budget the first time a figure is
typed.  The period table's inline editors both go through it: one cell is a
one-entry merge, and the "same every month" input fills the months on screen
without disturbing the ones that are not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from .. import commands as cmd
from ..account import Account
from ..budget import Budget, is_month
from ..changelog import EntityKind
from ..commodity import Commodity
from ..errors import NotFoundError, ValidationError
from ..ids import new_id
from ..rounding import to_fraction

if TYPE_CHECKING:  # pragma: no cover
    from ..book import Book


def _minor(value: object, currency: Commodity) -> int:
    scaled = to_fraction(value) * currency.minor_unit_factor  # type: ignore[arg-type]
    return round(scaled)


def _budgetable(book: Book, account_id: UUID) -> Account:
    """The account a plan is set on, or why there can be no plan.

    A plan is an allowance for what an account may move, so it needs an
    account that can move something: a placeholder only groups its children
    and a closed account keeps its history and takes nothing new.
    """
    account = book.account(account_id)
    if account.placeholder:
        raise ValidationError(
            f"{book.path(account)!r} is a placeholder and takes no budget"
        )
    if account.closed:
        raise ValidationError(f"{book.path(account)!r} is closed and takes no budget")
    return account


def _currency(book: Book, command: object, existing: Budget | None) -> Commodity:
    currency_id = getattr(command, "currency_id", None)
    if currency_id is not None:
        return book.commodity(currency_id)
    return existing.currency if existing is not None else book.base_currency


def _plan(budget: Budget) -> tuple:
    """The fields that matter for "did it change?" -- identity and audit out."""
    return (budget.account_id, budget.currency.id, budget.months)


def set_budget(book: Book, command: cmd.SetBudget) -> Budget:
    account = _budgetable(book, command.account_id)
    existing = book.budget_for(account.id)
    currency = _currency(book, command, existing)

    written: dict[str, int | None] = {}
    for key, amount in command.months.items():
        if not is_month(key):
            raise ValidationError(f"budget month must be YYYY-MM, got {key!r}")
        written[key] = None if amount is None else _minor(amount, currency)
    if command.replace and any(value is None for value in written.values()):
        raise ValidationError(
            "a whole plan cannot remove a single month; merge the months instead"
        )

    months: dict[str, int] = (
        {} if command.replace else dict(existing.months if existing else {})
    )
    for key, minor in written.items():
        if minor is None:
            months.pop(key, None)
        else:
            months[key] = minor

    # A plan with nothing in it is no plan at all. Clearing the last month
    # therefore removes the budget rather than leaving an empty shell behind.
    if not months:
        if existing is None:
            raise NotFoundError(f"no budget for account {account.id}")
        return book.drop(EntityKind.BUDGET, existing.id)

    budget = Budget(
        id=(existing.id if existing is not None else command.id) or new_id(),
        book_id=book.id,
        account_id=account.id,
        currency=currency,
        months=months,
    )
    if existing is not None and _plan(budget) == _plan(existing):
        return existing
    return book.put(EntityKind.BUDGET, budget, created=existing is None)


def delete_budget(book: Book, command: cmd.DeleteBudget) -> Budget:
    existing = book.budget_for(command.account_id)
    if existing is None:
        raise NotFoundError(f"no budget for account {command.account_id}")
    return book.drop(EntityKind.BUDGET, existing.id)
