"""Accounts (decision #1): typed and hierarchical, like KMyMoney.

An account has a *type* (CHECKING, CREDIT_CARD, STOCK, ...) and every type
belongs to exactly one *class* (ASSET, LIABILITY, INCOME, EXPENSE, EQUITY).  The
class decides the sign convention; the type only affects presentation and which
features a UI offers (a STOCK account gets a holdings view, a LOAN gets an
amortisation schedule).

Children must share their parent's class, which is what makes "income",
"expenses", "assets" and "liabilities" behave as the four familiar trees while
still letting you nest ``Expenses:Car:Fuel`` arbitrarily deep.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from .commodity import Commodity
from .entity import Entity
from .errors import ValidationError

__all__ = ["ACCOUNT_SEPARATOR", "Account", "AccountClass", "AccountType"]

ACCOUNT_SEPARATOR = ":"


class AccountClass(StrEnum):
    """The five fundamental account classes of double-entry bookkeeping."""

    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"
    EQUITY = "EQUITY"

    @property
    def is_debit_normal(self) -> bool:
        """True when a positive stored balance means a debit balance.

        Assets and expenses are debit-normal; liabilities, income and equity are
        credit-normal.  We store *signed* values (KMyMoney-style) rather than
        separate debit/credit columns, so this is purely a reporting concern.
        """
        return self in (AccountClass.ASSET, AccountClass.EXPENSE)

    @property
    def report_sign(self) -> int:
        """Multiplier to make a balance read naturally in a report.

        A credit card you owe 500 on stores -500; a report should show 500 of
        debt.  Income of 3000 stores -3000; a P&L should show 3000 earned.
        """
        return 1 if self.is_debit_normal else -1

    @property
    def is_balance_sheet(self) -> bool:
        """True for accounts whose balance accumulates forever."""
        return self in (AccountClass.ASSET, AccountClass.LIABILITY, AccountClass.EQUITY)


class AccountType(StrEnum):
    """Concrete account types; each maps to exactly one :class:`AccountClass`."""

    # asset
    CHECKING = "CHECKING"
    SAVINGS = "SAVINGS"
    CASH = "CASH"
    INVESTMENT = "INVESTMENT"
    STOCK = "STOCK"
    ASSET = "ASSET"
    RECEIVABLE = "RECEIVABLE"
    # liability
    CREDIT_CARD = "CREDIT_CARD"
    LOAN = "LOAN"
    LIABILITY = "LIABILITY"
    PAYABLE = "PAYABLE"
    # income / expense
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"
    # equity
    EQUITY = "EQUITY"

    @property
    def account_class(self) -> AccountClass:
        return _TYPE_CLASS[self]

    @property
    def is_debit_normal(self) -> bool:
        return self.account_class.is_debit_normal

    @property
    def report_sign(self) -> int:
        return self.account_class.report_sign

    @property
    def holds_securities(self) -> bool:
        """True for types whose commodity is normally a security."""
        return self is AccountType.STOCK


_TYPE_CLASS: dict[AccountType, AccountClass] = {
    AccountType.CHECKING: AccountClass.ASSET,
    AccountType.SAVINGS: AccountClass.ASSET,
    AccountType.CASH: AccountClass.ASSET,
    AccountType.INVESTMENT: AccountClass.ASSET,
    AccountType.STOCK: AccountClass.ASSET,
    AccountType.ASSET: AccountClass.ASSET,
    AccountType.RECEIVABLE: AccountClass.ASSET,
    AccountType.CREDIT_CARD: AccountClass.LIABILITY,
    AccountType.LOAN: AccountClass.LIABILITY,
    AccountType.LIABILITY: AccountClass.LIABILITY,
    AccountType.PAYABLE: AccountClass.LIABILITY,
    AccountType.INCOME: AccountClass.INCOME,
    AccountType.EXPENSE: AccountClass.EXPENSE,
    AccountType.EQUITY: AccountClass.EQUITY,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Account(Entity):
    """A node in one of the account trees.

    ``name`` is the local name; the full colon-joined path is derived by the
    :class:`~pymoney.core.book.Book`, which owns the tree structure.  Renames
    and moves are therefore cheap: no stored path to rewrite (decision #13).
    """

    type: AccountType
    name: str
    commodity: Commodity
    parent_id: UUID | None = None
    description: str = ""
    code: str = ""
    """User-visible account number, e.g. a chart-of-accounts code."""
    closed: bool = False
    """Closed accounts reject new postings but keep their history."""
    placeholder: bool = False
    """Placeholders group children and reject postings of their own."""
    notes: str = ""
    institution: str = ""
    iban: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("account name must not be empty")
        if ACCOUNT_SEPARATOR in self.name:
            raise ValidationError(
                f"account name must not contain {ACCOUNT_SEPARATOR!r}: {self.name!r}"
            )

    @property
    def account_class(self) -> AccountClass:
        return self.type.account_class

    @property
    def report_sign(self) -> int:
        return self.account_class.report_sign

    @property
    def accepts_postings(self) -> bool:
        return not self.closed and not self.placeholder

    @property
    def key(self) -> str:
        """Sibling-uniqueness key (case-insensitive)."""
        return self.name.strip().casefold()

    def __str__(self) -> str:
        return self.name
