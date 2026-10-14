"""The KMyMoney file as it is, before any interpretation.

These dataclasses mirror the XML one-for-one: same names, same rationals, same
numeric type codes. Nothing is mapped, rounded or validated here. Keeping the
parse and the interpretation apart means a surprise in a real file shows up as
"the parser read this" rather than as a mysterious import failure, and the
mappings below stay in one readable place.

Type codes come from KMyMoney's own enums (``mymoneyaccount.h``,
``mymoneysecurity.h``, ``mymoneysplit.h``). They are written as bare integers in
the file, so this module is where that knowledge lives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction

from ...core.account import AccountType
from ...core.commodity import CommodityKind
from ...core.transaction import ReconcileState

__all__ = [
    "ACCOUNT_TYPES",
    "RECONCILE_STATES",
    "SECURITY_KINDS",
    "STANDARD_ACCOUNTS",
    "KmyAccount",
    "KmyBudget",
    "KmyBudgetAccount",
    "KmyBudgetPeriod",
    "KmyCommodity",
    "KmyFileInfo",
    "KmyInstitution",
    "KmyPayee",
    "KmyPrice",
    "KmySplit",
    "KmyTransaction",
]


# KMyMoney's eMyMoney::Account::Type, verbatim, with the closest PyMoney type.
#
# Where KMyMoney is more specific than we are, the mapping loses a shade of
# meaning but never the account class: a certificate of deposit becomes a
# SAVINGS account, not an expense. The importer reports every one of these.
ACCOUNT_TYPES: dict[int, AccountType] = {
    1: AccountType.CHECKING,  # Checkings
    2: AccountType.SAVINGS,  # Savings
    3: AccountType.CASH,  # Cash
    4: AccountType.CREDIT_CARD,  # CreditCard
    5: AccountType.LOAN,  # Loan (a liability)
    6: AccountType.SAVINGS,  # CertificateDep
    7: AccountType.INVESTMENT,  # Investment
    8: AccountType.SAVINGS,  # MoneyMarket
    9: AccountType.ASSET,  # Asset
    10: AccountType.LIABILITY,  # Liability
    11: AccountType.ASSET,  # Currency (a currency-trading account)
    12: AccountType.INCOME,  # Income
    13: AccountType.EXPENSE,  # Expense
    14: AccountType.RECEIVABLE,  # AssetLoan: a loan *we* are owed
    15: AccountType.STOCK,  # Stock
    16: AccountType.EQUITY,  # Equity
}

# Types we deliberately narrow, and what the user loses by it.
NARROWED_TYPES: dict[int, str] = {
    6: "certificate of deposit -> savings account",
    8: "money market account -> savings account",
    11: "currency trading account -> generic asset account",
}

# eMyMoney::Security::Type
SECURITY_KINDS: dict[int, CommodityKind] = {
    0: CommodityKind.STOCK,
    1: CommodityKind.FUND,
    2: CommodityKind.BOND,
    3: CommodityKind.CURRENCY,
    4: CommodityKind.OTHER,
}

# eMyMoney::Split::State
RECONCILE_STATES: dict[int, ReconcileState] = {
    0: ReconcileState.NOT_RECONCILED,
    1: ReconcileState.CLEARED,
    2: ReconcileState.RECONCILED,
    3: ReconcileState.FROZEN,
}

# The five implicit roots. Top-level user accounts carry one of these as their
# parent; the accounts themselves are usually not written to the file at all.
STANDARD_ACCOUNTS: dict[str, tuple[str, AccountType]] = {
    "AStd::Asset": ("Assets", AccountType.ASSET),
    "AStd::Liability": ("Liabilities", AccountType.LIABILITY),
    "AStd::Income": ("Income", AccountType.INCOME),
    "AStd::Expense": ("Expenses", AccountType.EXPENSE),
    "AStd::Equity": ("Equity", AccountType.EQUITY),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class KmyFileInfo:
    creation_date: date | None = None
    last_modified: date | None = None
    version: str = ""
    fix_version: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class KmyCommodity:
    """A ``<CURRENCY>`` or a ``<SECURITY>``.

    They are the same thing to KMyMoney too: a currency's ``id`` is its ISO
    code, a security's is ``E000001``, and both are referenced the same way from
    accounts and prices.
    """

    id: str
    name: str = ""
    symbol: str = ""
    type: int = 0
    is_currency: bool = True
    smallest_account_fraction: int = 100
    """KMyMoney's ``saf``: exactly our ``minor_unit_factor``."""
    smallest_cash_fraction: int | None = None
    price_precision: int | None = None
    trading_currency: str = ""
    trading_market: str = ""
    isin: str = ""

    @property
    def kind(self) -> CommodityKind:
        if self.is_currency:
            return CommodityKind.CURRENCY
        return SECURITY_KINDS.get(self.type, CommodityKind.OTHER)


@dataclass(frozen=True, slots=True, kw_only=True)
class KmyInstitution:
    id: str
    name: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class KmyPayee:
    id: str
    name: str = ""
    email: str = ""
    notes: str = ""
    address: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class KmyAccount:
    id: str
    name: str = ""
    parent_id: str = ""
    type: int = 0
    currency_id: str = ""
    description: str = ""
    number: str = ""
    institution_id: str = ""
    opened: date | None = None
    last_reconciled: date | None = None
    key_values: dict[str, str] = field(default_factory=dict)

    @property
    def is_standard(self) -> bool:
        return self.id in STANDARD_ACCOUNTS

    @property
    def closed(self) -> bool:
        return self.key_values.get("mm-closed", "").lower() in ("yes", "true", "1")

    @property
    def iban(self) -> str:
        return self.key_values.get("iban", "")


@dataclass(frozen=True, slots=True, kw_only=True)
class KmySplit:
    """One leg. ``shares`` and ``value`` are exact rationals, as in the file.

    This is the crux of the whole import: KMyMoney keeps both as unbounded
    rationals, so ``1000/3`` is a perfectly ordinary value there and is not
    representable in our integer minor units. See :mod:`..importer`.
    """

    id: str = ""
    account_id: str = ""
    payee_id: str = ""
    shares: Fraction = Fraction(0)
    """Movement in the account's own commodity."""
    value: Fraction = Fraction(0)
    """Worth in the transaction's currency."""
    price: Fraction | None = None
    memo: str = ""
    action: str = ""
    number: str = ""
    bank_id: str = ""
    reconcile_flag: int = 0
    reconcile_date: date | None = None
    tag_ids: tuple[str, ...] = ()

    @property
    def reconcile_state(self) -> ReconcileState:
        return RECONCILE_STATES.get(self.reconcile_flag, ReconcileState.NOT_RECONCILED)


@dataclass(frozen=True, slots=True, kw_only=True)
class KmyTransaction:
    id: str = ""
    post_date: date | None = None
    entry_date: date | None = None
    currency_id: str = ""
    memo: str = ""
    splits: tuple[KmySplit, ...] = ()

    @property
    def value_total(self) -> Fraction:
        """Exact sum of the value column. Zero in a sound transaction."""
        return sum((s.value for s in self.splits), Fraction(0))


@dataclass(frozen=True, slots=True, kw_only=True)
class KmySchedule:
    """A scheduled / recurring transaction.

    The embedded ``transaction`` is the template; its ``post_date`` is the next
    occurrence that has not been entered.
    """

    id: str = ""
    name: str = ""
    occurrence: int = 0
    """KMyMoney's ``eMyMoney::Schedule::Occurrence`` code (32 = monthly,
    16384 = yearly, ...). See ``kmymoney/mymoney/mymoneyenums.h``."""
    occurrence_multiplier: int = 1
    start_date: date | None = None
    end_date: date | None = None
    last_payment: date | None = None
    auto_enter: bool = False
    fixed: bool = True
    """``False`` means KMyMoney treats the amounts as an estimate."""
    transaction: KmyTransaction | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class KmyBudgetPeriod:
    """One planned figure, as the ``<PERIOD>`` tag holds it.

    ``amount`` is an exact rational in the account's **major** units -- KMyMoney
    writes bare ``num/den`` with no currency and no scaling, so ``400/1`` is
    400.00 EUR and ``75/2`` is 37.50.
    """

    start: date | None = None
    amount: Fraction = Fraction(0)


@dataclass(frozen=True, slots=True, kw_only=True)
class KmyBudgetAccount:
    """One account's plan inside a budget: its level and its figures."""

    account_id: str
    budget_level: str = "none"
    """``eMyMoney::Budget::Level`` as the lowercase string KMyMoney writes:
    ``none`` / ``monthly`` / ``monthbymonth`` / ``yearly``."""
    budget_subaccounts: bool = False
    """KMyMoney's "include subaccounts": where a non-budgeted child's *actuals*
    are attributed in its reports.  It moves no planned amounts -- the parent's
    figure covers its subtree either way -- and PyMoney's period table shows
    the child's own row beside the parent's rollup regardless, so this has no
    effect on what we import."""
    treat_as: str = ""
    """``""`` / ``"income"`` / ``"expense"``: which side of KMyMoney's P&L the
    figure sits on.  PyMoney takes its side from the account class instead, so
    this is only worth saying something about when the two disagree."""
    periods: tuple[KmyBudgetPeriod, ...] = ()
    """One for ``monthly``/``yearly``; up to one per month for ``monthbymonth``,
    whose absent months are zero (KMyMoney writes no zero periods)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class KmyBudget:
    """A named budget: one plan, spanning twelve months from ``start``.

    KMyMoney holds several of these per file -- its UI creates one per year --
    where PyMoney has a single open-ended plan per account.  The importer merges
    them, so this is kept as the file has it.
    """

    id: str = ""
    name: str = ""
    start: date | None = None
    accounts: tuple[KmyBudgetAccount, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class KmyPrice:
    from_id: str
    to_id: str
    date: date
    rate: Fraction
    source: str = ""
