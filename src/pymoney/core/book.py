"""The Book: one tenant's ledger (decisions #5, #6, #7, #14).

What is left here after the split into :mod:`pymoney.core.store`,
:mod:`pymoney.core.handlers` and :mod:`pymoney.core.sync` is exactly what a
ledger aggregate should be:

* the **consistency boundary** -- one writer, one unit of work per command, one
  revision per command, no matter how many rows it touches;
* the **dispatcher** -- validate the intent, run the handler, stamp the result;
* the **read model** -- lookups, the account tree, balances.

It owns no storage. Everything goes through a :class:`~pymoney.core.store.Store`,
so the same rules and the same tests run over memory or over SQL.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, cast
from uuid import UUID

from . import commands as cmd
from . import sync as sync_protocol
from .account import ACCOUNT_SEPARATOR, Account, AccountClass, AccountType
from .amount import Amount, Balances
from .changelog import Change, ChangeOp, EntityKind
from .clock import Clock, SystemClock
from .commodity import Commodity, CommodityKind
from .entity import Entity
from .errors import CommandError, NotFoundError, ValidationError
from .handlers import handler_for
from .ids import new_id
from .payee import Payee
from .posting import LegSpec
from .price import Price, PriceDB, ValuationPolicy
from .settings import BookSettings
from .store import ChangeLogView, MemoryStore, Store
from .sync import RevisionBatch, SyncPage
from .transaction import ReconcileState, Split, Transaction

__all__ = ["Book", "SplitRef"]


@dataclass(frozen=True, slots=True)
class SplitRef:
    """A split together with the transaction it belongs to.

    What an account register is made of. ``quantity`` and ``value`` are bound to
    the book's *current* commodity snapshots, so display settings edited after
    the fact take effect; ``split`` is the stored row, untouched.
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
    """A single-tenant ledger with a command interface."""

    def __init__(
        self,
        *,
        id: UUID | None = None,
        name: str = "",
        base_currency: Commodity | None = None,
        clock: Clock | None = None,
        store: Store | None = None,
    ) -> None:
        """Open a book.

        :param store: where to keep it; defaults to a fresh
            :class:`~pymoney.core.store.MemoryStore`. A store that already holds
            a book is reopened rather than initialised, so ``name`` and
            ``base_currency`` are ignored in that case.
        """
        self.clock: Clock = clock or SystemClock()
        self._store = store if store is not None else MemoryStore(id or new_id())

        row = self._store.load_book()
        self.id = row.id
        self._name = row.name
        self._base_currency_id = row.base_currency_id
        self._revision = row.revision
        self._settings_revision = row.settings_revision

        self._applied: dict[UUID, cmd.CommandResult] = {}
        self._pending: list[Change] = []
        self._current_revision = 0
        self._current_command: cmd.Command | None = None
        self._in_unit_of_work = False

        if self._revision == 0:
            # Bootstrapping is itself a command, so a replica pulling from r0 is
            # told the book's name and base currency instead of having to guess.
            self.execute(
                cmd.InitialiseBook(
                    name=name or "PyMoney Book", base_currency=base_currency
                )
            )

    # ====================================================================
    # identity and settings
    # ====================================================================
    @property
    def store(self) -> Store:
        """The backing store. Handlers use it for pushed-down queries."""
        return self._store

    @property
    def name(self) -> str:
        return self._name

    @property
    def revision(self) -> int:
        """The book's current revision: a client's sync watermark."""
        return self._revision

    @property
    def base_currency_id(self) -> UUID | None:
        return self._base_currency_id

    @property
    def base_currency(self) -> Commodity:
        """The book's reporting currency."""
        if self._base_currency_id is None:
            raise ValidationError("this book has no base currency")
        return self.commodity(self._base_currency_id)

    @property
    def settings(self) -> BookSettings:
        """The book's own fields, as a syncable snapshot."""
        return BookSettings(
            id=self.id,
            book_id=self.id,
            name=self._name,
            base_currency_id=self._base_currency_id,
            revision=self._settings_revision,
        )

    @property
    def changelog(self) -> ChangeLogView:
        return ChangeLogView(self._store)

    @property
    def prices(self) -> PriceDB:
        return self._store.price_db

    def set_base_currency(self, commodity_id: UUID) -> Commodity:
        """Convenience wrapper around :class:`~pymoney.core.commands.SetBaseCurrency`."""
        self.execute(cmd.SetBaseCurrency(commodity_id=commodity_id))
        return self.base_currency

    def default_policy(self, as_of: date | None = None, **kwargs: Any) -> ValuationPolicy:
        """A :class:`ValuationPolicy` targeting the base currency."""
        return ValuationPolicy(
            target=self.base_currency, as_of=as_of or self.clock.today(), **kwargs
        )

    def close(self) -> None:
        self._store.close()

    # ====================================================================
    # live commodity snapshots
    # ====================================================================
    def live(self, commodity: Commodity) -> Commodity:
        """The store's current snapshot of ``commodity``.

        Entities embed a ``Commodity`` object rather than a bare id, which is
        convenient but means the copy can lag behind an edit to the commodity's
        display fields. Identity and ``minor_unit_factor`` never change, so
        arithmetic is always safe; only presentation can go stale. Every amount
        the book hands out is passed through here first.
        """
        current = self._store.get(EntityKind.COMMODITY, commodity.id)
        return commodity if current is None else cast(Commodity, current)

    def _live_amount(self, amount: Amount) -> Amount:
        current = self._store.get(EntityKind.COMMODITY, amount.commodity.id)
        # Identity check first: commodities are replaced wholesale on update, so
        # the common case costs one lookup and no allocation.
        if current is None or current is amount.commodity:
            return amount
        return Amount(cast(Commodity, current), amount.minor)

    # ====================================================================
    # lookups
    # ====================================================================
    def commodity(self, commodity_id: UUID) -> Commodity:
        found = self._store.get(EntityKind.COMMODITY, commodity_id)
        if found is None:
            raise NotFoundError(f"no commodity {commodity_id}")
        return cast(Commodity, found)

    def commodity_by_code(self, code: str) -> Commodity:
        key = code.strip().upper()
        for commodity in self._store.commodities():
            if commodity.key == key:
                return commodity
        raise NotFoundError(f"no commodity with code {code!r}")

    def find_commodity(self, code: str) -> Commodity | None:
        try:
            return self.commodity_by_code(code)
        except NotFoundError:
            return None

    def commodities(self) -> tuple[Commodity, ...]:
        return tuple(sorted(self._store.commodities(), key=lambda c: c.code))

    def account(self, account_id: UUID) -> Account:
        found = self._store.get(EntityKind.ACCOUNT, account_id)
        if found is None:
            raise NotFoundError(f"no account {account_id}")
        return cast(Account, found)

    def accounts(
        self,
        *,
        account_class: AccountClass | None = None,
        type: AccountType | None = None,
        include_closed: bool = True,
    ) -> tuple[Account, ...]:
        out = [
            a
            for a in self._store.accounts()
            if (account_class is None or a.account_class is account_class)
            and (type is None or a.type is type)
            and (include_closed or not a.closed)
        ]
        return tuple(sorted(out, key=self.path))

    def payee(self, payee_id: UUID) -> Payee:
        found = self._store.get(EntityKind.PAYEE, payee_id)
        if found is None:
            raise NotFoundError(f"no payee {payee_id}")
        return cast(Payee, found)

    def payees(self) -> tuple[Payee, ...]:
        return tuple(sorted(self._store.payees(), key=lambda p: p.name))

    def find_payee(self, name: str) -> Payee | None:
        key = name.strip().casefold()
        for payee in self._store.payees():
            if payee.key == key or any(m.casefold() == key for m in payee.match_keys):
                return payee
        return None

    def transaction(self, transaction_id: UUID) -> Transaction:
        found = self._store.get(EntityKind.TRANSACTION, transaction_id)
        if found is None:
            raise NotFoundError(f"no transaction {transaction_id}")
        return cast(Transaction, found)

    def transactions(
        self,
        *,
        account_id: UUID | None = None,
        start: date | None = None,
        end: date | None = None,
        payee_id: UUID | None = None,
    ) -> tuple[Transaction, ...]:
        """Transactions matching the filters, ordered by date then id."""
        if payee_id is not None:
            self.payee(payee_id)
            out = self._store.transactions_for_payee(payee_id)
            return tuple(
                t
                for t in out
                if (start is None or t.date >= start)
                and (end is None or t.date <= end)
                and (account_id is None or account_id in t.account_ids)
            )
        if account_id is not None:
            self.account(account_id)  # existence check
            return self._store.transactions(
                account_ids=[account_id], start=start, end=end
            )
        return self._store.transactions(start=start, end=end)

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
        out = [
            SplitRef(
                transaction=transaction,
                split=split,
                quantity=self._live_amount(split.quantity),
                value=self._live_amount(split.value),
            )
            for transaction in self._store.transactions(
                account_ids=account_ids, start=start, end=end
            )
            for split in transaction.splits
            if split.account_id in account_ids
        ]
        out.sort(
            key=lambda r: (r.transaction.date, r.transaction.id.int, r.split.id.int)
        )
        return tuple(out)

    # ====================================================================
    # tree navigation
    # ====================================================================
    def children(self, parent_id: UUID | None) -> tuple[Account, ...]:
        accounts = (self.account(i) for i in self._store.children_ids(parent_id))
        return tuple(sorted(accounts, key=lambda a: a.name))

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
        totals = self._store.balances_by_account(end=as_of)
        return Amount(self.live(account.commodity), totals.get(account_id, 0))

    def balances_by_account(
        self, *, start: date | None = None, end: date | None = None
    ) -> dict[UUID, Amount]:
        """Every account's balance, in one pushed-down aggregate query.

        Reports need all balances at once; calling :meth:`balance` per account
        would re-walk the ledger for each row. Accounts with no movement in the
        window are present with a zero amount, so callers never need ``.get``.
        """
        totals = self._store.balances_by_account(start=start, end=end)
        return {
            account.id: Amount(self.live(account.commodity), totals.get(account.id, 0))
            for account in self._store.accounts()
        }

    def subtree_balances(
        self, account_id: UUID, *, as_of: date | None = None
    ) -> Balances:
        """Balance of an account and its descendants, per commodity."""
        totals = self._store.balances_by_account(end=as_of)
        out = Balances()
        for account in self.subtree(account_id):
            out.add(Amount(self.live(account.commodity), totals.get(account.id, 0)))
        return out

    def class_balances(
        self, account_class: AccountClass, *, as_of: date | None = None
    ) -> Balances:
        """Balance of every account in one class, per commodity."""
        totals = self._store.balances_by_account(end=as_of)
        out = Balances()
        for account in self.accounts(account_class=account_class):
            out.add(Amount(self.live(account.commodity), totals.get(account.id, 0)))
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
        touches. A ``MergePayees`` that rewrites 400 transactions is a single
        sync step.
        """
        if command.command_id is not None:
            cached = self._applied.get(command.command_id)
            if cached is not None:
                return replace(cached, replayed=True)

        handler = handler_for(command)
        if handler is None:
            raise CommandError(f"no handler for command {type(command).__name__}")

        self._store.begin()
        self._in_unit_of_work = True
        self._pending = []
        self._current_revision = self._revision + 1
        self._current_command = command
        snapshot = (self._name, self._base_currency_id, self._settings_revision)
        try:
            entity = handler(self, command)
            changes = tuple(self._pending)
            if changes:
                self._store.append_changes(changes)
                self._store.save_book(
                    replace(
                        self._store.load_book(),
                        name=self._name,
                        base_currency_id=self._base_currency_id,
                        revision=self._current_revision,
                        settings_revision=self._settings_revision,
                    )
                )
            self._store.commit()
        except Exception:
            self._store.rollback()
            self._name, self._base_currency_id, self._settings_revision = snapshot
            raise
        finally:
            self._in_unit_of_work = False
            self._pending = []
            self._current_command = None

        if not changes:
            # A no-op command (setting a value it already had) must not burn a
            # revision: clients poll by revision, and an empty bump would make
            # every replica re-sync for nothing.
            return cmd.CommandResult(
                revision=self._revision,
                command=command.command_name,
                entity=entity,
            )

        self._revision = self._current_revision
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
        """Apply commands in order. Not a transaction: each gets its revision."""
        return tuple(self.execute(c) for c in commands)

    # -- the mutation surface handlers use --------------------------------
    def put[E: Entity](
        self,
        kind: EntityKind,
        entity: E,
        *,
        created: bool = False,
        note: str = "",
    ) -> E:
        """Insert or replace an entity, stamping it with the new revision."""
        self._require_unit_of_work()
        # cast: Entity.stamped returns Self, which mypy resolves to the
        # TypeVar's bound rather than to E.
        stamped = cast(
            E,
            entity.stamped(
                at=self.clock.now(),
                revision=self._current_revision,
                created=created,
            ),
        )
        self._store.put(kind, stamped)
        self._record(
            kind,
            stamped.id,
            ChangeOp.CREATE if created else ChangeOp.UPDATE,
            note=note,
        )
        return stamped

    def drop(self, kind: EntityKind, entity_id: UUID, *, note: str = "") -> Any:
        """Delete an entity and record a tombstone."""
        self._require_unit_of_work()
        previous = self._store.get(kind, entity_id)
        if previous is None or not self._store.delete(kind, entity_id):
            raise NotFoundError(f"no {kind.value.lower()} {entity_id}")
        self._record(kind, entity_id, ChangeOp.DELETE, note=note)
        return previous

    def set_book_field(self, name: str, value: Any, *, note: str = "") -> BookSettings:
        """Change one of the book's own fields, with a change record."""
        self._require_unit_of_work()
        setattr(self, f"_{name.lstrip('_')}", value)
        self._settings_revision = self._current_revision
        self._record(EntityKind.BOOK, self.id, ChangeOp.UPDATE, note=note)
        return self.settings

    def _require_unit_of_work(self) -> None:
        if not self._in_unit_of_work:
            raise AssertionError("mutations must happen inside execute()")

    def _record(
        self, kind: EntityKind, entity_id: UUID, op: ChangeOp, *, note: str = ""
    ) -> None:
        command = self._current_command
        assert command is not None
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
                date=on, legs=tuple(legs), currency_id=_id_of(currency), **kwargs
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

        The low-level view. Most clients want :meth:`pull`, which groups these
        into atomically applicable batches.
        """
        return self._store.changes_since(revision)

    def revision_batch(self, revision: int) -> RevisionBatch:
        """Everything the command at ``revision`` changed, entities included."""
        return sync_protocol.revision_batch(self, revision)

    def pull(
        self,
        since: int = 0,
        *,
        max_revisions: int | None = None,
        max_rows: int | None = None,
    ) -> SyncPage:
        """Fetch everything above ``since`` as whole revision batches."""
        return sync_protocol.pull(
            self, since, max_revisions=max_revisions, max_rows=max_rows
        )

    def snapshot_ids(self) -> dict[str, tuple[UUID, ...]]:
        """Ids currently present, for a replica to detect what it must drop."""
        return {
            "commodities": tuple(c.id for c in self._store.commodities()),
            "accounts": tuple(a.id for a in self._store.accounts()),
            "payees": tuple(p.id for p in self._store.payees()),
            "transactions": self._store.transaction_ids(),
            "prices": tuple(p.id for p in self.prices.all()),
        }

    def entity_for_change(self, change: Change) -> Any | None:
        """Resolve a change to its current entity, or None if it was deleted."""
        if change.kind is EntityKind.BOOK:
            return self.settings
        return self._store.get(change.kind, change.entity_id)

    # ====================================================================
    # integrity
    # ====================================================================
    def check_integrity(self) -> tuple[str, ...]:
        """Re-verify every invariant; returns a tuple of problem descriptions.

        Cheap insurance for importers and for the database backend: run it after
        a bulk load and you know the ledger is sound.
        """
        problems: list[str] = []
        commodity_ids = {c.id for c in self._store.commodities()}
        payee_ids = {p.id for p in self._store.payees()}
        accounts = {a.id: a for a in self._store.accounts()}

        for account in accounts.values():
            if account.parent_id is not None:
                parent = accounts.get(account.parent_id)
                if parent is None:
                    problems.append(f"account {account.id} has a missing parent")
                elif parent.account_class is not account.account_class:
                    problems.append(
                        f"account {self.path(account)!r} has a "
                        f"{parent.account_class} parent"
                    )
            if account.commodity.id not in commodity_ids:
                problems.append(
                    f"account {self.path(account)!r} uses an unknown commodity"
                )

        for transaction in self._store.transactions():
            try:
                transaction.validate()
            except ValidationError as exc:
                problems.append(f"transaction {transaction.id}: {exc}")
            for split in transaction.splits:
                posted_to = accounts.get(split.account_id)
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
            if transaction.payee_id is not None and transaction.payee_id not in payee_ids:
                problems.append(
                    f"transaction {transaction.id} references a missing payee"
                )
        return tuple(problems)

    def __repr__(self) -> str:
        return (
            f"Book({self._name!r}, r{self._revision}, "
            f"{len(self._store.accounts())} accounts, "
            f"{self._store.transaction_count()} transactions)"
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
