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
from ...core.reports import balance_sheet, holdings, income_statement
from ..deps import ReadableBook
from ..schemas import (
    AmountOut,
    BalanceSheetOut,
    ConversionOut,
    HoldingOut,
    HoldingsOut,
    IncomeStatementOut,
    RateOut,
    ReportRowOut,
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


def _rows(rows) -> list[ReportRowOut]:
    return [
        ReportRowOut(
            account_id=row.account.id,
            path=row.path,
            depth=row.depth,
            value=AmountOut.of(row.value),
            display_value=AmountOut.of(row.display_value),
            own_value=AmountOut.of(row.own_value),
        )
        for row in rows
    ]


def _conversions(conversions) -> list[ConversionOut]:
    return [
        ConversionOut(
            base=c.base.code,
            quote=c.quote.code,
            rate=RateOut.of(c.rate),
            hops=c.hops,
            path=[c.base.code] + [p.quote.code for p in c.prices],
            sources=sorted({p.source for p in c.prices}),
        )
        for c in conversions
    ]


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
    sheet = balance_sheet(book, policy, include_closed=include_closed, strict=strict)
    return BalanceSheetOut(
        as_of=sheet.as_of,
        target=sheet.target.code,
        assets=AmountOut.of(sheet.assets),
        liabilities=AmountOut.of(sheet.liabilities),
        liabilities_owed=AmountOut.of(sheet.liabilities_owed),
        equity=AmountOut.of(sheet.equity),
        net_worth=AmountOut.of(sheet.net_worth),
        rows=_rows(sheet.rows),
        conversions=_conversions(sheet.conversions),
        unpriced=[c.code for c in sheet.unpriced],
    )


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
    report = income_statement(
        book, policy, start=start, include_closed=include_closed, strict=strict
    )
    return IncomeStatementOut(
        start=report.start,
        end=report.end,
        target=report.target.code,
        income=AmountOut.of(report.income),
        income_earned=AmountOut.of(report.income_earned),
        expenses=AmountOut.of(report.expenses),
        net=AmountOut.of(report.net),
        savings_rate=(
            None if report.savings_rate is None else RateOut.of(report.savings_rate)
        ),
        rows=_rows(report.rows),
        conversions=_conversions(report.conversions),
        unpriced=[c.code for c in report.unpriced],
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
    report = holdings(book, policy, include_empty=include_empty, strict=strict)
    return HoldingsOut(
        as_of=report.as_of,
        target=report.target.code,
        positions=[
            HoldingOut(
                account_id=p.account.id,
                path=p.path,
                commodity=p.commodity.code,
                quantity=AmountOut.of(p.quantity),
                cost=AmountOut.of(p.cost),
                market_value=AmountOut.of(p.market_value),
                # Only meaningful when both sides are in one commodity; otherwise
                # it would mix exchange-rate movement with performance.
                unrealised_gain=(
                    AmountOut.of(p.unrealised_gain)
                    if p.cost.commodity.id == p.market_value.commodity.id
                    else None
                ),
                unit_price=None if p.unit_price is None else RateOut.of(p.unit_price),
            )
            for p in report.positions
        ],
        total_value=AmountOut.of(report.total_value),
        total_cost=AmountOut.of(report.total_cost),
    )


__all__ = ["router"]
