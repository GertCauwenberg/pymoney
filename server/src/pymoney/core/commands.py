"""Commands: the only way to change a book (decision #6).

Clients never mutate state directly.  They send an *intent* -- one of the frozen
dataclasses below -- and the book validates it, applies it atomically, stamps it
with a new revision and hands back a :class:`CommandResult`.  The same objects
are the natural payload of an HTTP endpoint and of the replica protocol, so the
desktop app in offline mode and the web client in online mode speak the same
language.

Every command carries an optional ``command_id``.  When present it makes the
command idempotent: replaying it (a mobile client retrying over a flaky
connection) returns the original result instead of applying it twice.

Partial updates use the :data:`UNSET` sentinel rather than ``None``, because
``None`` is a meaningful value for fields like ``parent_id``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from fractions import Fraction
from typing import Any
from uuid import UUID

from .account import AccountType
from .commodity import CommodityKind
from .posting import LegSpec
from .recurrence import Recurrence
from .rounding import DEFAULT_ROUNDING, RoundingMode
from .schedule import ScheduleTemplate
from .transaction import ReconcileState

__all__ = [
    "UNSET",
    "AddPrice",
    "Command",
    "CommandResult",
    "CreateAccount",
    "CreateCommodity",
    "CreatePayee",
    "CreateSchedule",
    "DeleteAccount",
    "DeleteCommodity",
    "DeletePayee",
    "DeletePrice",
    "DeleteSchedule",
    "DeleteTransaction",
    "MergeAccounts",
    "MergePayees",
    "MoveAccount",
    "PostScheduledOccurrence",
    "PostTransaction",
    "ReconcileAccount",
    "RenameAccount",
    "SetAccountClosed",
    "SetSplitReconcileState",
    "SkipScheduledOccurrence",
    "Unset",
    "UpdateAccount",
    "UpdateCommodity",
    "UpdatePayee",
    "UpdateSchedule",
    "UpdateTransaction",
    "fingerprint",
]


class Unset(Enum):
    """Sentinel type for "field not supplied" in partial updates."""

    TOKEN = "UNSET"

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = Unset.TOKEN

type Maybe[T] = T | Unset
"""A field that may be left unsupplied in a partial update."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Command:
    """Base class for all intents."""

    command_id: UUID | None = None
    """Idempotency key.  Replaying the same key is a no-op returning the
    original result."""
    actor: str = ""
    """Who is asking; recorded in the change log."""

    @property
    def command_name(self) -> str:
        """The command's type name, as recorded in the change log.

        Deliberately not called ``name``: several commands carry a ``name``
        *field* (``CreateAccount``, ``CreatePayee``), which would shadow it.
        """
        return type(self).__name__


def fingerprint(command: Command) -> str:
    """A stable digest of *what* a command asks for.

    Two requests carrying the same ``command_id`` must be the same request. A
    client that reuses a key for a different payload has a bug, and silently
    handing it the first request's result would hide it -- so the book compares
    this digest and refuses a mismatch.

    Identity and audit fields are left out: ``command_id`` is what is being
    scoped, ``actor`` is who sent it, and a :class:`~pymoney.core.posting.LegSpec`
    gets a fresh ``id`` on every construction, so none of them says anything
    about the intent.
    """

    def canonical(value: Any, *, top: bool = False) -> Any:
        if is_dataclass(value) and not isinstance(value, type):
            skipped = {"command_id", "actor", "transaction_id"} if top else {"id"}
            return [
                type(value).__name__,
                {
                    f.name: canonical(getattr(value, f.name))
                    for f in fields(value)
                    if f.name not in skipped
                },
            ]
        if isinstance(value, tuple | list):
            return [canonical(v) for v in value]
        if isinstance(value, dict):
            return {str(k): canonical(v) for k, v in sorted(value.items())}
        if isinstance(value, Enum):
            return [type(value).__name__, value.name]
        if isinstance(value, UUID | Fraction | date | datetime):
            return str(value)
        if value is None or isinstance(value, str | int | bool):
            return value
        return repr(value)

    return hashlib.sha256(repr(canonical(command, top=True)).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandResult:
    """What the server tells the client after accepting a command.

    ``revision`` is the client's new sync watermark; ``changes`` is exactly what
    a ``/changes`` poll would have returned for this command, so a client can
    apply it to its local cache without a second round trip.
    """

    revision: int
    command: str
    changes: tuple[Any, ...] = ()
    entity: Any = None
    """The primary entity created or updated, when there is one."""
    replayed: bool = False
    """True when this result came from the idempotency cache."""

    @property
    def entity_id(self) -> UUID | None:
        return getattr(self.entity, "id", None)


# -- commodities ----------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateCommodity(Command):
    code: str
    name: str = ""
    kind: CommodityKind = CommodityKind.CURRENCY
    minor_unit_factor: int = 100
    display_precision: int | None = None
    symbol: str | None = None
    isin: str | None = None
    quote_source: str | None = None
    id: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateCommodity(Command):
    commodity_id: UUID
    name: Maybe[str] = UNSET
    symbol: Maybe[str | None] = UNSET
    display_precision: Maybe[int | None] = UNSET
    isin: Maybe[str | None] = UNSET
    quote_source: Maybe[str | None] = UNSET
    code: Maybe[str] = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteCommodity(Command):
    commodity_id: UUID


# -- book settings --------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class InitialiseBook(Command):
    """Record a brand-new book's identity and create its base currency.

    Book creation is an operation like any other, so it gets a command and a
    revision.  Without one, the base currency would exist on the server but be
    invisible to a replica pulling from revision 0 -- the book would appear to
    have accounts denominated in a commodity that was never announced.

    Everything happens in a single revision, so a fresh client receives the
    whole bootstrap as one batch.
    """

    name: str = "PyMoney Book"
    base_currency: Any = None
    """A :class:`~pymoney.core.commodity.Commodity` to adopt, or ``None``."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SetBaseCurrency(Command):
    """Change the book's reporting currency.

    Safe at any time: it changes the default target of reports and the default
    commodity for new accounts, but reinterprets no stored amount.
    """

    commodity_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class RenameBook(Command):
    name: str


# -- accounts -------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateAccount(Command):
    name: str
    type: AccountType
    commodity_id: UUID | None = None
    """Defaults to the book's base currency."""
    parent_id: UUID | None = None
    description: str = ""
    code: str = ""
    placeholder: bool = False
    notes: str = ""
    institution: str = ""
    iban: str = ""
    id: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateAccount(Command):
    account_id: UUID
    description: Maybe[str] = UNSET
    code: Maybe[str] = UNSET
    notes: Maybe[str] = UNSET
    institution: Maybe[str] = UNSET
    iban: Maybe[str] = UNSET
    placeholder: Maybe[bool] = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class SetAccountCommodity(Command):
    """Correct the commodity of an account that has never been posted to.

    Deliberately narrow.  Once splits exist, their ``quantity`` is denominated
    in the account's commodity, so changing it would silently reinterpret every
    historical amount -- 1,000 JPY becoming 1,000 EUR.  Move the money instead:
    create the right account and use :class:`MergeAccounts`, or post a transfer.
    """

    account_id: UUID
    commodity_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class RenameAccount(Command):
    """Rename in place.  Paths are derived, so nothing else has to change."""

    account_id: UUID
    name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MoveAccount(Command):
    """Re-parent an account, keeping its history."""

    account_id: UUID
    new_parent_id: UUID | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SetAccountClosed(Command):
    account_id: UUID
    closed: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class MergeAccounts(Command):
    """Move every split and child of ``source_id`` onto ``target_id``."""

    source_id: UUID
    target_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteAccount(Command):
    account_id: UUID


# -- payees ---------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CreatePayee(Command):
    name: str
    notes: str = ""
    address: str = ""
    match_keys: tuple[str, ...] = ()
    default_account_id: UUID | None = None
    id: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdatePayee(Command):
    payee_id: UUID
    name: Maybe[str] = UNSET
    notes: Maybe[str] = UNSET
    address: Maybe[str] = UNSET
    match_keys: Maybe[tuple[str, ...]] = UNSET
    default_account_id: Maybe[UUID | None] = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class MergePayees(Command):
    """Repoint every reference from ``source_ids`` to ``target_id``."""

    source_ids: tuple[UUID, ...]
    target_id: UUID
    keep_match_keys: bool = True
    """Absorb the merged names as match keys, so imports still recognise them."""


@dataclass(frozen=True, slots=True, kw_only=True)
class DeletePayee(Command):
    payee_id: UUID


# -- scheduled / recurring transactions ---------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateSchedule(Command):
    """A recurring transaction: a template, a recurrence, and a cursor."""

    name: str
    template: ScheduleTemplate
    recurrence: Recurrence
    auto_post: bool = False
    paused: bool = False
    occurrences_done: int = 0
    """Usually 0; an importer sets it for a schedule partway through its run."""
    id: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateSchedule(Command):
    schedule_id: UUID
    name: Maybe[str] = UNSET
    template: Maybe[ScheduleTemplate] = UNSET
    recurrence: Maybe[Recurrence] = UNSET
    auto_post: Maybe[bool] = UNSET
    paused: Maybe[bool] = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteSchedule(Command):
    schedule_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class PostScheduledOccurrence(Command):
    """Post the occurrence currently due, and advance the schedule -- one
    revision for both.

    ``on`` pins which occurrence (it must be the one due); leave it unset for
    "the oldest that is due". ``overrides`` lets the confirming user adjust the
    amounts or the memo for this one occurrence without touching the template.
    The posted transaction carries ``imported_id = sched:<id>:<date>``, so a
    second client posting the same occurrence converges instead of doubling.
    """

    schedule_id: UUID
    on: Maybe[date] = UNSET
    legs: Maybe[tuple[LegSpec, ...]] = UNSET
    memo: Maybe[str] = UNSET
    payee_id: Maybe[UUID | None] = UNSET
    number: Maybe[str] = UNSET
    transaction_id: UUID | None = None
    """The id for the posted transaction; chosen by an offline client so its
    replica row and the server's are one row on replay."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SkipScheduledOccurrence(Command):
    """Advance past the occurrence currently due without posting anything."""

    schedule_id: UUID
    on: Maybe[date] = UNSET


# -- transactions ---------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class PostTransaction(Command):
    """Create a transaction from legs expressed as exact rationals."""

    date: date
    legs: tuple[LegSpec, ...]
    currency_id: UUID | None = None
    """Defaults to the book's base currency."""
    memo: str = ""
    payee_id: UUID | None = None
    number: str = ""
    rounding: RoundingMode = DEFAULT_ROUNDING
    imbalance_account_id: UUID | None = None
    entered_at: datetime | None = None
    imported_id: str | None = None
    id: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateTransaction(Command):
    """Replace a transaction's header and/or its whole set of legs.

    Splits are replaced wholesale rather than patched individually: a partial
    split edit can leave the transaction unbalanced, and we never want an
    unbalanced row to exist even transiently.
    """

    transaction_id: UUID
    date: Maybe[date] = UNSET
    legs: Maybe[tuple[LegSpec, ...]] = UNSET
    memo: Maybe[str] = UNSET
    payee_id: Maybe[UUID | None] = UNSET
    number: Maybe[str] = UNSET
    currency_id: Maybe[UUID] = UNSET
    rounding: Maybe[RoundingMode] = UNSET
    imbalance_account_id: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteTransaction(Command):
    transaction_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class SetSplitReconcileState(Command):
    transaction_id: UUID
    split_id: UUID
    state: ReconcileState
    on: date | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconcileAccount(Command):
    """Mark a batch of splits in one account, the way a reconcile dialog does."""

    account_id: UUID
    split_ids: tuple[UUID, ...]
    state: ReconcileState = ReconcileState.RECONCILED
    statement_date: date | None = None


# -- prices ---------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class AddPrice(Command):
    base_id: UUID
    quote_id: UUID
    rate: Fraction | str | int
    effective_date: date
    as_of: datetime | None = None
    """Knowledge timestamp; defaults to the server clock (decision #9)."""
    source: str = "user"
    id: UUID | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeletePrice(Command):
    price_id: UUID
