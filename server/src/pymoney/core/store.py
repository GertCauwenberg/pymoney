"""The storage contract, and the in-memory implementation of it.

:class:`Book` used to own its dicts. It no longer does: everything it reads or
writes goes through a :class:`Store`, so the same aggregate -- the same
invariants, the same commands, the same tests -- runs over memory or over SQL.

Three things in this interface are not obvious and matter a lot:

*Atomicity is the store's job.* ``begin``/``commit``/``rollback`` wrap every
command. :class:`MemoryStore` implements rollback with an undo log;
``pymoney.store`` implements it with a database transaction. The Book no longer
knows how.

*Aggregate queries are pushed down.* ``balances_by_account`` exists as a store
method rather than a loop in the Book because a SQL store answers it with one
indexed ``GROUP BY`` instead of materialising every transaction. That single
choice is most of the difference between a fast ledger and a slow one.

*Small tables are expected to be cached.* Commodities, accounts, payees and
prices are tiny and read constantly (every amount needs its commodity), so the
interface hands them back as whole tuples and a SQL store is free to keep them
in memory. Transactions and the change log are not: they grow without bound and
are always queried.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any
from uuid import UUID

from .account import Account
from .changelog import Change, ChangeLog, EntityKind
from .commodity import Commodity
from .entity import Entity
from .payee import Payee
from .price import PriceDB
from .schedule import Schedule
from .settings import BookSettings
from .tag import Tag
from .transaction import Transaction

__all__ = [
    "ChangeLogView",
    "CommandRecord",
    "MemoryStore",
    "Store",
    "StoredBook",
    "TransactionSearch",
]

_ENTITY_KINDS = (
    EntityKind.COMMODITY,
    EntityKind.ACCOUNT,
    EntityKind.PAYEE,
    EntityKind.TAG,
    EntityKind.SCHEDULE,
    EntityKind.TRANSACTION,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class TransactionSearch:
    """What a user is looking for. Every field narrows; all combine with AND.

    ``text`` is a case-insensitive substring of the transaction's memo or
    number, or of any split's memo or number. ``amount_min``/``amount_max``
    bound the *absolute* size of a split, in major units, and match a split
    whose quantity (in its account's commodity) or whose value (in the
    transaction currency) falls in the range -- so "84.20" finds the supermarket
    entry whether you think of it as the money that left the account or the
    expense it became, and a single amount is a range with both ends equal.
    ``account_ids`` and ``payee_id`` match on any split (or the header, for the
    payee); ``tag_id`` matches a transaction any of whose legs wears the tag.
    Results come newest first, ``limit`` at most.
    """

    text: str | None = None
    amount_min: Fraction | None = None
    amount_max: Fraction | None = None
    account_ids: frozenset[UUID] | None = None
    payee_id: UUID | None = None
    tag_id: UUID | None = None
    start: date | None = None
    end: date | None = None
    limit: int | None = None

    @property
    def is_empty(self) -> bool:
        """True when nothing narrows: the store would return the whole ledger."""
        return (
            not (self.text or "").strip()
            and self.amount_min is None
            and self.amount_max is None
            and self.account_ids is None
            and self.payee_id is None
            and self.tag_id is None
            and self.start is None
            and self.end is None
        )

    def matches(self, transaction: Transaction) -> bool:
        """The reference implementation, in Python, for stores without SQL.

        The SQL store pushes the same predicate down; the contract tests hold
        the two to the same answers.
        """
        t = transaction
        if self.start is not None and t.date < self.start:
            return False
        if self.end is not None and t.date > self.end:
            return False
        if self.account_ids is not None and not any(
            s.account_id in self.account_ids for s in t.splits
        ):
            return False
        if (
            self.payee_id is not None
            and t.payee_id != self.payee_id
            and not any(s.payee_id == self.payee_id for s in t.splits)
        ):
            return False
        if self.tag_id is not None and not any(
            self.tag_id in s.tag_ids for s in t.splits
        ):
            return False
        needle = (self.text or "").strip().casefold()
        if needle:
            haystacks = [
                t.memo,
                t.number,
                *(s.memo for s in t.splits),
                *(s.number for s in t.splits),
            ]
            if not any(needle in h.casefold() for h in haystacks):
                return False
        if self.amount_min is not None or self.amount_max is not None:
            lo = self.amount_min if self.amount_min is not None else Fraction(0)
            hi = self.amount_max

            def within(value: Fraction) -> bool:
                return value >= lo and (hi is None or value <= hi)

            if not any(
                within(abs(s.quantity.as_fraction)) or within(abs(s.value.as_fraction))
                for s in t.splits
            ):
                return False
        return True


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredBook:
    """The book row: identity, settings and the revision watermark."""

    id: UUID
    name: str = "PyMoney Book"
    base_currency_id: UUID | None = None
    revision: int = 0
    """Highest revision assigned so far."""
    settings_revision: int = 0
    """Revision at which the settings themselves last changed."""

    def as_settings(self) -> BookSettings:
        return BookSettings(
            id=self.id,
            book_id=self.id,
            name=self.name,
            base_currency_id=self.base_currency_id,
            revision=self.settings_revision,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandRecord:
    """What a book remembers about a command it has applied, by ``command_id``.

    Enough to answer a replay after a restart -- the revision it produced and
    which entity it was about -- and to refuse a replay that carries a different
    payload under the same key.
    """

    command_id: UUID
    fingerprint: str
    revision: int
    command: str
    entity_kind: EntityKind | None = None
    entity_id: UUID | None = None


class Store(ABC):
    """Everything a :class:`~pymoney.core.book.Book` needs from persistence."""

    # -- lifecycle ---------------------------------------------------------
    @abstractmethod
    def begin(self) -> None:
        """Open a unit of work. One command, one unit of work."""

    @abstractmethod
    def commit(self) -> None:
        """Make everything written since :meth:`begin` durable."""

    @abstractmethod
    def rollback(self) -> None:
        """Discard everything written since :meth:`begin`."""

    @abstractmethod
    def savepoint(self) -> AbstractContextManager[None]:
        """A nested, individually revertible scope inside a unit of work.

        This is what lets one command fail without taking its batch with it. An
        importer crossing ten years of records should not lose the lot because
        of one malformed row, but it must not keep that row either.
        """

    def close(self) -> None:  # noqa: B027 - optional hook, memory needs nothing
        """Release resources. Stores that hold none need not override this."""

    # -- the book row ------------------------------------------------------
    @abstractmethod
    def load_book(self) -> StoredBook:
        """The book row, creating it on first use."""

    @abstractmethod
    def save_book(self, book: StoredBook) -> None: ...

    # -- entities ----------------------------------------------------------
    @abstractmethod
    def get(self, kind: EntityKind, entity_id: UUID) -> Any | None: ...

    def exists(self, kind: EntityKind, entity_id: UUID) -> bool:
        """Whether an entity is present, without loading it.

        Worth its own method: a transaction is several rows, and the commands
        that only need to know "is this id taken?" should not pay to assemble one.
        """
        return self.get(kind, entity_id) is not None

    @abstractmethod
    def put(self, kind: EntityKind, entity: Entity, *, created: bool = False) -> None:
        """Insert or replace an entity.

        ``created`` is not a hint to be verified -- it is what the command
        already established, so a store must not spend a query asking the
        database whether the row is there. If it is wrong, the resulting
        constraint violation is the correct outcome.
        """

    @abstractmethod
    def delete(self, kind: EntityKind, entity_id: UUID) -> bool:
        """Remove an entity; False if it was not there."""

    @abstractmethod
    def commodities(self) -> tuple[Commodity, ...]: ...

    @abstractmethod
    def accounts(self) -> tuple[Account, ...]: ...

    @abstractmethod
    def payees(self) -> tuple[Payee, ...]: ...

    @abstractmethod
    def tags(self) -> tuple[Tag, ...]: ...

    @abstractmethod
    def schedules(self) -> tuple[Schedule, ...]: ...

    @abstractmethod
    def children_ids(self, parent_id: UUID | None) -> tuple[UUID, ...]:
        """Ids of the accounts directly under ``parent_id`` (None = top level)."""

    # -- transactions ------------------------------------------------------
    @abstractmethod
    def transaction_count(self) -> int: ...

    @abstractmethod
    def transaction_ids(self) -> tuple[UUID, ...]: ...

    @abstractmethod
    def transactions(
        self,
        *,
        account_ids: Iterable[UUID] | None = None,
        start: date | None = None,
        end: date | None = None,
        from_key: tuple[date, UUID] | None = None,
        limit: int | None = None,
    ) -> tuple[Transaction, ...]:
        """Matching transactions, ordered by ``(date, id)``.

        ``from_key`` is a keyset position: only transactions whose ``(date, id)``
        is at or after it are returned (inclusive, so a caller paging through
        *splits* can pick up the remaining legs of the cursor's transaction).
        ``limit`` caps the number of transactions. Both are pushed down so that
        one page of a register costs one page, not the whole history.
        """

    @abstractmethod
    def transactions_by_ids(self, ids: Iterable[UUID]) -> tuple[Transaction, ...]:
        """The named transactions, in one round trip; unknown ids are skipped."""

    @abstractmethod
    def posting_count(self, account_id: UUID) -> int:
        """How many transactions touch an account. Cheap existence check."""

    @abstractmethod
    def balances_by_account(
        self,
        *,
        account_ids: Iterable[UUID] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[UUID, int]:
        """Summed split quantities in minor units, per account.

        Pushed down to the storage engine on purpose: this is the hot path for
        every balance, register total and report. ``account_ids`` matters as much
        as the dates -- one account's balance must not cost a full aggregate over
        the ledger.
        """

    @abstractmethod
    def find_imported(self, imported_id: str) -> UUID | None: ...

    @abstractmethod
    def find_imported_prefix(self, prefix: str) -> str | None:
        """Any existing ``imported_id`` starting with ``prefix``, or None.

        Lets an importer tell "this book has already had a KMyMoney file loaded
        into it" *before* it starts creating accounts, instead of discovering it
        one duplicate transaction at a time.
        """

    @abstractmethod
    def transactions_for_payee(self, payee_id: UUID) -> tuple[Transaction, ...]:
        """Transactions referencing a payee, on the header or on any split."""

    @abstractmethod
    def transactions_for_tag(self, tag_id: UUID) -> tuple[Transaction, ...]:
        """Transactions any of whose legs wears ``tag_id``."""

    @abstractmethod
    def search(self, query: TransactionSearch) -> tuple[Transaction, ...]:
        """Transactions matching every criterion of ``query``, newest first.

        Must agree with :meth:`TransactionSearch.matches` on every transaction;
        a SQL store pushes the predicate down so a search costs a query, not a
        scan of the ledger through Python.
        """

    # -- prices ------------------------------------------------------------
    @property
    @abstractmethod
    def price_db(self) -> PriceDB:
        """Price lookup. Small enough to be held in memory by any store."""

    # -- change log --------------------------------------------------------
    @abstractmethod
    def append_changes(self, changes: Iterable[Change]) -> None: ...

    @abstractmethod
    def changes_since(
        self,
        revision: int,
        *,
        max_revisions: int | None = None,
        max_rows: int | None = None,
    ) -> tuple[Change, ...]:
        """Changes above ``revision``, optionally only the next few revisions.

        ``max_revisions`` and ``max_rows`` let a paged sync pull avoid dragging
        the whole log back just to use the first page of it. ``max_rows`` is
        soft: a revision is never returned half, so the last one is completed
        even if that overshoots.
        """

    @abstractmethod
    def changes_at(self, revision: int) -> tuple[Change, ...]: ...

    # -- applied commands --------------------------------------------------
    @abstractmethod
    def record_command(self, record: CommandRecord) -> None:
        """Remember an applied command, inside its own unit of work.

        Idempotency used to live in a dict on the Book, which meant a restart
        forgot every key and a retried request posted twice. The record goes
        through the same transaction as the command, so it exists exactly when
        the command's effects do.
        """

    @abstractmethod
    def find_command(self, command_id: UUID) -> CommandRecord | None: ...

    @abstractmethod
    def changes_for_entity(self, entity_id: UUID) -> tuple[Change, ...]: ...

    @abstractmethod
    def all_changes(self) -> tuple[Change, ...]:
        """The whole log. For small books, diagnostics and tests."""

    @abstractmethod
    def change_count(self) -> int: ...


class ChangeLogView:
    """Read-only :class:`~pymoney.core.changelog.ChangeLog` facade over a store.

    The log may be far too large to hold in memory, so this exposes the same
    query surface without implying that it is all local.
    """

    __slots__ = ("_store",)

    def __init__(self, store: Store) -> None:
        self._store = store

    def since(
        self,
        revision: int,
        *,
        max_revisions: int | None = None,
        max_rows: int | None = None,
    ) -> tuple[Change, ...]:
        return self._store.changes_since(
            revision, max_revisions=max_revisions, max_rows=max_rows
        )

    def at(self, revision: int) -> tuple[Change, ...]:
        return self._store.changes_at(revision)

    def for_entity(self, entity_id: UUID) -> tuple[Change, ...]:
        return self._store.changes_for_entity(entity_id)

    def tombstones(self, since: int = 0) -> tuple[Change, ...]:
        return tuple(c for c in self.since(since) if c.is_delete)

    @property
    def head(self) -> int:
        changes = self._store.changes_since(0)
        return changes[-1].revision if changes else 0

    def __len__(self) -> int:
        return self._store.change_count()

    def __iter__(self) -> Iterator[Change]:
        return iter(self._store.all_changes())

    def __getitem__(self, index: int) -> Change:
        return self._store.all_changes()[index]

    def __repr__(self) -> str:
        return f"ChangeLogView({len(self)} changes, head=r{self.head})"


class MemoryStore(Store):
    """Reference implementation: plain dicts, with an undo log for rollback.

    This is the specification the SQL store has to match, which is why it stays
    deliberately simple -- no cleverness to reproduce, just the rules.
    """

    def __init__(self, book_id: UUID) -> None:
        self._book = StoredBook(id=book_id)
        self._entities: dict[EntityKind, dict[UUID, Any]] = {
            kind: {} for kind in _ENTITY_KINDS
        }
        self._children: dict[UUID | None, list[UUID]] = {}
        self._txns_by_account: dict[UUID, set[UUID]] = {}
        self._prices = PriceDB()
        self._log = ChangeLog()
        self._commands: dict[UUID, CommandRecord] = {}
        self._undo: list[Callable[[], None]] | None = None

    # -- lifecycle ---------------------------------------------------------
    def begin(self) -> None:
        self._undo = []

    def commit(self) -> None:
        self._undo = None

    def rollback(self) -> None:
        for undo in reversed(self._undo or ()):
            undo()
        self._undo = None

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        assert self._undo is not None, "a savepoint needs a unit of work"
        mark = len(self._undo)
        try:
            yield
        except Exception:
            # Unwind only as far as the mark, leaving earlier work in place.
            while len(self._undo) > mark:
                self._undo.pop()()
            raise

    def _record_undo(self, action: Callable[[], None]) -> None:
        assert self._undo is not None, "mutations must happen inside a unit of work"
        self._undo.append(action)

    # -- the book row ------------------------------------------------------
    def load_book(self) -> StoredBook:
        return self._book

    def save_book(self, book: StoredBook) -> None:
        previous = self._book
        self._book = book
        self._record_undo(lambda: setattr(self, "_book", previous))

    # -- entities ----------------------------------------------------------
    def get(self, kind: EntityKind, entity_id: UUID) -> Any | None:
        if kind is EntityKind.PRICE:
            return self._prices.get(entity_id)
        return self._entities[kind].get(entity_id)

    def put(self, kind: EntityKind, entity: Entity, *, created: bool = False) -> None:
        del created  # dict assignment covers both cases
        if kind is EntityKind.PRICE:
            previous_price = self._prices.get(entity.id)
            self._prices.add(entity)  # type: ignore[arg-type]

            def undo_price() -> None:
                self._prices.remove(entity.id)
                if previous_price is not None:
                    self._prices.add(previous_price)

            self._record_undo(undo_price)
            return

        table = self._entities[kind]
        previous = table.get(entity.id)
        table[entity.id] = entity
        self._reindex(kind, previous, entity)

        def undo_entity() -> None:
            if previous is None:
                table.pop(entity.id, None)
                self._reindex(kind, entity, None)
            else:
                table[entity.id] = previous
                self._reindex(kind, entity, previous)

        self._record_undo(undo_entity)

    def delete(self, kind: EntityKind, entity_id: UUID) -> bool:
        if kind is EntityKind.PRICE:
            previous_price = self._prices.remove(entity_id)
            if previous_price is None:
                return False

            def undo_price_delete() -> None:
                self._prices.add(previous_price)

            self._record_undo(undo_price_delete)
            return True

        table = self._entities[kind]
        previous = table.pop(entity_id, None)
        if previous is None:
            return False
        self._reindex(kind, previous, None)

        def undo_entity() -> None:
            table[entity_id] = previous
            self._reindex(kind, None, previous)

        self._record_undo(undo_entity)
        return True

    def _reindex(self, kind: EntityKind, old: Any | None, new: Any | None) -> None:
        """Keep the derived indexes in step. A missing entry is a bug: let it raise."""
        if kind is EntityKind.ACCOUNT:
            if old is not None:
                siblings = self._children[old.parent_id]
                siblings.remove(old.id)
                if not siblings:
                    del self._children[old.parent_id]
            if new is not None:
                self._children.setdefault(new.parent_id, []).append(new.id)
        elif kind is EntityKind.TRANSACTION:
            if old is not None:
                for account_id in set(old.account_ids):
                    posted = self._txns_by_account[account_id]
                    posted.discard(old.id)
                    if not posted:
                        del self._txns_by_account[account_id]
            if new is not None:
                for account_id in set(new.account_ids):
                    self._txns_by_account.setdefault(account_id, set()).add(new.id)

    def commodities(self) -> tuple[Commodity, ...]:
        return tuple(self._entities[EntityKind.COMMODITY].values())

    def accounts(self) -> tuple[Account, ...]:
        return tuple(self._entities[EntityKind.ACCOUNT].values())

    def payees(self) -> tuple[Payee, ...]:
        return tuple(self._entities[EntityKind.PAYEE].values())

    def tags(self) -> tuple[Tag, ...]:
        return tuple(self._entities[EntityKind.TAG].values())

    def schedules(self) -> tuple[Schedule, ...]:
        return tuple(self._entities[EntityKind.SCHEDULE].values())

    def children_ids(self, parent_id: UUID | None) -> tuple[UUID, ...]:
        return tuple(self._children.get(parent_id, ()))

    # -- transactions ------------------------------------------------------
    def transaction_count(self) -> int:
        return len(self._entities[EntityKind.TRANSACTION])

    def transaction_ids(self) -> tuple[UUID, ...]:
        return tuple(self._entities[EntityKind.TRANSACTION])

    def transactions(
        self,
        *,
        account_ids: Iterable[UUID] | None = None,
        start: date | None = None,
        end: date | None = None,
        from_key: tuple[date, UUID] | None = None,
        limit: int | None = None,
    ) -> tuple[Transaction, ...]:
        table = self._entities[EntityKind.TRANSACTION]
        if account_ids is None:
            candidates: Iterable[Transaction] = table.values()
        else:
            wanted: set[UUID] = set()
            for account_id in account_ids:
                wanted |= self._txns_by_account.get(account_id, set())
            candidates = (table[i] for i in wanted)
        mark = None if from_key is None else (from_key[0], from_key[1].int)
        out = [
            t
            for t in candidates
            if (start is None or t.date >= start)
            and (end is None or t.date <= end)
            and (mark is None or (t.date, t.id.int) >= mark)
        ]
        out.sort(key=lambda t: (t.date, t.id.int))
        if limit is not None:
            out = out[:limit]
        return tuple(out)

    def transactions_by_ids(self, ids: Iterable[UUID]) -> tuple[Transaction, ...]:
        table = self._entities[EntityKind.TRANSACTION]
        return tuple(table[i] for i in ids if i in table)

    def posting_count(self, account_id: UUID) -> int:
        return len(self._txns_by_account.get(account_id, ()))

    def balances_by_account(
        self,
        *,
        account_ids: Iterable[UUID] | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[UUID, int]:
        wanted = None if account_ids is None else set(account_ids)
        totals: dict[UUID, int] = {}
        for transaction in self.transactions(account_ids=wanted, start=start, end=end):
            for split in transaction.splits:
                if wanted is not None and split.account_id not in wanted:
                    continue
                totals[split.account_id] = (
                    totals.get(split.account_id, 0) + split.quantity.minor
                )
        return totals

    def find_imported(self, imported_id: str) -> UUID | None:
        for transaction in self._entities[EntityKind.TRANSACTION].values():
            if transaction.imported_id == imported_id:
                return transaction.id
        return None

    def find_imported_prefix(self, prefix: str) -> str | None:
        for transaction in self._entities[EntityKind.TRANSACTION].values():
            if transaction.imported_id and transaction.imported_id.startswith(prefix):
                return transaction.imported_id
        return None

    def transactions_for_payee(self, payee_id: UUID) -> tuple[Transaction, ...]:
        out = [
            t
            for t in self._entities[EntityKind.TRANSACTION].values()
            if t.payee_id == payee_id or any(s.payee_id == payee_id for s in t.splits)
        ]
        out.sort(key=lambda t: (t.date, t.id.int))
        return tuple(out)

    def transactions_for_tag(self, tag_id: UUID) -> tuple[Transaction, ...]:
        out = [
            t
            for t in self._entities[EntityKind.TRANSACTION].values()
            if any(tag_id in s.tag_ids for s in t.splits)
        ]
        out.sort(key=lambda t: (t.date, t.id.int))
        return tuple(out)

    def search(self, query: TransactionSearch) -> tuple[Transaction, ...]:
        table = self._entities[EntityKind.TRANSACTION]
        if query.account_ids is not None:
            ids: set[UUID] = set()
            for account_id in query.account_ids:
                ids |= self._txns_by_account.get(account_id, set())
            candidates: Iterable[Transaction] = (table[i] for i in ids)
        else:
            candidates = table.values()
        out = [t for t in candidates if query.matches(t)]
        out.sort(key=lambda t: (t.date, t.id.int), reverse=True)
        if query.limit is not None:
            out = out[: query.limit]
        return tuple(out)

    # -- prices ------------------------------------------------------------
    @property
    def price_db(self) -> PriceDB:
        return self._prices

    # -- change log --------------------------------------------------------
    def append_changes(self, changes: Iterable[Change]) -> None:
        before = len(self._log)
        self._log.extend(changes)
        self._record_undo(lambda: self._log.discard_after(before))

    def changes_since(
        self,
        revision: int,
        *,
        max_revisions: int | None = None,
        max_rows: int | None = None,
    ) -> tuple[Change, ...]:
        found = self._log.since(revision)
        if max_revisions is not None:
            cutoff = sorted({c.revision for c in found})[:max_revisions]
            allowed = set(cutoff)
            found = tuple(c for c in found if c.revision in allowed)
        if max_rows is not None and len(found) > max_rows:
            # Complete the revision the cap landed in rather than split it.
            last = found[max_rows - 1].revision
            found = tuple(c for c in found if c.revision <= last)
        return found

    def changes_at(self, revision: int) -> tuple[Change, ...]:
        return self._log.at(revision)

    def record_command(self, record: CommandRecord) -> None:
        self._commands[record.command_id] = record

        def undo() -> None:
            self._commands.pop(record.command_id, None)

        self._record_undo(undo)

    def find_command(self, command_id: UUID) -> CommandRecord | None:
        return self._commands.get(command_id)

    def changes_for_entity(self, entity_id: UUID) -> tuple[Change, ...]:
        return self._log.for_entity(entity_id)

    def all_changes(self) -> tuple[Change, ...]:
        return tuple(self._log)

    def change_count(self) -> int:
        return len(self._log)

    def __repr__(self) -> str:
        return (
            f"MemoryStore({self.transaction_count()} transactions, "
            f"{len(self.accounts())} accounts)"
        )
