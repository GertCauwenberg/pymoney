"""Read models assembled from a :class:`~pymoney.core.book.Book`.

The routers used to build these inline. They are here because the desktop
client's offline mode answers the same questions from a local replica, and two
copies of "how a register page is put together" would drift. A router is now:
authorise, parse the query, call a view, stamp the revision.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from ..core.amount import Amount, Balances, RationalAmount
from ..core.book import Book
from ..core.price import ValuationPolicy
from ..core.reports import Valuer, balance_sheet, holdings, income_statement
from .routers._shared import encode_cursor
from .schemas import (
    AccountBalanceOut,
    AccountOut,
    AmountOut,
    BalanceSheetOut,
    ConversionOut,
    HoldingOut,
    HoldingsOut,
    IncomeStatementOut,
    RateOut,
    ReconcileStateName,
    RegisterPage,
    RegisterRowOut,
    RegisterTotals,
    ReportRowOut,
)

__all__ = [
    "account_balance",
    "account_out",
    "balance_sheet_out",
    "conversions_out",
    "register_page",
    "report_rows",
]


def account_out(book: Book, account_id: UUID) -> AccountOut:
    account = book.account(account_id)
    return AccountOut.of(account, book.path(account.id))


def account_balance(
    book: Book, account_id: UUID, *, as_of: date | None = None
) -> AccountBalanceOut:
    return AccountBalanceOut(
        account_id=account_id,
        path=book.path(account_id),
        balance=AmountOut.of(book.balance(account_id, as_of=as_of)),
        subtree=[AmountOut.of(a) for a in book.subtree_balances(account_id, as_of=as_of)],
    )


def register_page(
    book: Book,
    account_id: UUID,
    *,
    start: date | None,
    end: date | None,
    include_children: bool,
    after: tuple[date, UUID, UUID] | None,
    size: int,
) -> RegisterPage:
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
    account = book.account(account_id)
    target = book.live(account.commodity)

    if not include_children:
        # One account, one commodity: sums, exactly.
        #
        # Opening balance: everything strictly before the window, answered by the
        # pushed-down aggregate. The running balance continues from wherever the
        # previous page stopped, which is the balance *at the cursor*, not at the
        # start of the window -- or every page would start counting from zero.
        opening = (
            Amount.zero(target)
            if start is None
            else book.balance(account_id, as_of=start - timedelta(days=1))
        )
        running = opening if after is None else book.balance_at(account_id, after)
        items: list[RegisterRowOut] = []
        for row in rows:
            running = running + row.quantity
            items.append(_row_out(row, AmountOut.of(running)))
        totals = RegisterTotals(
            opening=AmountOut.of(opening),
            closing=AmountOut.of(book.balance(account_id, as_of=end)),
        )
    else:
        # A subtree may hold several commodities. The running balance is then a
        # *valuation* in the account's own commodity, every leg translated at one
        # rate -- the window's end date, or today -- exactly as the income
        # statement translates a period. One rate keeps the figure reproducible,
        # and makes opening, closing and the balance at a cursor plain aggregates
        # times a rate rather than a walk over every earlier row. A sub-account
        # whose commodity has no price stops the running balance rather than
        # faking one; the response says which.
        policy = ValuationPolicy(target=target, as_of=end or book.clock.today())
        valuer = Valuer(book, policy, strict=False)
        before = (
            Balances()
            if start is None
            else book.subtree_balances(account_id, as_of=start - timedelta(days=1))
        )
        at_cursor = (
            before if after is None else book.subtree_balance_at(account_id, after)
        )
        running_exact = valuer.value(at_cursor).value
        items = []
        stopped = False
        for row in rows:
            conversion = valuer.conversion(row.quantity.commodity)
            if conversion is None or stopped:
                stopped = True
                items.append(_row_out(row, None))
                continue
            running_exact += conversion.apply(row.quantity).value
            items.append(
                _row_out(row, AmountOut.of(RationalAmount(target, running_exact).book()))
            )
        opening_value = valuer.value_amount(before)
        closing_value = valuer.value_amount(book.subtree_balances(account_id, as_of=end))
        # Totals are only honest if everything in them could be translated.
        unpriced = [c.code for c in valuer.unpriced]
        totals = RegisterTotals(
            opening=None if unpriced else AmountOut.of(opening_value),
            closing=None if unpriced else AmountOut.of(closing_value),
            translated=bool(valuer.conversions) or bool(unpriced),
            conversions=conversions_out(valuer.conversions),
            unpriced=unpriced,
        )

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
        items=items, next_cursor=next_cursor, has_more=has_more, totals=totals
    )


def _row_out(row, running: AmountOut | None) -> RegisterRowOut:
    return RegisterRowOut(
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
        running_balance=running,
    )


def report_rows(rows) -> list[ReportRowOut]:
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


def conversions_out(conversions) -> list[ConversionOut]:
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


def balance_sheet_out(
    book: Book, policy: ValuationPolicy, *, include_closed: bool, strict: bool
) -> BalanceSheetOut:
    sheet = balance_sheet(book, policy, include_closed=include_closed, strict=strict)
    return BalanceSheetOut(
        as_of=sheet.as_of,
        target=sheet.target.code,
        assets=AmountOut.of(sheet.assets),
        liabilities=AmountOut.of(sheet.liabilities),
        liabilities_owed=AmountOut.of(sheet.liabilities_owed),
        equity=AmountOut.of(sheet.equity),
        net_worth=AmountOut.of(sheet.net_worth),
        rows=report_rows(sheet.rows),
        conversions=conversions_out(sheet.conversions),
        unpriced=[c.code for c in sheet.unpriced],
    )


def income_statement_out(
    book: Book,
    policy: ValuationPolicy,
    *,
    start: date,
    include_closed: bool,
    strict: bool,
) -> IncomeStatementOut:
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
        rows=report_rows(report.rows),
        conversions=conversions_out(report.conversions),
        unpriced=[c.code for c in report.unpriced],
    )


def holdings_out(
    book: Book, policy: ValuationPolicy, *, include_empty: bool, strict: bool
) -> HoldingsOut:
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
