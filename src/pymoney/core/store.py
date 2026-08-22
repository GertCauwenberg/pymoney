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
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from .account import Account
from .changelog import Change, ChangeLog, EntityKind
from .commodity import Commodity
from .entity import Entity
from .payee import Payee
from .price import PriceDB
from .settings import BookSettings
from .transaction import Transaction

__all__ = ["ChangeLogView", "MemoryStore", "Store", "StoredBook"]

_ENTITY_KINDS = (
    EntityKind.COMMODITY,
    EntityKind.ACCOUNT,
    EntityKind.PAYEE,
    EntityKind.TRANSACTION,
)


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

    @abstractmethod
    def put(self, kind: EntityKind, entity: Entity) -> None: ...

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
    ) -> tuple[Transaction, ...]:
        """Matching transactions, ordered by ``(date, id)``."""

    @abstractmethod
    def posting_count(self, account_id: UUID) -> int:
        """How many transactions touch an account. Cheap existence check."""

    @abstractmethod
    def balances_by_account(
        self, *, start: date | None = None, end: date | None = None
    ) -> dict[UUID, int]:
        """Summed split quantities in minor units, per account.

        Pushed down to the storage engine on purpose: this is the hot path for
        every balance, register total and report.
        """

    @abstractmethod
    def find_imported(self, imported_id: str) -> UUID | None: ...

    @abstractmethod
    def transactions_for_payee(self, payee_id: UUID) -> tuple[Transaction, ...]:
        """Transactions referencing a payee, on the header or on any split."""

    # -- prices ------------------------------------------------------------
    @property
    @abstractmethod
    def price_db(self) -> PriceDB:
        """Price lookup. Small enough to be held in memory by any store."""

    # -- change log --------------------------------------------------------
    @abstractmethod
    def append_changes(self, changes: Iterable[Change]) -> None: ...

    @abstractmethod
    def changes_since(self, revision: int) -> tuple[Change, ...]: ...

    @abstractmethod
    def changes_at(self, revision: int) -> tuple[Change, ...]: ...

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

    def since(self, revision: int) -> tuple[Change, ...]:
        return self._store.changes_since(revision)

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

    def put(self, kind: EntityKind, entity: Entity) -> None:
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
    ) -> tuple[Transaction, ...]:
        table = self._entities[EntityKind.TRANSACTION]
        if account_ids is None:
            candidates: Iterable[Transaction] = table.values()
        else:
            wanted: set[UUID] = set()
            for account_id in account_ids:
                wanted |= self._txns_by_account.get(account_id, set())
            candidates = (table[i] for i in wanted)
        out = [
            t
            for t in candidates
            if (start is None or t.date >= start) and (end is None or t.date <= end)
        ]
        out.sort(key=lambda t: (t.date, t.id.int))
        return tuple(out)

    def posting_count(self, account_id: UUID) -> int:
        return len(self._txns_by_account.get(account_id, ()))

    def balances_by_account(
        self, *, start: date | None = None, end: date | None = None
    ) -> dict[UUID, int]:
        totals: dict[UUID, int] = {}
        for transaction in self.transactions(start=start, end=end):
            for split in transaction.splits:
                totals[split.account_id] = (
                    totals.get(split.account_id, 0) + split.quantity.minor
                )
        return totals

    def find_imported(self, imported_id: str) -> UUID | None:
        for transaction in self._entities[EntityKind.TRANSACTION].values():
            if transaction.imported_id == imported_id:
                return transaction.id
        return None

    def transactions_for_payee(self, payee_id: UUID) -> tuple[Transaction, ...]:
        out = [
            t
            for t in self._entities[EntityKind.TRANSACTION].values()
            if t.payee_id == payee_id or any(s.payee_id == payee_id for s in t.splits)
        ]
        out.sort(key=lambda t: (t.date, t.id.int))
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

    def changes_since(self, revision: int) -> tuple[Change, ...]:
        return self._log.since(revision)

    def changes_at(self, revision: int) -> tuple[Change, ...]:
        return self._log.at(revision)

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
