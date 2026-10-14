"""Reports (decision #12: a report is a function of a valuation policy).

Nothing here reads the clock or picks a price on its own.  Every report takes a
:class:`~pymoney.core.price.ValuationPolicy`, so the same call against the same
book always produces the same numbers -- which is what makes a report
reproducible, citable and testable.

Reports also hand back the :class:`~pymoney.core.price.Conversion` objects they
used.  "Total net worth 142,301.18 EUR" is only trustworthy if the user can
expand it into "...of which 12,000 USD at 0.9174 from ECB 2026-01-30".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from typing import Literal
from uuid import UUID

from .account import Account, AccountClass, AccountType
from .amount import Amount, Balances, RationalAmount
from .book import Book
from .commodity import Commodity
from .errors import NoPriceError, ValidationError
from .price import Conversion, ValuationPolicy
from .rounding import DEFAULT_ROUNDING, RoundingMode

__all__ = [
    "AccountRow",
    "BalanceSheet",
    "BudgetClassTotals",
    "BudgetRow",
    "BudgetTable",
    "Holding",
    "HoldingsReport",
    "IncomeStatement",
    "Interval",
    "NetWorthPoint",
    "NetWorthSeries",
    "Period",
    "PeriodRow",
    "PeriodTable",
    "Valuer",
    "account_tree",
    "balance_sheet",
    "budget_table",
    "holdings",
    "income_statement",
    "net_worth_series",
    "period_table",
    "periods_between",
]


class Valuer:
    """Converts balances into one target commodity under a fixed policy.

    Caches conversions per commodity so a 5,000-row report does not walk the
    price graph 5,000 times, and keeps the list of conversions actually used so
    the report can show its work.
    """

    __slots__ = ("_cache", "_unpriced", "book", "policy", "strict", "target")

    def __init__(
        self, book: Book, policy: ValuationPolicy, *, strict: bool = True
    ) -> None:
        if policy.target is None:
            raise ValidationError("a valuation policy needs a target commodity")
        self.book = book
        self.policy = policy
        self.target: Commodity = policy.target
        self.strict = strict
        self._cache: dict[UUID, Conversion | None] = {}
        self._unpriced: dict[UUID, Commodity] = {}

    def conversion(self, commodity: Commodity) -> Conversion | None:
        """Cached conversion from ``commodity`` to the target, or None."""
        if commodity.id in self._cache:
            return self._cache[commodity.id]
        try:
            result: Conversion | None = self.book.prices.convert(
                commodity, self.target, self.policy
            )
        except NoPriceError:
            if self.strict:
                raise
            result = None
            self._unpriced[commodity.id] = commodity
        self._cache[commodity.id] = result
        return result

    def value(self, balances: Balances) -> RationalAmount:
        """Exact value of a multi-commodity balance in the target commodity.

        Conversion happens in exact rationals and is summed before rounding, so
        a portfolio's total is not the sum of individually rounded positions.
        """
        total = Fraction(0)
        for amount in balances:
            conversion = self.conversion(amount.commodity)
            if conversion is None:
                continue
            total += conversion.apply(amount).value
        return RationalAmount(self.target, total)

    def value_amount(
        self, balances: Balances, mode: RoundingMode = DEFAULT_ROUNDING
    ) -> Amount:
        """:meth:`value`, booked into the target commodity."""
        return self.value(balances).book(mode)

    @property
    def conversions(self) -> tuple[Conversion, ...]:
        """Every conversion used, for the report's provenance footer."""
        return tuple(
            sorted(
                (c for c in self._cache.values() if c is not None and not c.is_identity),
                key=lambda c: c.base.code,
            )
        )

    @property
    def unpriced(self) -> tuple[Commodity, ...]:
        """Commodities that could not be valued (non-strict mode only)."""
        return tuple(sorted(self._unpriced.values(), key=lambda c: c.code))


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountRow:
    """One line of a hierarchical report."""

    account: Account
    path: str
    depth: int
    own: Balances
    """Balance of this account alone."""
    subtree: Balances
    """Balance of this account plus its descendants."""
    value: Amount
    """Subtree balance converted to the report's target commodity."""
    own_value: Amount
    sign: int
    """+1 or -1; multiply by this to read the figure as a report would show it."""

    @property
    def display_value(self) -> Amount:
        """The subtree value with the account class's natural sign applied."""
        return self.value * self.sign

    @property
    def is_leaf(self) -> bool:
        return self.own == self.subtree

    def __str__(self) -> str:
        return f"{'  ' * self.depth}{self.account.name}: {self.display_value}"


def _subtree_totals(
    book: Book, own: dict[UUID, Amount], roots: Iterable[Account]
) -> dict[UUID, Balances]:
    """Roll ``own`` balances up the tree, visiting each account exactly once."""
    totals: dict[UUID, Balances] = {}

    def visit(account: Account) -> Balances:
        rolled = Balances([own[account.id]])
        for child in book.children(account.id):
            rolled = rolled + visit(child)
        totals[account.id] = rolled
        return rolled

    for root in roots:
        visit(root)
    return totals


def account_tree(
    book: Book,
    policy: ValuationPolicy,
    *,
    account_class: AccountClass | None = None,
    roots: Iterable[Account] | None = None,
    include_closed: bool = False,
    include_empty: bool = True,
    valuer: Valuer | None = None,
    strict: bool = True,
    own_balances: dict[UUID, Amount] | None = None,
) -> tuple[AccountRow, ...]:
    """Depth-first rows for a tree of accounts, valued under ``policy``.

    :param own_balances: precomputed per-account balances, as returned by
        :meth:`~pymoney.core.book.Book.balances_by_account`.  Passed in by
        period reports, which compute balances over a window rather than up to a
        date.
    """
    v = valuer or Valuer(book, policy, strict=strict)
    own = (
        own_balances
        if own_balances is not None
        else book.balances_by_account(end=policy.as_of)
    )

    if roots is None:
        selected = [
            a
            for a in book.roots()
            if account_class is None or a.account_class is account_class
        ]
    else:
        selected = list(roots)

    selected.sort(key=lambda a: a.name)
    totals = _subtree_totals(book, own, selected)
    rows: list[AccountRow] = []

    def emit(account: Account, depth: int) -> None:
        if account.closed and not include_closed:
            return
        subtree = totals[account.id]
        if include_empty or len(subtree) > 0:
            rows.append(
                AccountRow(
                    account=account,
                    path=book.path(account),
                    depth=depth,
                    own=Balances([own[account.id]]),
                    subtree=subtree,
                    value=v.value_amount(subtree),
                    own_value=v.value_amount(Balances([own[account.id]])),
                    sign=account.report_sign,
                )
            )
        for child in book.children(account.id):
            emit(child, depth + 1)

    for root in selected:
        emit(root, 0)
    return tuple(rows)


@dataclass(frozen=True, slots=True, kw_only=True)
class BalanceSheet:
    """Assets, liabilities and equity at a point in time."""

    as_of: date
    target: Commodity
    policy: ValuationPolicy
    rows: tuple[AccountRow, ...]
    assets: Amount
    liabilities: Amount
    equity: Amount
    conversions: tuple[Conversion, ...] = ()
    unpriced: tuple[Commodity, ...] = ()

    @property
    def net_worth(self) -> Amount:
        """Assets minus liabilities, as a positive number when solvent.

        Stored balances are signed, so liabilities are already negative and the
        sum is the right answer; we flip the sign of the *reported* liabilities
        separately for display.
        """
        return self.assets + self.liabilities

    @property
    def liabilities_owed(self) -> Amount:
        """Liabilities as a positive "amount owed"."""
        return -self.liabilities

    @property
    def balances(self) -> bool:
        """True when assets + liabilities + equity nets to zero.

        Only holds for books that track equity explicitly (opening balances as
        equity postings rather than as unbalanced imports).
        """
        return (self.assets + self.liabilities + self.equity).is_zero

    def rows_for(self, account_class: AccountClass) -> tuple[AccountRow, ...]:
        return tuple(r for r in self.rows if r.account.account_class is account_class)

    def __str__(self) -> str:
        return (
            f"Balance sheet {self.as_of} ({self.target.code}): "
            f"assets {self.assets}, liabilities {self.liabilities_owed}, "
            f"net worth {self.net_worth}"
        )


def balance_sheet(
    book: Book,
    policy: ValuationPolicy,
    *,
    include_closed: bool = False,
    strict: bool = True,
) -> BalanceSheet:
    """Assets, liabilities and equity as of ``policy.as_of``."""
    if policy.as_of is None:
        raise ValidationError("a balance sheet needs policy.as_of")
    valuer = Valuer(book, policy, strict=strict)
    own = book.balances_by_account(end=policy.as_of)
    rows = account_tree(
        book,
        policy,
        include_closed=include_closed,
        valuer=valuer,
        own_balances=own,
        roots=[a for a in book.roots() if a.account_class.is_balance_sheet],
    )

    def class_total(account_class: AccountClass) -> Amount:
        return valuer.value_amount(
            Balances(own[a.id] for a in book.accounts(account_class=account_class))
        )

    totals = {
        klass: class_total(klass)
        for klass in (AccountClass.ASSET, AccountClass.LIABILITY, AccountClass.EQUITY)
    }
    return BalanceSheet(
        as_of=policy.as_of,
        target=valuer.target,
        policy=policy,
        rows=rows,
        assets=totals[AccountClass.ASSET],
        liabilities=totals[AccountClass.LIABILITY],
        equity=totals[AccountClass.EQUITY],
        conversions=valuer.conversions,
        unpriced=valuer.unpriced,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class IncomeStatement:
    """Income and expenses over a period."""

    start: date
    end: date
    target: Commodity
    policy: ValuationPolicy
    rows: tuple[AccountRow, ...]
    income: Amount
    """Signed: income is stored negative, so this is normally negative."""
    expenses: Amount
    conversions: tuple[Conversion, ...] = ()
    unpriced: tuple[Commodity, ...] = ()

    @property
    def income_earned(self) -> Amount:
        """Income as a positive figure."""
        return -self.income

    @property
    def net(self) -> Amount:
        """Surplus (positive) or deficit (negative) for the period."""
        return -(self.income + self.expenses)

    @property
    def savings_rate(self) -> Fraction | None:
        """Net over income earned, exact; None when there was no income."""
        earned = self.income_earned.minor
        if earned == 0:
            return None
        return Fraction(self.net.minor, earned)

    def rows_for(self, account_class: AccountClass) -> tuple[AccountRow, ...]:
        return tuple(r for r in self.rows if r.account.account_class is account_class)

    def __str__(self) -> str:
        return (
            f"Income statement {self.start}..{self.end} ({self.target.code}): "
            f"income {self.income_earned}, expenses {self.expenses}, "
            f"net {self.net}"
        )


def income_statement(
    book: Book,
    policy: ValuationPolicy,
    *,
    start: date,
    include_closed: bool = False,
    strict: bool = True,
) -> IncomeStatement:
    """Income and expenses from ``start`` to ``policy.as_of`` inclusive.

    Period reports need two dates, but the valuation date is still a single
    point -- ``policy.as_of`` -- so that a multi-currency P&L is translated at
    one consistent rate rather than at whatever rate happened to apply on each
    posting date.
    """
    end = policy.as_of
    if end is None:
        raise ValidationError("an income statement needs policy.as_of as its end date")
    if start > end:
        raise ValidationError(f"start {start} is after end {end}")

    valuer = Valuer(book, policy, strict=strict)
    movements = book.balances_by_account(start=start, end=end)
    rows = account_tree(
        book,
        policy,
        roots=[a for a in book.roots() if not a.account_class.is_balance_sheet],
        include_closed=include_closed,
        valuer=valuer,
        own_balances=movements,
    )

    def class_total(account_class: AccountClass) -> Amount:
        totals = Balances(
            movements[a.id] for a in book.accounts(account_class=account_class)
        )
        return valuer.value_amount(totals)

    return IncomeStatement(
        start=start,
        end=end,
        target=valuer.target,
        policy=policy,
        rows=rows,
        income=class_total(AccountClass.INCOME),
        expenses=class_total(AccountClass.EXPENSE),
        conversions=valuer.conversions,
        unpriced=valuer.unpriced,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Holding:
    """One security position."""

    account: Account
    path: str
    commodity: Commodity
    quantity: Amount
    """Units held, in the security's own commodity."""
    cost: Amount
    """What was paid, summed from the transaction-value column."""
    market_value: Amount
    """Current worth under the policy, in the target commodity."""
    conversion: Conversion | None

    @property
    def unrealised_gain(self) -> Amount:
        """Market value minus cost.

        Only meaningful when the cost was recorded in the same commodity the
        report is targeting; otherwise the cost itself needs translating and
        this figure mixes FX with performance.
        """
        return self.market_value - self.cost

    @property
    def unit_price(self) -> Fraction | None:
        return None if self.conversion is None else self.conversion.rate

    def __str__(self) -> str:
        return f"{self.path}: {self.quantity} = {self.market_value}"


@dataclass(frozen=True, slots=True, kw_only=True)
class HoldingsReport:
    as_of: date
    target: Commodity
    policy: ValuationPolicy
    positions: tuple[Holding, ...] = ()
    conversions: tuple[Conversion, ...] = ()
    unpriced: tuple[Commodity, ...] = ()

    @property
    def total_value(self) -> Amount:
        if not self.positions:
            return Amount.zero(self.target)
        total = Amount.zero(self.target)
        for position in self.positions:
            total = total + position.market_value
        return total

    @property
    def total_cost(self) -> Amount:
        total = Amount.zero(self.target)
        for position in self.positions:
            if position.cost.commodity.id == self.target.id:
                total = total + position.cost
        return total

    def __str__(self) -> str:
        return (
            f"Holdings {self.as_of} ({self.target.code}): "
            f"{len(self.positions)} positions, {self.total_value}"
        )


def holdings(
    book: Book,
    policy: ValuationPolicy,
    *,
    include_empty: bool = False,
    strict: bool = True,
) -> HoldingsReport:
    """Security positions with market value under ``policy``."""
    if policy.as_of is None:
        raise ValidationError("a holdings report needs policy.as_of")
    valuer = Valuer(book, policy, strict=strict)
    positions: list[Holding] = []
    for account in book.accounts(type=AccountType.STOCK):
        quantity = book.balance(account.id, as_of=policy.as_of)
        if quantity.is_zero and not include_empty:
            continue
        conversion = valuer.conversion(account.commodity)
        market = valuer.value_amount(Balances([quantity]))
        positions.append(
            Holding(
                account=account,
                path=book.path(account),
                commodity=account.commodity,
                quantity=quantity,
                cost=book.value_balance(account.id, as_of=policy.as_of),
                market_value=market,
                conversion=conversion,
            )
        )
    positions.sort(key=lambda p: p.path)
    return HoldingsReport(
        as_of=policy.as_of,
        target=valuer.target,
        policy=policy,
        positions=tuple(positions),
        conversions=valuer.conversions,
        unpriced=valuer.unpriced,
    )


# ==========================================================================
# over time: the same figures, per period
# ==========================================================================
Interval = Literal["month", "quarter", "year"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Period:
    """One column of a time series: a closed date range with a short label."""

    start: date
    end: date
    label: str
    """``2026-03`` for a month, ``2026-Q1`` for a quarter, ``2026`` for a year."""


def _period_start(day: date, interval: Interval) -> date:
    if interval == "month":
        return day.replace(day=1)
    if interval == "quarter":
        return day.replace(month=day.month - (day.month - 1) % 3, day=1)
    return day.replace(month=1, day=1)


def _next_start(start: date, interval: Interval) -> date:
    months = {"month": 1, "quarter": 3, "year": 12}[interval]
    year, month = divmod(start.month - 1 + months, 12)
    return start.replace(year=start.year + year, month=month + 1)


def _label(start: date, interval: Interval) -> str:
    if interval == "month":
        return f"{start.year}-{start.month:02d}"
    if interval == "quarter":
        return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
    return str(start.year)


def periods_between(
    start: date, end: date, interval: Interval = "month"
) -> tuple[Period, ...]:
    """Calendar periods covering ``start``..``end`` inclusive.

    The first and last are clipped to the range, so a report from the 15th
    counts from the 15th: a column is what happened in the window, never more.
    """
    if start > end:
        raise ValidationError(f"start {start} is after end {end}")
    out: list[Period] = []
    cursor = _period_start(start, interval)
    while cursor <= end:
        following = _next_start(cursor, interval)
        out.append(
            Period(
                start=max(cursor, start),
                end=min(following - timedelta(days=1), end),
                label=_label(cursor, interval),
            )
        )
        cursor = following
    return tuple(out)


@dataclass(frozen=True, slots=True, kw_only=True)
class NetWorthPoint:
    as_of: date
    label: str
    assets: Amount
    liabilities: Amount
    """Signed as stored, so normally negative."""
    net_worth: Amount


@dataclass(frozen=True, slots=True, kw_only=True)
class NetWorthSeries:
    """Assets, liabilities and net worth at the end of every period."""

    start: date
    end: date
    interval: Interval
    target: Commodity
    policy: ValuationPolicy
    points: tuple[NetWorthPoint, ...]
    conversions: tuple[Conversion, ...] = ()
    """Every distinct rate used at any point, so the line can be traced."""
    unpriced: tuple[Commodity, ...] = ()


def net_worth_series(
    book: Book,
    policy: ValuationPolicy,
    *,
    start: date,
    interval: Interval = "month",
    strict: bool = True,
) -> NetWorthSeries:
    """The balance sheet's three totals at the end of each period up to ``policy.as_of``.

    Each point is valued *as of its own date* -- that is what a net-worth line
    means: what it was worth then, at the rates known for then -- under the same
    knowledge cutoff and source preferences for every point, so the series is
    as reproducible as a single balance sheet.
    """
    end = policy.as_of
    if end is None:
        raise ValidationError("a net worth series needs policy.as_of as its end date")
    by_class = {
        klass: [a.id for a in book.accounts(account_class=klass)]
        for klass in (AccountClass.ASSET, AccountClass.LIABILITY)
    }
    target = Valuer(book, policy, strict=strict).target
    points: list[NetWorthPoint] = []
    conversions: dict[tuple, Conversion] = {}
    unpriced: dict[UUID, Commodity] = {}
    for period in periods_between(start, end, interval):
        dated = policy.for_date(period.end)
        valuer = Valuer(book, dated, strict=strict)
        own = book.balances_by_account(end=period.end)
        totals = {
            klass: valuer.value_amount(Balances(own[i] for i in ids))
            for klass, ids in by_class.items()
        }
        points.append(
            NetWorthPoint(
                as_of=period.end,
                label=period.label,
                assets=totals[AccountClass.ASSET],
                liabilities=totals[AccountClass.LIABILITY],
                net_worth=totals[AccountClass.ASSET] + totals[AccountClass.LIABILITY],
            )
        )
        for conversion in valuer.conversions:
            conversions.setdefault(tuple(p.id for p in conversion.prices), conversion)
        for commodity in valuer.unpriced:
            unpriced.setdefault(commodity.id, commodity)
    return NetWorthSeries(
        start=start,
        end=end,
        interval=interval,
        target=target,
        policy=policy,
        points=tuple(points),
        conversions=tuple(
            sorted(
                conversions.values(),
                key=lambda c: (
                    c.base.code,
                    c.prices[0].effective_date if c.prices else date.min,
                ),
            )
        ),
        unpriced=tuple(sorted(unpriced.values(), key=lambda c: c.code)),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PeriodRow:
    """One account across the columns: subtree movement per period, and the total."""

    account: Account
    path: str
    depth: int
    cells: tuple[Amount, ...]
    """Signed as stored, one per column."""
    total: Amount
    sign: int

    @property
    def display_cells(self) -> tuple[Amount, ...]:
        return tuple(c * self.sign for c in self.cells)

    @property
    def display_total(self) -> Amount:
        return self.total * self.sign


@dataclass(frozen=True, slots=True, kw_only=True)
class PeriodTable:
    """Income and expense accounts down, periods across: the spreadsheet view."""

    start: date
    end: date
    interval: Interval
    target: Commodity
    policy: ValuationPolicy
    columns: tuple[Period, ...]
    rows: tuple[PeriodRow, ...]
    income: tuple[Amount, ...]
    """Per column, signed as stored (normally negative); the last is the total."""
    expenses: tuple[Amount, ...]
    conversions: tuple[Conversion, ...] = ()
    unpriced: tuple[Commodity, ...] = ()

    @property
    def net(self) -> tuple[Amount, ...]:
        """Surplus per column, positive when more came in than went out."""
        return tuple(-(i + e) for i, e in zip(self.income, self.expenses, strict=True))


def period_table(
    book: Book,
    policy: ValuationPolicy,
    *,
    start: date,
    interval: Interval = "month",
    include_closed: bool = False,
    strict: bool = True,
) -> PeriodTable:
    """Income and expenses per period from ``start`` to ``policy.as_of``.

    Like the income statement, every column is translated at one rate -- the
    policy's ``as_of`` -- so a row can be read across without exchange-rate
    noise between the months. Rows follow the account tree, each holding its
    subtree's movement, so a parent reads as the sum of its children.
    """
    end = policy.as_of
    if end is None:
        raise ValidationError("a period table needs policy.as_of as its end date")
    columns = periods_between(start, end, interval)
    valuer = Valuer(book, policy, strict=strict)
    # Income above expenses, then by name: the order a P&L reads in.
    roots = sorted(
        (a for a in book.roots() if not a.account_class.is_balance_sheet),
        key=lambda a: (a.account_class is not AccountClass.INCOME, a.name),
    )
    per_column: list[dict[UUID, Balances]] = []
    own_per_column: list[dict[UUID, Amount]] = []
    for column in columns:
        own = book.balances_by_account(start=column.start, end=column.end)
        own_per_column.append(own)
        per_column.append(_subtree_totals(book, own, roots))

    def summed(cells: Iterable[Amount]) -> Amount:
        # The total is the sum of the booked cells, not a fresh valuation of
        # the whole: a spreadsheet row must add up to its own last column.
        total = Amount.zero(valuer.target)
        for cell in cells:
            total = total + cell
        return total

    rows: list[PeriodRow] = []

    def emit(account: Account, depth: int) -> None:
        if account.closed and not include_closed:
            return
        cells = tuple(valuer.value_amount(t[account.id]) for t in per_column)
        rows.append(
            PeriodRow(
                account=account,
                path=book.path(account),
                depth=depth,
                cells=cells,
                total=summed(cells),
                sign=account.report_sign,
            )
        )
        for child in book.children(account.id):
            emit(child, depth + 1)

    for root in roots:
        emit(root, 0)

    def class_totals(klass: AccountClass) -> tuple[Amount, ...]:
        ids = [a.id for a in book.accounts(account_class=klass)]
        per = [
            valuer.value_amount(Balances(own[i] for i in ids)) for own in own_per_column
        ]
        return (*per, summed(per))

    return PeriodTable(
        start=start,
        end=end,
        interval=interval,
        target=valuer.target,
        policy=policy,
        columns=columns,
        rows=tuple(rows),
        income=class_totals(AccountClass.INCOME),
        expenses=class_totals(AccountClass.EXPENSE),
        conversions=valuer.conversions,
        unpriced=valuer.unpriced,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class BudgetRow:
    """One account's plan beside its period-table row.

    ``budget`` is the account's whole subtree budget for each column, already
    in the table's target commodity.  ``None`` means the subtree has no budget
    for that month, or its currency could not be converted.

    A client derives the rest from this and the row's movement:
    ``remaining[i]`` is ``budget[i]`` minus ``table.rows[i].display_cells[i]``
    (``None`` where ``budget[i]`` is ``None``), and a total is the
    ``None``-skipping sum of the columns.
    """

    account_id: UUID
    placeholder: bool
    """The account only groups its children, so it takes no plan of its own:
    a client should show the rolled-up figures but not the editor."""
    closed: bool
    """Closed accounts keep their history and refuse new postings, so no plan
    can be set on one either."""
    budget: tuple[Amount | None, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class BudgetClassTotals:
    """What one class of accounts has, per column -- income, or expenses.

    Counted over just the budgeted accounts of this class that the period
    table shows, each movement once.  ``budget`` is the plan on those
    accounts, ``spent`` what they moved; both read as positive magnitudes --
    what is earned, what is spent -- so a client subtracts them into savings.
    Both tuples have one entry per column and no total entry.

    A client derives ``remaining[i]`` as ``budget[i] - spent[i]``
    (``None`` where ``budget[i]`` is ``None``), and a total as the
    ``None``-skipping sum of the columns.
    """

    budget: tuple[Amount | None, ...]
    spent: tuple[Amount, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class BudgetTable:
    """The period table, plus a plan for every budgeted row."""

    table: PeriodTable
    rows: tuple[BudgetRow, ...]
    """One per ``table.rows`` entry, in the same order."""
    income: BudgetClassTotals
    """The income accounts only."""
    expenses: BudgetClassTotals
    """The expense accounts only."""


def _months_of(column: Period) -> tuple[str, ...]:
    """The ``YYYY-MM`` labels a column covers (one for a month, three for a
    quarter, twelve for a year)."""
    return tuple(p.label for p in periods_between(column.start, column.end, "month"))


def budget_table(
    book: Book,
    policy: ValuationPolicy,
    *,
    start: date,
    interval: Interval = "month",
    include_closed: bool = False,
    strict: bool = True,
) -> BudgetTable:
    """The period table with each account's budget and what is left of it.

    Budgets are monthly; a quarter or year column sums the months it spans.
    Each budget is converted to the table's target under the same policy as
    the actuals, so ``remaining`` is comparable down the column.
    """
    table = period_table(
        book,
        policy,
        start=start,
        interval=interval,
        include_closed=include_closed,
        strict=strict,
    )
    valuer = Valuer(book, policy, strict=strict)
    budgets = {b.account_id: b for b in book.budgets()}
    col_months = [_months_of(c) for c in table.columns]

    def own_budget(account_id: UUID, months: tuple[str, ...]) -> Amount | None:
        budget = budgets.get(account_id)
        if budget is None:
            return None
        minors = [m for ym in months if (m := budget.for_month(ym)) is not None]
        if not minors:
            return None
        raw = Amount(budget.currency, sum(minors))
        conversion = valuer.conversion(budget.currency)
        if conversion is None:
            return None
        return conversion.apply(raw).book()

    # Subtree budget per (account, column): own plus the children's, paralleling
    # the period table's subtree cells.
    subtree: dict[UUID, list[Amount | None]] = {}

    def roll(account: Account) -> list[Amount | None]:
        acc: list[Amount | None] = [
            own_budget(account.id, months) for months in col_months
        ]
        for child in book.children(account.id):
            child_cells = roll(child)
            acc = [
                _add_optional(a, b, valuer.target)
                for a, b in zip(acc, child_cells, strict=True)
            ]
        subtree[account.id] = acc
        return acc

    for root in book.roots():
        roll(root)

    budget_rows: list[BudgetRow] = []
    for row in table.rows:
        cells = subtree.get(row.account.id, [None] * len(table.columns))
        budget_rows.append(
            BudgetRow(
                account_id=row.account.id,
                placeholder=row.account.placeholder,
                closed=row.account.closed,
                budget=tuple(cells),
            )
        )

    def class_totals(*classes: AccountClass) -> BudgetClassTotals:
        """The footer for one class: the rollup of the topmost rows carrying a plan.

        Reading it as a row rollup rather than a sum of accounts keeps it equal
        to the table above it, counts each movement once when a budgeted
        account has a budgeted child (whose subtree its ancestor's rollup
        already carries), and leaves the footer alone when a plan sits on an
        account this report cannot show -- an asset, or a closed account while
        those are hidden -- rather than inflating a figure no row explains.
        """
        carrying = {
            row.account.id
            for row, budget_row in zip(table.rows, budget_rows, strict=True)
            if row.account.account_class in classes
            and any(b is not None for b in budget_row.budget)
        }
        topmost = [
            (row, budget_row)
            for row, budget_row in zip(table.rows, budget_rows, strict=True)
            if row.account.id in carrying
            and not any(a.id in carrying for a in book.ancestors(row.account))
        ]
        per_budget: list[Amount | None] = []
        per_spent: list[Amount] = []
        for i in range(len(table.columns)):
            b_total: Amount | None = None
            s_total = Amount.zero(valuer.target)
            for row, budget_row in topmost:
                plan = budget_row.budget[i]
                if plan is None:
                    continue
                b_total = plan if b_total is None else b_total + plan
                s_total = s_total + row.display_cells[i]
            per_budget.append(b_total)
            per_spent.append(s_total)
        return BudgetClassTotals(budget=tuple(per_budget), spent=tuple(per_spent))

    return BudgetTable(
        table=table,
        rows=tuple(budget_rows),
        income=class_totals(AccountClass.INCOME),
        expenses=class_totals(AccountClass.EXPENSE),
    )


def _add_optional(a: Amount | None, b: Amount | None, target: Commodity) -> Amount | None:
    if a is None:
        return b
    if b is None:
        return a
    return a + b
