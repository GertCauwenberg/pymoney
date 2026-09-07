"""PyMoney core: the double-entry accounting domain, in pure Python.

No database, no network, no UI.  Everything in this package is deterministic and
exact: amounts are integer minor units, rates are :class:`fractions.Fraction`,
timestamps come from an injectable clock.

The intended entry point is :class:`~pymoney.core.book.Book`::

    from datetime import date
    from pymoney.core import Account, AccountType, Book, Commodity, LegSpec

    book = Book(base_currency=Commodity.currency("EUR", symbol="\u20ac"))
    assets = book.add_account("Assets", AccountType.ASSET, placeholder=True)
    cash = book.add_account("Cash", AccountType.CASH, parent=assets)
    food = book.add_account("Groceries", AccountType.EXPENSE)

    book.post(
        date(2026, 2, 1),
        [LegSpec.of(cash.id, "-23.40"), LegSpec.of(food.id)],
        memo="Market",
    )
    book.balance(cash.id)  # Amount(EUR -23.40)

Mutations only happen through commands (``book.execute(...)``, or the thin
``add_*``/``post`` façade that wraps them), so every change lands in the change
log with a revision and can be replayed to a replica.
"""

from __future__ import annotations

from .account import ACCOUNT_SEPARATOR, Account, AccountClass, AccountType
from .amount import Amount, Balances, RationalAmount
from .book import Book, SplitRef
from .changelog import Change, ChangeLog, ChangeOp, EntityKind
from .clock import Clock, ManualClock, SystemClock
from .commands import (
    UNSET,
    AddPrice,
    Command,
    CommandResult,
    CreateAccount,
    CreateCommodity,
    CreatePayee,
    DeleteAccount,
    DeleteCommodity,
    DeletePayee,
    DeletePrice,
    DeleteTransaction,
    InitialiseBook,
    MergeAccounts,
    MergePayees,
    MoveAccount,
    PostTransaction,
    ReconcileAccount,
    RenameAccount,
    RenameBook,
    SetAccountClosed,
    SetAccountCommodity,
    SetBaseCurrency,
    SetSplitReconcileState,
    Unset,
    UpdateAccount,
    UpdateCommodity,
    UpdatePayee,
    UpdateTransaction,
)
from .commodity import Commodity, CommodityKind
from .entity import Entity
from .errors import (
    CommandError,
    CommodityMismatchError,
    ConflictError,
    ImmutableError,
    NoPriceError,
    NotFoundError,
    PyMoneyError,
    UnbalancedTransactionError,
    ValidationError,
)
from .ids import Uuid7Generator, new_id, uuid7
from .payee import Payee
from .posting import BookedLegs, LegSpec, book_legs
from .price import Conversion, Price, PriceDB, PriceSource, ValuationPolicy
from .recurrence import Frequency, Recurrence
from .reports import (
    AccountRow,
    BalanceSheet,
    Holding,
    HoldingsReport,
    IncomeStatement,
    NetWorthPoint,
    NetWorthSeries,
    Period,
    PeriodRow,
    PeriodTable,
    Valuer,
    account_tree,
    balance_sheet,
    holdings,
    income_statement,
    net_worth_series,
    period_table,
    periods_between,
)
from .rounding import (
    DEFAULT_ROUNDING,
    RoundingMode,
    allocate,
    allocate_to_zero_sum,
    distribute,
    round_fraction,
    to_fraction,
)
from .schedule import Schedule, ScheduleTemplate, TemplateLeg
from .settings import BookSettings
from .store import TransactionSearch
from .sync import RevisionBatch, SyncPage
from .transaction import ReconcileState, Split, Transaction

__version__ = "0.1.0"

# Grouped by concept rather than alphabetically: this list doubles as a map of
# the domain for anyone reading the package for the first time.
__all__ = [  # noqa: RUF022
    "__version__",
    # identity & time
    "new_id",
    "uuid7",
    "Uuid7Generator",
    "Clock",
    "SystemClock",
    "ManualClock",
    "Entity",
    # numbers
    "RoundingMode",
    "DEFAULT_ROUNDING",
    "to_fraction",
    "round_fraction",
    "distribute",
    "allocate",
    "allocate_to_zero_sum",
    # value objects
    "Commodity",
    "CommodityKind",
    "Amount",
    "RationalAmount",
    "Balances",
    # entities
    "Account",
    "AccountClass",
    "AccountType",
    "ACCOUNT_SEPARATOR",
    "Frequency",
    "Payee",
    "Recurrence",
    "Schedule",
    "ScheduleTemplate",
    "TemplateLeg",
    "Split",
    "Transaction",
    "ReconcileState",
    "Price",
    "PriceDB",
    "PriceSource",
    "ValuationPolicy",
    "Conversion",
    # posting
    "LegSpec",
    "BookedLegs",
    "book_legs",
    # book & sync
    "Book",
    "SplitRef",
    "Change",
    "ChangeLog",
    "ChangeOp",
    "EntityKind",
    "BookSettings",
    "RevisionBatch",
    "SyncPage",
    "TransactionSearch",
    # commands
    "Command",
    "CommandResult",
    "UNSET",
    "Unset",
    "CreateCommodity",
    "UpdateCommodity",
    "DeleteCommodity",
    "SetBaseCurrency",
    "RenameBook",
    "InitialiseBook",
    "CreateAccount",
    "UpdateAccount",
    "RenameAccount",
    "SetAccountCommodity",
    "MoveAccount",
    "SetAccountClosed",
    "MergeAccounts",
    "DeleteAccount",
    "CreatePayee",
    "UpdatePayee",
    "MergePayees",
    "DeletePayee",
    "PostTransaction",
    "UpdateTransaction",
    "DeleteTransaction",
    "SetSplitReconcileState",
    "ReconcileAccount",
    "AddPrice",
    "DeletePrice",
    # reports
    "Valuer",
    "AccountRow",
    "BalanceSheet",
    "IncomeStatement",
    "PeriodTable",
    "PeriodRow",
    "Period",
    "NetWorthSeries",
    "NetWorthPoint",
    "Holding",
    "HoldingsReport",
    "account_tree",
    "balance_sheet",
    "income_statement",
    "periods_between",
    "period_table",
    "net_worth_series",
    "holdings",
    # errors
    "PyMoneyError",
    "ValidationError",
    "NotFoundError",
    "ConflictError",
    "ImmutableError",
    "CommodityMismatchError",
    "UnbalancedTransactionError",
    "NoPriceError",
    "CommandError",
]
