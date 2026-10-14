"""Reports.

Every report takes an explicit valuation policy in its query string (decision
#12), so the same request always produces the same numbers. ``known_before`` is
the one to notice: with it, last quarter's report can be reproduced exactly even
after corrected quotes have arrived.

Each response carries the conversions it used, so a total can be traced to the
rates behind it rather than taken on trust.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Response

from ...core.price import ValuationPolicy
from ..deps import ReadableBook
from ..schemas import (
    BalanceSheetOut,
    BudgetTableOut,
    HoldingsOut,
    IncomeStatementOut,
    NetWorthSeriesOut,
    PeriodTableOut,
    ReportInterval,
)
from ..views import (
    balance_sheet_out,
    budget_table_out,
    holdings_out,
    income_statement_out,
    net_worth_series_out,
    period_table_out,
)
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}/reports", tags=["reports"])


def _policy(
    book,
    as_of: date | None,
    target_id: UUID | None,
    known_before: datetime | None,
    sources: list[str] | None,
    max_staleness_days: int | None,
) -> ValuationPolicy:
    return ValuationPolicy(
        target=book.commodity(target_id) if target_id else book.base_currency,
        as_of=as_of or book.clock.today(),
        knowledge_time=known_before,
        sources=tuple(sources or ()),
        max_staleness_days=max_staleness_days,
    )


@router.get("/balance-sheet", response_model=BalanceSheetOut, summary="Balance sheet")
def get_balance_sheet(
    book: ReadableBook,
    response: Response,
    as_of: date | None = Query(default=None),
    target_id: UUID | None = Query(
        default=None, description="Report currency. Defaults to the book's."
    ),
    known_before: datetime | None = Query(default=None),
    sources: list[str] | None = Query(default=None),
    max_staleness_days: int | None = Query(default=None, ge=0),
    include_closed: bool = Query(default=False),
    strict: bool = Query(
        default=True,
        description=(
            "Fail if something cannot be valued. Off: list it in `unpriced` and "
            "leave it out of the totals."
        ),
    ),
) -> BalanceSheetOut:
    set_revision(response, book)
    policy = _policy(book, as_of, target_id, known_before, sources, max_staleness_days)
    return balance_sheet_out(book, policy, include_closed=include_closed, strict=strict)


@router.get(
    "/income-statement",
    response_model=IncomeStatementOut,
    summary="Income and expenses over a period",
)
def get_income_statement(
    book: ReadableBook,
    response: Response,
    start: date,
    end: date | None = Query(
        default=None,
        description="Inclusive, and also the valuation date for translation.",
    ),
    target_id: UUID | None = Query(default=None),
    known_before: datetime | None = Query(default=None),
    sources: list[str] | None = Query(default=None),
    max_staleness_days: int | None = Query(default=None, ge=0),
    include_closed: bool = Query(default=False),
    strict: bool = Query(default=True),
) -> IncomeStatementOut:
    set_revision(response, book)
    policy = _policy(book, end, target_id, known_before, sources, max_staleness_days)
    return income_statement_out(
        book, policy, start=start, include_closed=include_closed, strict=strict
    )


@router.get("/holdings", response_model=HoldingsOut, summary="Security positions")
def get_holdings(
    book: ReadableBook,
    response: Response,
    as_of: date | None = Query(default=None),
    target_id: UUID | None = Query(default=None),
    known_before: datetime | None = Query(default=None),
    include_empty: bool = Query(default=False),
    strict: bool = Query(default=True),
) -> HoldingsOut:
    set_revision(response, book)
    policy = _policy(book, as_of, target_id, known_before, None, None)
    return holdings_out(book, policy, include_empty=include_empty, strict=strict)


@router.get(
    "/net-worth",
    response_model=NetWorthSeriesOut,
    summary="Net worth at the end of every period",
)
def get_net_worth(
    book: ReadableBook,
    response: Response,
    start: date,
    end: date | None = Query(default=None, description="Inclusive; defaults to today."),
    interval: ReportInterval = Query(default="month"),
    target_id: UUID | None = Query(default=None),
    known_before: datetime | None = Query(default=None),
    sources: list[str] | None = Query(default=None),
    max_staleness_days: int | None = Query(default=None, ge=0),
    strict: bool = Query(default=True),
) -> NetWorthSeriesOut:
    """Each point is valued at its own date: what the book was worth then."""
    set_revision(response, book)
    policy = _policy(book, end, target_id, known_before, sources, max_staleness_days)
    return net_worth_series_out(
        book, policy, start=start, interval=interval, strict=strict
    )


@router.get(
    "/period-table",
    response_model=PeriodTableOut,
    summary="Income and expenses per account, per period",
)
def get_period_table(
    book: ReadableBook,
    response: Response,
    start: date,
    end: date | None = Query(
        default=None,
        description="Inclusive, and also the valuation date for translation.",
    ),
    interval: ReportInterval = Query(default="month"),
    target_id: UUID | None = Query(default=None),
    known_before: datetime | None = Query(default=None),
    sources: list[str] | None = Query(default=None),
    max_staleness_days: int | None = Query(default=None, ge=0),
    include_closed: bool = Query(default=False),
    strict: bool = Query(default=True),
) -> PeriodTableOut:
    """Every column is translated at one rate -- the end date -- like the income statement."""
    set_revision(response, book)
    policy = _policy(book, end, target_id, known_before, sources, max_staleness_days)
    return period_table_out(
        book,
        policy,
        start=start,
        interval=interval,
        include_closed=include_closed,
        strict=strict,
    )


@router.get(
    "/budget",
    response_model=BudgetTableOut,
    summary="The period table with a plan and a remaining per account",
)
def get_budget_report(
    book: ReadableBook,
    response: Response,
    start: date,
    end: date | None = Query(
        default=None,
        description="Inclusive, and also the valuation date for translation.",
    ),
    interval: ReportInterval = Query(
        default="month",
        description="Budgets are monthly; a quarter or year column sums the months it spans.",
    ),
    target_id: UUID | None = Query(default=None),
    known_before: datetime | None = Query(default=None),
    sources: list[str] | None = Query(default=None),
    max_staleness_days: int | None = Query(default=None, ge=0),
    include_closed: bool = Query(default=False),
    strict: bool = Query(default=False),
) -> BudgetTableOut:
    """What was spent beside what was allowed. Each budget is converted to the
    report's currency under the same policy as the actuals."""
    set_revision(response, book)
    policy = _policy(book, end, target_id, known_before, sources, max_staleness_days)
    return budget_table_out(
        book,
        policy,
        start=start,
        interval=interval,
        include_closed=include_closed,
        strict=strict,
    )


__all__ = ["router"]
