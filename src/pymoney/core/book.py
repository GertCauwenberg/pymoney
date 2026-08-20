"""The Book: one tenant's ledger, in memory (decisions #5, #6, #7, #14).

A :class:`Book` owns the commodities, accounts, payees, transactions and prices
of a single ledger, enforces every invariant, assigns revisions and maintains the
change log.  It is the *only* writer: decision #14 says one writer per store, and
the same rule applies inside the process.

Storage here is plain dicts.  That is on purpose: this module is the reference
implementation of the rules, and the upcoming SQLAlchemy layer has to reproduce
its behaviour, test for test.  Nothing in this file knows that databases exist.

Commands are applied atomically.  Each mutation pushes an inverse onto an undo
log, so a handler that fails halfway leaves the book exactly as it was -- without
copying the whole store on every write.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from uuid import UUID

from . import commands as cmd
from .account import ACCOUNT_SEPARATOR, Account, AccountClass, AccountType
from .amount import Amount, Balances
from .changelog import Change, ChangeLog, ChangeOp, EntityKind
from .clock import Clock, SystemClock
from .commodity import Commodity, CommodityKind
from .entity import Entity
from .errors import (
    CommandError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from .ids import new_id
from .payee import Payee
from .posting import LegSpec, book_legs
from .price import Price, PriceDB, ValuationPolicy
from .rounding import to_fraction
from .settings import BookSettings
from .sync import RevisionBatch, SyncPage, build_batch
from .transaction import ReconcileState, Split, Transaction

__all__ = ["Book", "SplitRef"]


@dataclass(frozen=True, slots=True)
class SplitRef:
    """A split together with the transaction it belongs to.

    What an account register is made of.  ``quantity`` and ``value`` are bound
    to the book's *current* commodity snapshots, so display settings edited
    after the fact take effect; ``split`` is the stored row, untouched.
    """

    transaction: Transaction
    split: Split
    quantity: Amount
    value: Amount

    @property
    def date(self) -> date:
        return self.transaction.date

    @property
    def account_id(self) -> UUID:
        return self.split.account_id

    @property
    def memo(self) -> str:
        """The split's memo, falling back to the transaction's."""
        return self.split.memo or self.transaction.memo

    @property
    def reconcile_state(self) -> ReconcileState:
        return self.split.reconcile_state

    def __repr__(self) -> str:
        return f"SplitRef({self.date} {self.quantity})"


class Book:
    """An in-memory, single-tenant ledger with a command interface."""

    def __init__(
        self,
        *,
        id: UUID | None = None,
        name: str = "",
        base_currency: Commodity | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.id = id or new_id()
        self.name = name or "PyMoney Book"
        self.clock: Clock = clock or SystemClock()

        self._commodities: dict[UUID, Commodity] = {}
        self._accounts: dict[UUID, Account] = {}
        self._payees: dict[UUID, Payee] = {}
        self._transactions: dict[UUID, Transaction] = {}
        self.prices = PriceDB()

        self._children: dict[UUID | None, list[UUID]] = {}
        self._txns_by_account: dict[UUID, set[UUID]] = {}

        self.changelog = ChangeLog()
        self._revision = 0
        self._applied: dict[UUID, cmd.CommandResult] = {}

        self._undo: list[Callable[[], None]] | None = None
        self._pending: list[Change] = []
        self._current_revision = 0
        self._current_command: cmd.Command | None = None

        self._settings_revision = 0
        self._base_currency_id: UUID | None = None

        # Bootstrapping is itself a command, so a replica pulling from r0 is
        # told the book's name and base currency instead of having to guess.
        self.name = "PyMoney Book"
        self.execute(
            cmd.InitialiseBook(name=name or "PyMoney Book", base_currency=base_currency)
        )

    # ====================================================================
    # bootstrapping
    # ====================================================================
    @property
    def revision(self) -> int:
        """The book's current revision: a client's sync watermark."""
        return self._revision

    @property
    def base_currency(self) -> Commodity:
        """The book's reporting currency."""
        if self._base_currency_id is None:
            raise ValidationError("this book has no base currency")
        return self._commodities[self._base_currency_id]

    @property
    def settings(self) -> BookSettings:
        """The book's own fields, as a syncable snapshot."""
        return BookSettings(
            id=self.id,
            book_id=self.id,
            name=self.name,
            base_currency_id=self._base_currency_id,
            revision=self._settings_revision,
        )

    def set_base_currency(self, commodity_id: UUID) -> Commodity:
        """Convenience wrapper around :class:`~pymoney.core.commands.SetBaseCurrency`."""
        self.execute(cmd.SetBaseCurrency(commodity_id=commodity_id))
        return self.base_currency

    def default_policy(self, as_of: date | None = None, **kwargs: Any) -> ValuationPolicy:
        """A :class:`ValuationPolicy` targeting the base currency."""
        return ValuationPolicy(
            target=self.base_currency,
            as_of=as_of or self.clock.today(),
            **kwargs,
        )

    # ====================================================================
    # live commodity snapshots
    # ====================================================================
    def live(self, commodity: Commodity) -> Commodity:
        """The registry's current snapshot of ``commodity``.

        Entities embed a ``Commodity`` object rather than a bare id, which is
        convenient but means the copy can lag behind an edit to the commodity's
        display fields.  Identity and ``minor_unit_factor`` never change, so
        arithmetic is always safe; only presentation can go stale.  Every amount
        the book hands out is passed through here first.

        (Phase 2 note: the wire format must carry ``commodity_id``, not a nested
        copy, or replicas will inherit this staleness.)
        """
        current = self._commodities.get(commodity.id)
        return commodity if current is None else current

    def _live_amount(self, amount: Amount) -> Amount:
        current = self._commodities.get(amount.commodity.id)
        # Identity check first: commodities are replaced wholesale on update, so
        # the common case costs one dict lookup and no allocation.
        if current is None or current is amount.commodity:
            return amount
        return Amount(current, amount.minor)

    # ====================================================================
    # lookups
    # ====================================================================
    def commodity(self, commodity_id: UUID) -> Commodity:
        try:
            return self._commodities[commodity_id]
        except KeyError:
            raise NotFoundError(f"no commodity {commodity_id}") from None

    def commodity_by_code(self, code: str) -> Commodity:
        key = code.strip().upper()
        for commodity in self._commodities.values():
            if commodity.key == key:
                return commodity
        raise NotFoundError(f"no commodity with code {code!r}")

    def find_commodity(self, code: str) -> Commodity | None:
        try:
            return self.commodity_by_code(code)
        except NotFoundError:
            return None

    def commodities(self) -> tuple[Commodity, ...]:
        return tuple(sorted(self._commodities.values(), key=lambda c: c.code))

    def account(self, account_id: UUID) -> Account:
        try:
            return self._accounts[account_id]
        except KeyError:
            raise NotFoundError(f"no account {account_id}") from None

    def accounts(
        self,
        *,
        account_class: AccountClass | None = None,
        type: AccountType | None = None,
        include_closed: bool = True,
    ) -> tuple[Account, ...]:
        out = [
            a
            for a in self._accounts.values()
            if (account_class is None or a.account_class is account_class)
            and (type is None or a.type is type)
            and (include_closed or not a.closed)
        ]
        return tuple(sorted(out, key=self.path))

    def payee(self, payee_id: UUID) -> Payee:
        try:
            return self._payees[payee_id]
        except KeyError:
            raise NotFoundError(f"no payee {payee_id}") from None

    def payees(self) -> tuple[Payee, ...]:
        return tuple(sorted(self._payees.values(), key=lambda p: p.name))

    def find_payee(self, name: str) -> Payee | None:
        key = name.strip().casefold()
        for payee in self._payees.values():
            if payee.key == key or any(m.casefold() == key for m in payee.match_keys):
                return payee
        return None

    def transaction(self, transaction_id: UUID) -> Transaction:
        try:
            return self._transactions[transaction_id]
        except KeyError:
            raise NotFoundError(f"no transaction {transaction_id}") from None

    def transactions(
        self,
        *,
        account_id: UUID | None = None,
        start: date | None = None,
        end: date | None = None,
        payee_id: UUID | None = None,
    ) -> tuple[Transaction, ...]:
        """Transactions matching the filters, ordered by date then id."""
        if account_id is not None:
            self.account(account_id)  # existence check
            ids = self._txns_by_account.get(account_id, set())
            candidates: Iterable[Transaction] = (self._transactions[i] for i in ids)
        else:
            candidates = self._transactions.values()
        out = [
            t
            for t in candidates
            if (start is None or t.date >= start)
            and (end is None or t.date <= end)
            and (payee_id is None or self._mentions_payee(t, payee_id))
        ]
        return tuple(sorted(out, key=lambda t: (t.date, t.id.int)))

    @staticmethod
    def _mentions_payee(transaction: Transaction, payee_id: UUID) -> bool:
        return transaction.payee_id == payee_id or any(
            s.payee_id == payee_id for s in transaction.splits
        )

    def register(
        self,
        account_id: UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        include_children: bool = False,
    ) -> tuple[SplitRef, ...]:
        """Every split touching an account, in post-date order.

        This is the account register a UI shows, and the input to every balance
        computation.
        """
        account_ids = (
            {a.id for a in self.subtree(account_id)}
            if include_children
            else {self.account(account_id).id}
        )
        # Driven off the per-account index rather than a full table scan: an
        # account register must stay cheap in a book with 100k transactions.
        transaction_ids: set[UUID] = set()
        for one in account_ids:
            transaction_ids |= self._txns_by_account.get(one, set())

        out: list[SplitRef] = []
        for transaction_id in transaction_ids:
            transaction = self._transactions[transaction_id]
            if start is not None and transaction.date < start:
                continue
            if end is not None and transaction.date > end:
                continue
            for split in transaction.splits:
                if split.account_id in account_ids:
                    out.append(
                        SplitRef(
                            transaction=transaction,
                            split=split,
                            quantity=self._live_amount(split.quantity),
                            value=self._live_amount(split.value),
                        )
                    )
        out.sort(key=lambda r: (r.transaction.date, r.transaction.id.int, r.split.id.int))
        return tuple(out)

    # ====================================================================
    # tree navigation
    # ====================================================================
    def children(self, parent_id: UUID | None) -> tuple[Account, ...]:
        ids = self._children.get(parent_id, [])
        return tuple(sorted((self._accounts[i] for i in ids), key=lambda a: a.name))

    def roots(self) -> tuple[Account, ...]:
        return self.children(None)

    def parent(self, account: Account | UUID) -> Account | None:
        acc = account if isinstance(account, Account) else self.account(account)
        return None if acc.parent_id is None else self.account(acc.parent_id)

    def ancestors(self, account: Account | UUID) -> tuple[Account, ...]:
        """From the immediate parent up to the root."""
        acc = account if isinstance(account, Account) else self.account(account)
        out = []
        seen = {acc.id}
        while acc.parent_id is not None:
            if acc.parent_id in seen:  # pragma: no cover - cycles are prevented
                raise ValidationError(f"account cycle at {acc.id}")
            seen.add(acc.parent_id)
            acc = self.account(acc.parent_id)
            out.append(acc)
        return tuple(out)

    def subtree(self, account: Account | UUID) -> tuple[Account, ...]:
        """The account itself plus all descendants, depth-first by name."""
        acc = account if isinstance(account, Account) else self.account(account)
        out = [acc]
        for child in self.children(acc.id):
            out.extend(self.subtree(child))
        return tuple(out)

    def descendants(self, account: Account | UUID) -> tuple[Account, ...]:
        return self.subtree(account)[1:]

    def path(self, account: Account | UUID) -> str:
        """Colon-joined full name, derived from the tree (decision #13)."""
        acc = account if isinstance(account, Account) else self.account(account)
        names = [a.name for a in reversed(self.ancestors(acc))] + [acc.name]
        return ACCOUNT_SEPARATOR.join(names)

    def account_by_path(self, path: str) -> Account:
        """Look up an account by its colon-joined path."""
        parts = [p for p in path.split(ACCOUNT_SEPARATOR) if p]
        if not parts:
            raise NotFoundError("empty account path")
        parent_id: UUID | None = None
        current: Account | None = None
        for part in parts:
            key = part.strip().casefold()
            matches = [a for a in self.children(parent_id) if a.key == key]
            if not matches:
                raise NotFoundError(f"no account at path {path!r}")
            current = matches[0]
            parent_id = current.id
        assert current is not None
        return current

    def find_account(self, path: str) -> Account | None:
        try:
            return self.account_by_path(path)
        except NotFoundError:
            return None

    # ====================================================================
    # balances
    # ====================================================================
    def balance(self, account_id: UUID, *, as_of: date | None = None) -> Amount:
        """Booked balance of one account in its own commodity."""
        account = self.account(account_id)
        total = 0
        for ref in self.register(account_id, end=as_of):
            total += ref.split.quantity.minor
        return Amount(self.live(account.commodity), total)

    def balances_by_account(
        self, *, start: date | None = None, end: date | None = None
    ) -> dict[UUID, Amount]:
        """Every account's balance, in one pass over the transactions.

        Reports need all balances at once; calling :meth:`balance` per account
        would re-walk the ledger for each row.  Accounts with no movement in the
        window are present with a zero amount, so callers never need ``.get``.
        """
        totals: dict[UUID, int] = {}
        for transaction in self.transactions(start=start, end=end):
            for split in transaction.splits:
                totals[split.account_id] = (
                    totals.get(split.account_id, 0) + split.quantity.minor
                )
        return {
            account.id: Amount(self.live(account.commodity), totals.get(account.id, 0))
            for account in self._accounts.values()
        }

    def subtree_balances(
        self, account_id: UUID, *, as_of: date | None = None
    ) -> Balances:
        """Balance of an account and its descendants, per commodity."""
        out = Balances()
        for account in self.subtree(account_id):
            out.add(self.balance(account.id, as_of=as_of))
        return out

    def class_balances(
        self, account_class: AccountClass, *, as_of: date | None = None
    ) -> Balances:
        """Balance of every account in one class, per commodity."""
        out = Balances()
        for account in self.accounts(account_class=account_class):
            out.add(self.balance(account.id, as_of=as_of))
        return out

    def value_balance(
        self,
        account_id: UUID,
        *,
        as_of: date | None = None,
        include_children: bool = False,
    ) -> Amount:
        """Balance in the *transaction value* column rather than quantities.

        For a security account this is the acquisition cost in the transaction
        currency, not the current market value -- valuation needs a price and
        lives in :mod:`pymoney.core.reports`.
        """
        out = Balances()
        for ref in self.register(
            account_id, end=as_of, include_children=include_children
        ):
            out.add(ref.value)
        if len(out) == 0:
            return Amount.zero(self.base_currency)
        return out.sole()

    # ====================================================================
    # command dispatch
    # ====================================================================
    def execute(self, command: cmd.Command) -> cmd.CommandResult:
        """Validate and apply a command atomically, returning its result.

        One command produces exactly one revision, no matter how many rows it
        touches.  A ``MergePayees`` that rewrites 400 transactions is a single
        sync step.
        """
        if command.command_id is not None:
            cached = self._applied.get(command.command_id)
            if cached is not None:
                return replace(cached, replayed=True)

        handler = _HANDLERS.get(type(command))
        if handler is None:
            raise CommandError(f"no handler for command {type(command).__name__}")

        self._undo = []
        self._pending = []
        self._current_revision = self._revision + 1
        self._current_command = command
        try:
            entity = handler(self, command)
        except Exception:
            for undo in reversed(self._undo):
                undo()
            self._undo = None
            self._pending = []
            self._current_command = None
            raise

        changes = tuple(self._pending)
        self._undo = None
        self._pending = []
        self._current_command = None

        if not changes:
            # A no-op command (e.g. setting a value it already had) must not
            # burn a revision: clients poll by revision and an empty bump would
            # make every replica re-sync for nothing.
            return cmd.CommandResult(
                revision=self._revision, command=command.command_name, entity=entity
            )

        self._revision = self._current_revision
        self.changelog.extend(changes)
        result = cmd.CommandResult(
            revision=self._revision,
            command=command.command_name,
            changes=changes,
            entity=entity,
        )
        if command.command_id is not None:
            self._applied[command.command_id] = result
        return result

    def execute_all(
        self, commands: Iterable[cmd.Command]
    ) -> tuple[cmd.CommandResult, ...]:
        """Apply commands in order.  Not a transaction: each gets its revision."""
        return tuple(self.execute(c) for c in commands)

    # -- change recording --------------------------------------------------
    def _record(
        self,
        kind: EntityKind,
        entity_id: UUID,
        op: ChangeOp,
        *,
        note: str = "",
    ) -> None:
        command = self._current_command
        assert command is not None and self._undo is not None
        self._pending.append(
            Change(
                revision=self._current_revision,
                at=self.clock.now(),
                kind=kind,
                entity_id=entity_id,
                op=op,
                command=command.command_name,
                command_id=command.command_id,
                actor=command.actor,
                note=note,
            )
        )

    # -- storage primitives with undo -------------------------------------
    def _store_for(self, kind: EntityKind) -> dict[UUID, Any]:
        stores: dict[EntityKind, dict[UUID, Any]] = {
            EntityKind.COMMODITY: self._commodities,
            EntityKind.ACCOUNT: self._accounts,
            EntityKind.PAYEE: self._payees,
            EntityKind.TRANSACTION: self._transactions,
        }
        return stores[kind]

    def _put(
        self,
        kind: EntityKind,
        entity: Entity,
        *,
        created: bool = False,
        note: str = "",
    ) -> Any:
        """Insert or replace an entity, stamping it with the new revision."""
        assert self._undo is not None, "mutations must happen inside execute()"
        stamped = entity.stamped(
            at=self.clock.now(), revision=self._current_revision, created=created
        )
        if kind is EntityKind.PRICE:
            previous_price = self.prices.get(stamped.id)
            self.prices.add(stamped)  # type: ignore[arg-type]

            def undo_price() -> None:
                self.prices.remove(stamped.id)
                if previous_price is not None:
                    self.prices.add(previous_price)

            self._undo.append(undo_price)
        else:
            store = self._store_for(kind)
            previous = store.get(stamped.id)
            store[stamped.id] = stamped
            self._reindex(kind, previous, stamped)

            def undo_entity() -> None:
                if previous is None:
                    store.pop(stamped.id, None)
                    self._reindex(kind, stamped, None)
                else:
                    store[stamped.id] = previous
                    self._reindex(kind, stamped, previous)

            self._undo.append(undo_entity)

        self._record(
            kind,
            stamped.id,
            ChangeOp.CREATE if created else ChangeOp.UPDATE,
            note=note,
        )
        return stamped

    def _drop(self, kind: EntityKind, entity_id: UUID, *, note: str = "") -> Any:
        """Delete an entity and record a tombstone."""
        assert self._undo is not None, "mutations must happen inside execute()"
        if kind is EntityKind.PRICE:
            previous_price = self.prices.remove(entity_id)
            if previous_price is None:
                raise NotFoundError(f"no price {entity_id}")

            def undo_price() -> None:
                self.prices.add(previous_price)  # type: ignore[arg-type]

            self._undo.append(undo_price)
            removed: Any = previous_price
        else:
            store = self._store_for(kind)
            previous = store.pop(entity_id, None)
            if previous is None:
                raise NotFoundError(f"no {kind.value.lower()} {entity_id}")
            self._reindex(kind, previous, None)

            def undo_entity() -> None:
                store[entity_id] = previous
                self._reindex(kind, None, previous)

            self._undo.append(undo_entity)
            removed = previous

        self._record(kind, entity_id, ChangeOp.DELETE, note=note)
        return removed

    def _set_book_field(self, name: str, value: Any, *, note: str = "") -> BookSettings:
        """Change one of the book's own fields, with undo and a change record."""
        assert self._undo is not None, "mutations must happen inside execute()"
        previous = getattr(self, name)
        previous_revision = self._settings_revision
        setattr(self, name, value)
        self._settings_revision = self._current_revision

        def undo() -> None:
            setattr(self, name, previous)
            self._settings_revision = previous_revision

        self._undo.append(undo)
        self._record(EntityKind.BOOK, self.id, ChangeOp.UPDATE, note=note)
        return self.settings

    def _reindex(self, kind: EntityKind, old: Any | None, new: Any | None) -> None:
        """Keep the derived indexes in step with a store mutation."""
        # Indexes are maintained strictly in step with the stores, so a missing
        # entry is a bug: let it raise rather than papering over it.
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

    # ====================================================================
    # handlers: commodities
    # ====================================================================
    def _on_create_commodity(self, command: cmd.CreateCommodity) -> Commodity:
        key = command.code.strip().upper()
        if any(c.key == key for c in self._commodities.values()):
            raise ConflictError(f"commodity {command.code} already exists")
        commodity = Commodity(
            id=command.id or new_id(),
            book_id=self.id,
            kind=command.kind,
            code=command.code,
            name=command.name or command.code,
            symbol=command.symbol,
            minor_unit_factor=command.minor_unit_factor,
            display_precision=command.display_precision,
            isin=command.isin,
            quote_source=command.quote_source,
        )
        if commodity.id in self._commodities:
            raise ConflictError(f"commodity {commodity.id} already exists")
        return self._put(EntityKind.COMMODITY, commodity, created=True)

    def _on_update_commodity(self, command: cmd.UpdateCommodity) -> Commodity:
        current = self.commodity(command.commodity_id)
        updated = current
        if not isinstance(command.code, cmd.Unset):
            key = command.code.strip().upper()
            if key != current.key and any(
                c.key == key for c in self._commodities.values()
            ):
                raise ConflictError(f"commodity {command.code} already exists")
            updated = replace(updated, code=command.code)
        for field_name in ("name", "symbol", "display_precision", "isin", "quote_source"):
            value = getattr(command, field_name)
            if not isinstance(value, cmd.Unset):
                updated = replace(updated, **{field_name: value})
        if updated == current:
            return current
        return self._put(EntityKind.COMMODITY, updated)

    def _on_delete_commodity(self, command: cmd.DeleteCommodity) -> Commodity:
        commodity = self.commodity(command.commodity_id)
        if commodity.id == self._base_currency_id:
            raise ValidationError(
                f"{commodity.code} is the book's base currency and cannot be deleted"
            )
        users = [a for a in self._accounts.values() if a.commodity.id == commodity.id]
        if users:
            raise ConflictError(
                f"{commodity.code} is used by {len(users)} account(s), "
                f"starting with {self.path(users[0])!r}"
            )
        priced = [p for p in self.prices.all() if commodity.id in (p.base.id, p.quote.id)]
        if priced:
            raise ConflictError(
                f"{commodity.code} has {len(priced)} price(s); delete them first"
            )
        return self._drop(EntityKind.COMMODITY, commodity.id)

    # ====================================================================
    # handlers: book settings
    # ====================================================================
    def _on_initialise_book(self, command: cmd.InitialiseBook) -> BookSettings:
        if self._revision != 0:
            raise ConflictError(
                f"book {self.id} is already initialised (r{self._revision})"
            )
        self._set_book_field("name", command.name, note="book created")
        if command.base_currency is not None:
            commodity: Commodity = command.base_currency
            stored = self._put(
                EntityKind.COMMODITY,
                replace(commodity, book_id=self.id),
                created=True,
            )
            self._set_book_field(
                "_base_currency_id",
                stored.id,
                note=f"base currency {stored.code}",
            )
        return self.settings

    def _on_set_base_currency(self, command: cmd.SetBaseCurrency) -> BookSettings:
        currency = self.commodity(command.commodity_id)
        if not currency.is_currency:
            raise ValidationError(f"{currency.code} is a {currency.kind}, not a currency")
        if currency.id == self._base_currency_id:
            return self.settings
        previous = (
            self._commodities[self._base_currency_id].code
            if self._base_currency_id is not None
            else None
        )
        return self._set_book_field(
            "_base_currency_id",
            currency.id,
            note=f"base currency {previous} -> {currency.code}",
        )

    def _on_rename_book(self, command: cmd.RenameBook) -> BookSettings:
        if not command.name.strip():
            raise ValidationError("book name must not be empty")
        if command.name == self.name:
            return self.settings
        return self._set_book_field(
            "name", command.name, note=f"renamed from {self.name!r}"
        )

    # ====================================================================
    # handlers: accounts
    # ====================================================================
    def _check_sibling_name(
        self, parent_id: UUID | None, name: str, *, exclude: UUID | None = None
    ) -> None:
        key = name.strip().casefold()
        for sibling in self.children(parent_id):
            if sibling.id != exclude and sibling.key == key:
                where = "the top level" if parent_id is None else self.path(parent_id)
                raise ConflictError(f"{where} already has an account named {name!r}")

    def _on_create_account(self, command: cmd.CreateAccount) -> Account:
        commodity = (
            self.commodity(command.commodity_id)
            if command.commodity_id is not None
            else self.base_currency
        )
        parent = None
        if command.parent_id is not None:
            parent = self.account(command.parent_id)
            if parent.account_class is not command.type.account_class:
                raise ValidationError(
                    f"cannot put a {command.type} account under "
                    f"{self.path(parent)!r}, which is a {parent.account_class} "
                    f"account"
                )
        self._check_sibling_name(command.parent_id, command.name)
        account = Account(
            id=command.id or new_id(),
            book_id=self.id,
            type=command.type,
            name=command.name,
            commodity=commodity,
            parent_id=command.parent_id,
            description=command.description,
            code=command.code,
            placeholder=command.placeholder,
            notes=command.notes,
            institution=command.institution,
            iban=command.iban,
        )
        if account.id in self._accounts:
            raise ConflictError(f"account {account.id} already exists")
        return self._put(EntityKind.ACCOUNT, account, created=True)

    def _on_update_account(self, command: cmd.UpdateAccount) -> Account:
        current = self.account(command.account_id)
        updated = current
        for field_name in (
            "description",
            "code",
            "notes",
            "institution",
            "iban",
            "placeholder",
        ):
            value = getattr(command, field_name)
            if not isinstance(value, cmd.Unset):
                updated = replace(updated, **{field_name: value})
        if (
            updated.placeholder
            and not current.placeholder
            and self._txns_by_account.get(current.id)
        ):
            raise ValidationError(
                f"{self.path(current)!r} has postings and cannot become a placeholder"
            )
        if updated == current:
            return current
        return self._put(EntityKind.ACCOUNT, updated)

    def _on_rename_account(self, command: cmd.RenameAccount) -> Account:
        current = self.account(command.account_id)
        if current.name == command.name:
            return current
        self._check_sibling_name(current.parent_id, command.name, exclude=current.id)
        return self._put(
            EntityKind.ACCOUNT,
            replace(current, name=command.name),
            note=f"renamed from {current.name!r}",
        )

    def _on_set_account_commodity(self, command: cmd.SetAccountCommodity) -> Account:
        current = self.account(command.account_id)
        commodity = self.commodity(command.commodity_id)
        if commodity.id == current.commodity.id:
            return current
        posted = self._txns_by_account.get(current.id)
        if posted:
            raise ConflictError(
                f"{self.path(current)!r} already has {len(posted)} "
                f"transaction(s) denominated in {current.commodity.code}; "
                f"changing its commodity to {commodity.code} would silently "
                f"reinterpret them. Create a {commodity.code} account and "
                f"transfer the balance instead"
            )
        return self._put(
            EntityKind.ACCOUNT,
            replace(current, commodity=commodity),
            note=f"commodity {current.commodity.code} -> {commodity.code}",
        )

    def _on_move_account(self, command: cmd.MoveAccount) -> Account:
        current = self.account(command.account_id)
        if current.parent_id == command.new_parent_id:
            return current
        if command.new_parent_id is not None:
            new_parent = self.account(command.new_parent_id)
            if new_parent.id == current.id:
                raise ValidationError("an account cannot be its own parent")
            if any(a.id == new_parent.id for a in self.descendants(current)):
                raise ValidationError(
                    f"cannot move {self.path(current)!r} under its own "
                    f"descendant {self.path(new_parent)!r}"
                )
            if new_parent.account_class is not current.account_class:
                raise ValidationError(
                    f"cannot move a {current.account_class} account under a "
                    f"{new_parent.account_class} account"
                )
        self._check_sibling_name(command.new_parent_id, current.name, exclude=current.id)
        old_path = self.path(current)
        return self._put(
            EntityKind.ACCOUNT,
            replace(current, parent_id=command.new_parent_id),
            note=f"moved from {old_path!r}",
        )

    def _on_set_account_closed(self, command: cmd.SetAccountClosed) -> Account:
        current = self.account(command.account_id)
        if current.closed == command.closed:
            return current
        if command.closed:
            open_children = [c for c in self.descendants(current) if not c.closed]
            if open_children:
                raise ValidationError(
                    f"close the {len(open_children)} open sub-account(s) of "
                    f"{self.path(current)!r} first"
                )
        return self._put(EntityKind.ACCOUNT, replace(current, closed=command.closed))

    def _on_merge_accounts(self, command: cmd.MergeAccounts) -> Account:
        source = self.account(command.source_id)
        target = self.account(command.target_id)
        if source.id == target.id:
            raise ValidationError("cannot merge an account into itself")
        if source.commodity.id != target.commodity.id:
            raise ValidationError(
                f"cannot merge {source.commodity.code} account "
                f"{self.path(source)!r} into {target.commodity.code} account "
                f"{self.path(target)!r}"
            )
        if source.account_class is not target.account_class:
            raise ValidationError(
                f"cannot merge a {source.account_class} account into a "
                f"{target.account_class} account"
            )
        if any(a.id == target.id for a in self.descendants(source)):
            raise ValidationError(
                f"cannot merge {self.path(source)!r} into its own descendant"
            )

        source_path = self.path(source)
        note = f"merged from {source_path!r}"

        # Re-point the children first, so paths stay valid as we go.
        for child in self.children(source.id):
            self._check_sibling_name(target.id, child.name)
            self._put(
                EntityKind.ACCOUNT,
                replace(child, parent_id=target.id),
                note=note,
            )

        for transaction_id in sorted(
            self._txns_by_account.get(source.id, set()), key=lambda i: i.int
        ):
            transaction = self._transactions[transaction_id]
            splits = tuple(
                replace(s, account_id=target.id) if s.account_id == source.id else s
                for s in transaction.splits
            )
            self._put(
                EntityKind.TRANSACTION,
                transaction.replace_splits(splits),
                note=note,
            )

        self._drop(EntityKind.ACCOUNT, source.id, note=f"merged into {target.id}")
        return self.account(target.id)

    def _on_delete_account(self, command: cmd.DeleteAccount) -> Account:
        account = self.account(command.account_id)
        if self._children.get(account.id):
            raise ConflictError(
                f"{self.path(account)!r} has sub-accounts; move or delete them first"
            )
        if self._txns_by_account.get(account.id):
            count = len(self._txns_by_account[account.id])
            raise ConflictError(
                f"{self.path(account)!r} is used by {count} transaction(s); "
                f"merge it into another account instead"
            )
        return self._drop(EntityKind.ACCOUNT, account.id)

    # ====================================================================
    # handlers: payees
    # ====================================================================
    def _on_create_payee(self, command: cmd.CreatePayee) -> Payee:
        existing = self.find_payee(command.name)
        if existing is not None:
            raise ConflictError(
                f"payee {command.name!r} already exists as {existing.name!r}"
            )
        payee = Payee(
            id=command.id or new_id(),
            book_id=self.id,
            name=command.name,
            notes=command.notes,
            address=command.address,
            match_keys=command.match_keys,
            default_account_id=command.default_account_id,
        )
        if payee.default_account_id is not None:
            self.account(payee.default_account_id)
        if payee.id in self._payees:
            raise ConflictError(f"payee {payee.id} already exists")
        return self._put(EntityKind.PAYEE, payee, created=True)

    def _on_update_payee(self, command: cmd.UpdatePayee) -> Payee:
        current = self.payee(command.payee_id)
        updated = current
        if not isinstance(command.name, cmd.Unset):
            other = self.find_payee(command.name)
            if other is not None and other.id != current.id:
                raise ConflictError(f"payee {command.name!r} already exists")
            updated = replace(updated, name=command.name)
        for field_name in ("notes", "address", "match_keys", "default_account_id"):
            value = getattr(command, field_name)
            if not isinstance(value, cmd.Unset):
                updated = replace(updated, **{field_name: value})
        if updated.default_account_id is not None:
            self.account(updated.default_account_id)
        if updated == current:
            return current
        return self._put(EntityKind.PAYEE, updated)

    def _on_merge_payees(self, command: cmd.MergePayees) -> Payee:
        target = self.payee(command.target_id)
        sources = [self.payee(i) for i in command.source_ids]
        if any(s.id == target.id for s in sources):
            raise ValidationError("cannot merge a payee into itself")
        if not sources:
            raise ValidationError("no payees to merge")

        source_ids = {s.id for s in sources}
        note = f"merged from {', '.join(repr(s.name) for s in sources)}"

        if command.keep_match_keys:
            extra = tuple(s.name for s in sources) + tuple(
                k for s in sources for k in s.match_keys
            )
            merged = target.with_match_keys(extra)
            if merged != target:
                target = self._put(EntityKind.PAYEE, merged, note=note)

        for transaction in sorted(
            self._transactions.values(), key=lambda t: (t.date, t.id.int)
        ):
            touched = False
            new_payee_id = transaction.payee_id
            if new_payee_id in source_ids:
                new_payee_id, touched = target.id, True
            splits = []
            for split in transaction.splits:
                if split.payee_id in source_ids:
                    splits.append(replace(split, payee_id=target.id))
                    touched = True
                else:
                    splits.append(split)
            if touched:
                self._put(
                    EntityKind.TRANSACTION,
                    replace(transaction, payee_id=new_payee_id, splits=tuple(splits)),
                    note=note,
                )

        for source in sources:
            self._drop(EntityKind.PAYEE, source.id, note=f"merged into {target.id}")
        return self.payee(target.id)

    def _on_delete_payee(self, command: cmd.DeletePayee) -> Payee:
        payee = self.payee(command.payee_id)
        users = [
            t for t in self._transactions.values() if self._mentions_payee(t, payee.id)
        ]
        if users:
            raise ConflictError(
                f"payee {payee.name!r} is used by {len(users)} transaction(s); "
                f"merge it instead"
            )
        return self._drop(EntityKind.PAYEE, payee.id)

    # ====================================================================
    # handlers: transactions
    # ====================================================================
    def _account_for_posting(self, account_id: UUID) -> Account:
        account = self.account(account_id)
        if account.placeholder:
            raise ValidationError(
                f"{self.path(account)!r} is a placeholder and accepts no postings"
            )
        if account.closed:
            raise ValidationError(
                f"{self.path(account)!r} is closed and accepts no postings"
            )
        return account

    def _build_splits(
        self,
        legs: tuple[LegSpec, ...],
        currency: Commodity,
        rounding: Any,
        imbalance_account_id: UUID | None,
    ) -> tuple[Split, ...]:
        booked = book_legs(
            legs,
            currency=currency,
            resolve_account=self._account_for_posting,
            rounding=rounding,
            imbalance_account_id=imbalance_account_id,
        )
        return booked.splits

    def _on_post_transaction(self, command: cmd.PostTransaction) -> Transaction:
        currency = (
            self.commodity(command.currency_id)
            if command.currency_id is not None
            else self.base_currency
        )
        if command.payee_id is not None:
            self.payee(command.payee_id)
        if command.imported_id is not None:
            clash = [
                t
                for t in self._transactions.values()
                if t.imported_id == command.imported_id
            ]
            if clash:
                raise ConflictError(
                    f"a transaction with imported_id "
                    f"{command.imported_id!r} already exists"
                )
        splits = self._build_splits(
            tuple(command.legs),
            currency,
            command.rounding,
            command.imbalance_account_id,
        )
        for split in splits:
            if split.payee_id is not None:
                self.payee(split.payee_id)
        transaction = Transaction(
            id=command.id or new_id(),
            book_id=self.id,
            date=command.date,
            currency=currency,
            splits=splits,
            memo=command.memo,
            payee_id=command.payee_id,
            number=command.number,
            rounding=command.rounding,
            entered_at=command.entered_at or self.clock.now(),
            imported_id=command.imported_id,
        )
        transaction.validate()
        if transaction.id in self._transactions:
            raise ConflictError(f"transaction {transaction.id} already exists")
        return self._put(EntityKind.TRANSACTION, transaction, created=True)

    def _on_update_transaction(self, command: cmd.UpdateTransaction) -> Transaction:
        current = self.transaction(command.transaction_id)
        current.require_mutable("modify")

        currency = current.currency
        if not isinstance(command.currency_id, cmd.Unset):
            currency = self.commodity(command.currency_id)
        rounding = current.rounding
        if not isinstance(command.rounding, cmd.Unset):
            rounding = command.rounding

        updated = current
        if currency is not current.currency:
            updated = replace(updated, currency=currency)
        if rounding is not current.rounding:
            updated = replace(updated, rounding=rounding)
        for field_name in ("date", "memo", "number", "payee_id"):
            value = getattr(command, field_name)
            if not isinstance(value, cmd.Unset):
                updated = replace(updated, **{field_name: value})
        if updated.payee_id is not None:
            self.payee(updated.payee_id)

        if not isinstance(command.legs, cmd.Unset):
            splits = self._build_splits(
                tuple(command.legs),
                currency,
                rounding,
                command.imbalance_account_id,
            )
            for split in splits:
                if split.payee_id is not None:
                    self.payee(split.payee_id)
            updated = replace(updated, splits=splits)
        elif currency is not current.currency:
            raise ValidationError(
                "changing the transaction currency requires re-supplying the legs"
            )

        updated.validate()
        if updated == current:
            return current
        return self._put(EntityKind.TRANSACTION, updated)

    def _on_delete_transaction(self, command: cmd.DeleteTransaction) -> Transaction:
        transaction = self.transaction(command.transaction_id)
        transaction.require_mutable("delete")
        return self._drop(EntityKind.TRANSACTION, transaction.id)

    def _on_set_split_reconcile_state(
        self, command: cmd.SetSplitReconcileState
    ) -> Transaction:
        transaction = self.transaction(command.transaction_id)
        split = transaction.get_split(command.split_id)
        updated_split = split.with_reconcile_state(command.state, on=command.on)
        if updated_split == split:
            return transaction
        return self._put(
            EntityKind.TRANSACTION,
            transaction.with_split(updated_split),
            note=f"split {split.id} -> {command.state.name}",
        )

    def _on_reconcile_account(self, command: cmd.ReconcileAccount) -> Account:
        account = self.account(command.account_id)
        wanted = set(command.split_ids)
        found: set[UUID] = set()
        for transaction_id in sorted(
            self._txns_by_account.get(account.id, set()), key=lambda i: i.int
        ):
            transaction = self._transactions[transaction_id]
            splits = []
            touched = False
            for split in transaction.splits:
                if split.id in wanted and split.account_id == account.id:
                    found.add(split.id)
                    new_split = split.with_reconcile_state(
                        command.state, on=command.statement_date
                    )
                    splits.append(new_split)
                    touched = touched or new_split != split
                else:
                    splits.append(split)
            if touched:
                self._put(
                    EntityKind.TRANSACTION,
                    transaction.replace_splits(tuple(splits)),
                    note=f"reconciled {command.state.name}",
                )
        missing = wanted - found
        if missing:
            raise NotFoundError(
                f"{len(missing)} split(s) are not in {self.path(account)!r}: "
                f"{sorted(str(m) for m in missing)[0]}..."
            )
        return account

    # ====================================================================
    # handlers: prices
    # ====================================================================
    def _on_add_price(self, command: cmd.AddPrice) -> Price:
        base = self.commodity(command.base_id)
        quote = self.commodity(command.quote_id)
        price = Price.of(
            base,
            quote,
            to_fraction(command.rate),
            effective_date=command.effective_date,
            as_of=command.as_of or self.clock.now(),
            source=command.source,
            id=command.id or new_id(),
            book_id=self.id,
        )
        created = self.prices.get(price.id) is None
        return self._put(EntityKind.PRICE, price, created=created)

    def _on_delete_price(self, command: cmd.DeletePrice) -> Price:
        if self.prices.get(command.price_id) is None:
            raise NotFoundError(f"no price {command.price_id}")
        return self._drop(EntityKind.PRICE, command.price_id)

    # ====================================================================
    # convenience façade
    # ====================================================================
    def add_commodity(self, code: str, **kwargs: Any) -> Commodity:
        return self.execute(cmd.CreateCommodity(code=code, **kwargs)).entity

    def add_currency(self, code: str, *, scale: int = 2, **kwargs: Any) -> Commodity:
        return self.add_commodity(
            code, minor_unit_factor=10**scale, kind=CommodityKind.CURRENCY, **kwargs
        )

    def add_security(
        self,
        code: str,
        *,
        scale: int = 4,
        kind: CommodityKind = CommodityKind.STOCK,
        **kwargs: Any,
    ) -> Commodity:
        return self.add_commodity(code, minor_unit_factor=10**scale, kind=kind, **kwargs)

    def add_account(
        self,
        name: str,
        type: AccountType,
        *,
        parent: Account | UUID | None = None,
        commodity: Commodity | UUID | None = None,
        **kwargs: Any,
    ) -> Account:
        return self.execute(
            cmd.CreateAccount(
                name=name,
                type=type,
                parent_id=_id_of(parent),
                commodity_id=_id_of(commodity),
                **kwargs,
            )
        ).entity

    def add_payee(self, name: str, **kwargs: Any) -> Payee:
        return self.execute(cmd.CreatePayee(name=name, **kwargs)).entity

    def post(
        self,
        on: date,
        legs: Iterable[LegSpec],
        *,
        currency: Commodity | UUID | None = None,
        **kwargs: Any,
    ) -> Transaction:
        return self.execute(
            cmd.PostTransaction(
                date=on,
                legs=tuple(legs),
                currency_id=_id_of(currency),
                **kwargs,
            )
        ).entity

    def add_price(
        self,
        base: Commodity | UUID,
        quote: Commodity | UUID,
        rate: Any,
        *,
        effective_date: date,
        **kwargs: Any,
    ) -> Price:
        return self.execute(
            cmd.AddPrice(
                base_id=_require_id(base),
                quote_id=_require_id(quote),
                rate=rate,
                effective_date=effective_date,
                **kwargs,
            )
        ).entity

    # ====================================================================
    # sync surface
    # ====================================================================
    def changes_since(self, revision: int) -> tuple[Change, ...]:
        """Raw row-level changes above ``revision``.

        The low-level view.  Most clients want :meth:`pull`, which groups these
        into atomically applicable batches.
        """
        return self.changelog.since(revision)

    def revision_batch(self, revision: int) -> RevisionBatch:
        """Everything the command at ``revision`` changed, entities included.

        One revision is one command, so this is the unit a replica should apply
        atomically: a merge across 400 transactions arrives whole or not at all.

        :raises NotFoundError: if no command produced that revision.
        """
        changes = self.changelog.at(revision)
        if not changes:
            raise NotFoundError(f"no revision {revision} in this book")
        return build_batch(revision, changes, self.entity_for_change)

    def pull(
        self,
        since: int = 0,
        *,
        max_revisions: int | None = None,
        max_rows: int | None = None,
    ) -> SyncPage:
        """Fetch everything above ``since`` as whole revision batches.

        This is the shape the ``GET /changes`` endpoint should have.

        :param max_revisions: hard cap on the number of commands returned.
        :param max_rows: *soft* cap on rows.  A revision is never split, so the
            page stops after the first batch that reaches the cap -- a single
            command bigger than ``max_rows`` is still returned whole, because
            half of it is not a valid state.
        """
        if since < 0:
            raise ValidationError(f"sync watermark must not be negative: {since}")
        if since > self._revision:
            raise ValidationError(
                f"sync watermark r{since} is ahead of this book (r{self._revision}); "
                f"the client is talking to the wrong book or it was restored "
                f"from a backup"
            )

        by_revision: dict[int, list[Change]] = {}
        for change in self.changelog.since(since):
            by_revision.setdefault(change.revision, []).append(change)

        batches: list[RevisionBatch] = []
        watermark = since
        rows = 0
        for revision in sorted(by_revision):
            if max_revisions is not None and len(batches) >= max_revisions:
                break
            if max_rows is not None and rows >= max_rows:
                break
            batch = build_batch(revision, by_revision[revision], self.entity_for_change)
            batches.append(batch)
            watermark = revision
            rows += batch.rows

        return SyncPage(
            from_revision=since,
            to_revision=watermark,
            head=self._revision,
            batches=tuple(batches),
        )

    def snapshot_ids(self) -> dict[str, tuple[UUID, ...]]:
        """Ids currently present, for a replica to detect what it must drop."""
        return {
            "commodities": tuple(self._commodities),
            "accounts": tuple(self._accounts),
            "payees": tuple(self._payees),
            "transactions": tuple(self._transactions),
            "prices": tuple(p.id for p in self.prices.all()),
        }

    def entity_for_change(self, change: Change) -> Any | None:
        """Resolve a change to its current entity, or None if it was deleted."""
        if change.kind is EntityKind.PRICE:
            return self.prices.get(change.entity_id)
        if change.kind is EntityKind.BOOK:
            return self.settings
        return self._store_for(change.kind).get(change.entity_id)

    # ====================================================================
    # integrity
    # ====================================================================
    def check_integrity(self) -> tuple[str, ...]:
        """Re-verify every invariant; returns a tuple of problem descriptions.

        Cheap insurance for importers and for the eventual database backend:
        run it after a bulk load and you know the ledger is sound.
        """
        problems: list[str] = []
        for account in self._accounts.values():
            if account.parent_id is not None:
                parent = self._accounts.get(account.parent_id)
                if parent is None:
                    problems.append(f"account {account.id} has a missing parent")
                elif parent.account_class is not account.account_class:
                    problems.append(
                        f"account {self.path(account)!r} has a "
                        f"{parent.account_class} parent"
                    )
            if account.commodity.id not in self._commodities:
                problems.append(
                    f"account {self.path(account)!r} uses an unknown commodity"
                )
        for transaction in self._transactions.values():
            try:
                transaction.validate()
            except ValidationError as exc:
                problems.append(f"transaction {transaction.id}: {exc}")
            for split in transaction.splits:
                posted_to = self._accounts.get(split.account_id)
                if posted_to is None:
                    problems.append(
                        f"transaction {transaction.id} posts to a missing account"
                    )
                elif posted_to.commodity.id != split.commodity.id:
                    problems.append(
                        f"transaction {transaction.id} posts "
                        f"{split.commodity.code} to a "
                        f"{posted_to.commodity.code} account"
                    )
            if (
                transaction.payee_id is not None
                and transaction.payee_id not in self._payees
            ):
                problems.append(
                    f"transaction {transaction.id} references a missing payee"
                )
        return tuple(problems)

    def __repr__(self) -> str:
        return (
            f"Book({self.name!r}, r{self._revision}, "
            f"{len(self._accounts)} accounts, "
            f"{len(self._transactions)} transactions)"
        )


def _id_of(value: Any) -> UUID | None:
    """Accept an entity or a bare id wherever an optional id is expected."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return value.id


def _require_id(value: Any) -> UUID:
    """Same, where the id is mandatory."""
    resolved = _id_of(value)
    if resolved is None:
        raise ValidationError("an id or entity is required here, got None")
    return resolved


_HANDLERS: dict[type, Callable[[Book, Any], Any]] = {
    cmd.InitialiseBook: Book._on_initialise_book,
    cmd.CreateCommodity: Book._on_create_commodity,
    cmd.UpdateCommodity: Book._on_update_commodity,
    cmd.DeleteCommodity: Book._on_delete_commodity,
    cmd.SetBaseCurrency: Book._on_set_base_currency,
    cmd.RenameBook: Book._on_rename_book,
    cmd.CreateAccount: Book._on_create_account,
    cmd.UpdateAccount: Book._on_update_account,
    cmd.RenameAccount: Book._on_rename_account,
    cmd.SetAccountCommodity: Book._on_set_account_commodity,
    cmd.MoveAccount: Book._on_move_account,
    cmd.SetAccountClosed: Book._on_set_account_closed,
    cmd.MergeAccounts: Book._on_merge_accounts,
    cmd.DeleteAccount: Book._on_delete_account,
    cmd.CreatePayee: Book._on_create_payee,
    cmd.UpdatePayee: Book._on_update_payee,
    cmd.MergePayees: Book._on_merge_payees,
    cmd.DeletePayee: Book._on_delete_payee,
    cmd.PostTransaction: Book._on_post_transaction,
    cmd.UpdateTransaction: Book._on_update_transaction,
    cmd.DeleteTransaction: Book._on_delete_transaction,
    cmd.SetSplitReconcileState: Book._on_set_split_reconcile_state,
    cmd.ReconcileAccount: Book._on_reconcile_account,
    cmd.AddPrice: Book._on_add_price,
    cmd.DeletePrice: Book._on_delete_price,
}
