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
from datetime import date
from fractions import Fraction
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
    "Holding",
    "HoldingsReport",
    "IncomeStatement",
    "Valuer",
    "account_tree",
    "balance_sheet",
    "holdings",
    "income_statement",
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
