"""The interface the desktop UI talks to, and the errors it hears.

One interface, two implementations (decision: the server is authoritative):

:class:`~pymoney_desktop.remote.RemoteClient`
    Holds nothing but a small read cache. Every write is an HTTP request to the
    same API the web frontend uses, and fails the moment the network does.

:class:`~pymoney_desktop.offline.OfflineClient`
    Keeps a local SQLite replica of each book, applies writes to it at once --
    validated by the very same domain rules the server runs -- and queues them
    for replay. Reads never need the network. On reconnect the queue replays in
    order; the server's answer is final, so a conflict is a *rejection* the
    user is told about, never a merge.

The UI cannot tell them apart except through :meth:`LedgerClient.status`, which
is how it knows what to put in the status bar.

Wire types are the API's own pydantic schemas (:mod:`pymoney.api.schemas`), so
this client and the web frontend speak the identical contract and a field
cannot exist on one side only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from pymoney.api.schemas import (
    AccountBalanceOut,
    AccountIn,
    AccountOut,
    BalanceSheetOut,
    BookIn,
    BookOut,
    ColumnMappingModel,
    CommodityOut,
    CsvInspectOut,
    CsvPreviewOut,
    HoldingsOut,
    ImportDecisionIn,
    IncomeStatementOut,
    NetWorthSeriesOut,
    PayeeIn,
    PayeeOut,
    PeriodTableOut,
    ReconcileStateName,
    RegisterPage,
    ReportInterval,
    StatementImportOut,
    TransactionIn,
    TransactionOut,
    TransactionSearchOut,
    TransactionUpdateIn,
    UserOut,
)

__all__ = [
    "ApiError",
    "ConnectionStatus",
    "LedgerClient",
    "OfflineError",
    "QueuedCommand",
    "Rejection",
    "ReportOptions",
    "SearchCriteria",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchCriteria:
    """What to look for: every field set narrows, all combine with AND.

    Amounts are decimal strings in major units (the API's exact form) and
    bound the absolute size of a split -- its quantity in the account's
    commodity or its value in the transaction currency; ``amount`` alone means
    exactly that much. At least one criterion is required.
    """

    text: str | None = None
    amount: str | None = None
    amount_min: str | None = None
    amount_max: str | None = None
    start: date | None = None
    end: date | None = None
    account_id: UUID | None = None
    include_children: bool = False
    payee_id: UUID | None = None
    limit: int = 100

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                (self.text or "").strip(),
                self.amount,
                self.amount_min,
                self.amount_max,
                self.start,
                self.end,
                self.account_id,
                self.payee_id,
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportOptions:
    """The valuation policy a report is run under, as the web app exposes it.

    Every report takes these, so two reports run with the same options against
    the same book give the same numbers (decision #12). ``as_of`` is the
    valuation date -- for an income statement also the end of the period --
    and ``target_id`` the commodity to report in (None: the book's currency).
    ``strict`` fails when something cannot be valued; off, it is listed in
    ``unpriced`` and left out of the totals.
    """

    as_of: date | None = None
    target_id: UUID | None = None
    known_before: datetime | None = None
    include_closed: bool = False
    strict: bool = False


class ApiError(Exception):
    """The server answered and said no. ``status`` and ``type`` are its words."""

    def __init__(self, status: int, type_: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.type = type_

    def __str__(self) -> str:
        return f"{self.args[0]} ({self.type}, HTTP {self.status})"


class OfflineError(Exception):
    """The server could not be reached. The remote client raises this for
    everything; the offline client only for what truly needs the server."""


@dataclass(frozen=True, slots=True)
class QueuedCommand:
    """One write waiting for the server."""

    id: int
    book_id: UUID
    op: str
    """The local operation, e.g. ``post_transaction``; how a rebuilt replica re-applies it."""
    method: str
    path: str
    body: dict | None
    idempotency_key: str
    queued_at: datetime
    summary: str


@dataclass(frozen=True, slots=True)
class Rejection:
    """A queued command the server refused on replay. The local replica has
    been rebuilt from the server, so the effect is gone; the user decides what
    to do about it."""

    command: QueuedCommand
    status: int
    message: str
    at: datetime


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    online: bool
    mode: str
    """``"remote"`` or ``"offline"``."""
    queued: int = 0
    rejections: tuple[Rejection, ...] = ()
    last_sync: datetime | None = None
    last_error: str | None = None
    watermarks: dict[UUID, int] = field(default_factory=dict)
    """Per book: the server revision the local replica has seen (offline only)."""


class LedgerClient(ABC):
    """Everything the desktop UI needs. Synchronous; the UI runs it off-thread."""

    # -- session -------------------------------------------------------------
    @abstractmethod
    def login(self, email: str, password: str) -> UserOut: ...

    @abstractmethod
    def logout(self) -> None: ...

    @abstractmethod
    def me(self) -> UserOut | None:
        """The current user, or None when not logged in."""

    @abstractmethod
    def set_locale(self, locale: str) -> UserOut | None:
        """Store the user's language with the account. None if it could not
        reach the server (the offline client remembers it for next time)."""

    # -- books ---------------------------------------------------------------
    @abstractmethod
    def books(self) -> list[BookOut]: ...

    @abstractmethod
    def create_book(self, body: BookIn) -> BookOut: ...

    # -- reference data ------------------------------------------------------
    @abstractmethod
    def accounts(self, book_id: UUID) -> list[AccountOut]: ...

    @abstractmethod
    def commodities(self, book_id: UUID) -> list[CommodityOut]: ...

    @abstractmethod
    def payees(self, book_id: UUID) -> list[PayeeOut]: ...

    @abstractmethod
    def balance(
        self, book_id: UUID, account_id: UUID, *, as_of: date | None = None
    ) -> AccountBalanceOut: ...

    @abstractmethod
    def balance_sheet(
        self, book_id: UUID, options: ReportOptions | None = None
    ) -> BalanceSheetOut: ...

    @abstractmethod
    def income_statement(
        self, book_id: UUID, *, start: date, options: ReportOptions | None = None
    ) -> IncomeStatementOut:
        """Income and expenses from ``start`` to ``options.as_of`` inclusive."""

    @abstractmethod
    def holdings(
        self,
        book_id: UUID,
        options: ReportOptions | None = None,
        *,
        include_empty: bool = False,
    ) -> HoldingsOut: ...

    @abstractmethod
    def net_worth(
        self,
        book_id: UUID,
        *,
        start: date,
        interval: ReportInterval = "month",
        options: ReportOptions | None = None,
    ) -> NetWorthSeriesOut:
        """Assets, liabilities and net worth at the end of every period up to ``options.as_of``."""

    @abstractmethod
    def period_table(
        self,
        book_id: UUID,
        *,
        start: date,
        interval: ReportInterval = "month",
        options: ReportOptions | None = None,
    ) -> PeriodTableOut:
        """Income and expense accounts per period, from ``start`` to ``options.as_of``."""

    # -- ledger --------------------------------------------------------------
    @abstractmethod
    def register(
        self,
        book_id: UUID,
        account_id: UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        cursor: str | None = None,
        limit: int = 200,
    ) -> RegisterPage: ...

    @abstractmethod
    def transaction(self, book_id: UUID, transaction_id: UUID) -> TransactionOut: ...

    @abstractmethod
    def search(self, book_id: UUID, criteria: SearchCriteria) -> TransactionSearchOut:
        """Transactions matching every criterion, newest first; see SearchCriteria."""

    @abstractmethod
    def post_transaction(self, book_id: UUID, body: TransactionIn) -> TransactionOut: ...

    @abstractmethod
    def update_transaction(
        self, book_id: UUID, transaction_id: UUID, body: TransactionUpdateIn
    ) -> TransactionOut: ...

    @abstractmethod
    def delete_transaction(self, book_id: UUID, transaction_id: UUID) -> None: ...

    @abstractmethod
    def set_split_state(
        self,
        book_id: UUID,
        transaction_id: UUID,
        split_id: UUID,
        state: ReconcileStateName,
        on: date | None = None,
    ) -> TransactionOut:
        """Move one split to another reconciliation state. RECONCILED can be
        walked back; FROZEN cannot, and the server accepts it from owners only."""

    @abstractmethod
    def reconcile_account(
        self,
        book_id: UUID,
        account_id: UUID,
        split_ids: list[UUID],
        state: ReconcileStateName,
        statement_date: date,
    ) -> None:
        """Mark a batch of an account's splits against one statement: one
        request, one revision."""

    @abstractmethod
    def create_payee(self, book_id: UUID, body: PayeeIn) -> PayeeOut: ...

    @abstractmethod
    def create_account(self, book_id: UUID, body: AccountIn) -> AccountOut: ...

    # -- CSV statement import ----------------------------------------------
    #
    # The whole flow goes to the server: the duplicate and transfer checks
    # want the book as the server sees it, not a replica a write behind. Like
    # creating a book, it needs the connection -- offline, every one of these
    # raises OfflineError.
    @abstractmethod
    def inspect_csv(self, book_id: UUID, data: bytes) -> CsvInspectOut:
        """Read an uploaded statement: its columns, sample rows, a suggested
        mapping. Needs the server."""

    @abstractmethod
    def preview_csv(
        self,
        book_id: UUID,
        account_id: UUID,
        data: bytes,
        mapping: ColumnMappingModel,
    ) -> CsvPreviewOut:
        """Classify every row of the statement -- new, duplicate, transfer --
        with a suggested payee and category. Writes nothing."""

    @abstractmethod
    def commit_csv(
        self,
        book_id: UUID,
        account_id: UUID,
        data: bytes,
        mapping: ColumnMappingModel,
        decisions: list[ImportDecisionIn],
        *,
        uncategorised_account_id: UUID | None = None,
    ) -> StatementImportOut:
        """Post the reviewed statement in one unit of work; the column mapping
        is remembered for this account."""

    @abstractmethod
    def import_mapping(
        self, book_id: UUID, account_id: UUID
    ) -> ColumnMappingModel | None:
        """The mapping last used for this account, or None if never imported to."""

    @abstractmethod
    def save_import_mapping(
        self, book_id: UUID, account_id: UUID, mapping: ColumnMappingModel
    ) -> ColumnMappingModel:
        """Remember a mapping without importing in the same breath."""

    # -- connection ----------------------------------------------------------
    @abstractmethod
    def status(self) -> ConnectionStatus: ...

    @abstractmethod
    def sync(self) -> ConnectionStatus:
        """Talk to the server now: replay what is queued, pull what is new.
        A no-op for the remote client beyond checking that the server answers."""

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release resources."""
