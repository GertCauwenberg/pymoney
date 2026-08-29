"""The interface the desktop UI talks to, and the errors it hears.

One interface, two implementations (decision: the server is authoritative):

:class:`~pymoney.desktop.remote.RemoteClient`
    Holds nothing but a small read cache. Every write is an HTTP request to the
    same API the web frontend uses, and fails the moment the network does.

:class:`~pymoney.desktop.offline.OfflineClient`
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

from ..api.schemas import (
    AccountBalanceOut,
    AccountIn,
    AccountOut,
    BalanceSheetOut,
    BookIn,
    BookOut,
    CommodityOut,
    PayeeIn,
    PayeeOut,
    RegisterPage,
    TransactionIn,
    TransactionOut,
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
]


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
        self, book_id: UUID, *, as_of: date | None = None
    ) -> BalanceSheetOut: ...

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
    def post_transaction(self, book_id: UUID, body: TransactionIn) -> TransactionOut: ...

    @abstractmethod
    def update_transaction(
        self, book_id: UUID, transaction_id: UUID, body: TransactionUpdateIn
    ) -> TransactionOut: ...

    @abstractmethod
    def delete_transaction(self, book_id: UUID, transaction_id: UUID) -> None: ...

    @abstractmethod
    def create_payee(self, book_id: UUID, body: PayeeIn) -> PayeeOut: ...

    @abstractmethod
    def create_account(self, book_id: UUID, body: AccountIn) -> AccountOut: ...

    # -- connection ----------------------------------------------------------
    @abstractmethod
    def status(self) -> ConnectionStatus: ...

    @abstractmethod
    def sync(self) -> ConnectionStatus:
        """Talk to the server now: replay what is queued, pull what is new.
        A no-op for the remote client beyond checking that the server answers."""

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release resources."""
