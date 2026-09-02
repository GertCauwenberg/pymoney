"""Transactions and splits (decisions #1, #4, #11).

A :class:`Transaction` is a container: one post date, one memo, one *transaction
currency*, and at least two :class:`Split` rows.  Each split carries two booked
amounts:

``quantity``
    What moved, in the **account's own commodity**.  10 shares of AAPL, or
    -1500.00 EUR.

``value``
    What it was worth, in the **transaction currency**.  This is the column that
    must sum to exactly zero across all splits -- that is double-entry.

For a single-currency transaction the two are identical and the invariant is the
familiar "debits equal credits".  For a EUR account paying a USD invoice, or for
a share purchase, they differ and the ``value`` column is what balances.

Reconciliation state makes splits progressively immutable (decision #11):
once a split is RECONCILED its transaction can no longer be edited or deleted
until someone explicitly walks the state back, and FROZEN cannot be walked back
at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import IntEnum
from fractions import Fraction
from uuid import UUID

from .amount import Amount
from .commodity import Commodity
from .entity import Entity
from .errors import ImmutableError, ValidationError
from .ids import new_id
from .rounding import DEFAULT_ROUNDING, RoundingMode

__all__ = ["ReconcileState", "Split", "Transaction"]


class ReconcileState(IntEnum):
    """How firmly a split has been matched against an external record.

    Ordered, so "at least cleared" is a comparison rather than a set lookup.
    """

    NOT_RECONCILED = 0
    CLEARED = 1
    """Seen on a statement, not yet part of a closed reconciliation."""
    RECONCILED = 2
    """Part of a completed reconciliation; edits require an explicit unlock."""
    FROZEN = 3
    """Closed accounting period.  Never editable again."""

    @property
    def is_locked(self) -> bool:
        return self >= ReconcileState.RECONCILED

    @property
    def is_final(self) -> bool:
        return self is ReconcileState.FROZEN


@dataclass(frozen=True, slots=True, kw_only=True)
class Split:
    """One leg of a transaction.

    Owned by its transaction: no revision of its own, because sync happens at
    transaction granularity.  A transaction is always transmitted whole.
    """

    account_id: UUID
    quantity: Amount
    """Booked movement in the account's commodity."""
    value: Amount
    """Booked worth in the transaction currency.  Sums to zero per transaction."""
    id: UUID = field(default_factory=new_id)
    memo: str = ""
    payee_id: UUID | None = None
    """Per-split payee; overrides the transaction's payee when set."""
    reconcile_state: ReconcileState = ReconcileState.NOT_RECONCILED
    reconcile_date: date | None = None
    action: str = ""
    """Investment action hint: BUY, SELL, DIV, REINVEST, SPLIT, ..."""
    number: str = ""
    """Cheque or reference number."""
    price: Fraction | None = None
    """The exact rate used to derive ``value`` from ``quantity``, if any.

    Stored because it is the user's intent and cannot always be recovered from
    the booked amounts: 3 shares booked at 10.00 could be 3.3333/share rounded.
    """

    def __post_init__(self) -> None:
        if self.price is not None and self.price <= 0:
            raise ValidationError(f"split price must be positive, got {self.price}")

    @property
    def commodity(self) -> Commodity:
        return self.quantity.commodity

    @property
    def is_locked(self) -> bool:
        return self.reconcile_state.is_locked

    @property
    def implied_price(self) -> Fraction | None:
        """``value / quantity``, or None when the quantity is zero."""
        q = self.quantity.as_fraction
        if q == 0:
            return None
        return self.value.as_fraction / q

    def with_reconcile_state(
        self, state: ReconcileState, *, on: date | None = None
    ) -> Split:
        """Return a copy in a new reconciliation state.

        :raises ImmutableError: when trying to leave the FROZEN state.
        """
        if self.reconcile_state.is_final and state is not ReconcileState.FROZEN:
            raise ImmutableError(f"split {self.id} is frozen and cannot be unreconciled")
        return replace(self, reconcile_state=state, reconcile_date=on)

    def __str__(self) -> str:
        return f"{self.quantity} (= {self.value})"


@dataclass(frozen=True, slots=True, kw_only=True)
class Transaction(Entity):
    """A balanced set of splits sharing one post date and currency."""

    date: date
    """Post date: a plain date, no timezone (decision #10)."""
    currency: Commodity
    """The transaction currency that ``Split.value`` is expressed in."""
    splits: tuple[Split, ...]
    memo: str = ""
    payee_id: UUID | None = None
    number: str = ""
    rounding: RoundingMode = DEFAULT_ROUNDING
    """Per-transaction rounding override (decision #4)."""
    entered_at: datetime | None = None
    """When the user entered it, as opposed to the post date."""
    imported_id: str | None = None
    """External id (FITID from OFX, bank reference) for dedup on import."""

    # -- invariants --------------------------------------------------------
    def validate(self) -> None:
        """Check every structural invariant.

        Called by the Book before a transaction is accepted; also useful
        standalone when constructing transactions in tests or importers.
        """
        if len(self.splits) < 2:
            raise ValidationError(
                f"a transaction needs at least 2 splits, got {len(self.splits)}"
            )

        seen_ids: set[UUID] = set()
        for split in self.splits:
            if split.id in seen_ids:
                raise ValidationError(f"duplicate split id {split.id}")
            seen_ids.add(split.id)
            if split.value.commodity.id != self.currency.id:
                raise ValidationError(
                    f"split value is in {split.value.commodity.code} but the "
                    f"transaction currency is {self.currency.code}"
                )
            if split.commodity.id == self.currency.id and split.quantity != split.value:
                raise ValidationError(
                    f"split in the transaction currency must have equal "
                    f"quantity and value, got {split.quantity} / {split.value}"
                )
            q_sign, v_sign = split.quantity.sign, split.value.sign
            if q_sign and v_sign and q_sign != v_sign:
                raise ValidationError(
                    f"split quantity {split.quantity} and value {split.value} "
                    f"have opposite signs"
                )

        total = sum(s.value.minor for s in self.splits)
        if total != 0:
            raise ValidationError(
                f"transaction values must sum to zero, off by "
                f"{Amount(self.currency, total)}"
            )

    @property
    def is_balanced(self) -> bool:
        return sum(s.value.minor for s in self.splits) == 0

    # -- reconciliation locking -------------------------------------------
    @property
    def lock_state(self) -> ReconcileState:
        """The strongest reconciliation state among the splits."""
        if not self.splits:
            return ReconcileState.NOT_RECONCILED
        return max(s.reconcile_state for s in self.splits)

    @property
    def is_locked(self) -> bool:
        return self.lock_state.is_locked

    def require_mutable(self, what: str = "modify") -> None:
        """:raises ImmutableError: if any split is reconciled or frozen."""
        state = self.lock_state
        if state.is_locked:
            raise ImmutableError(
                f"cannot {what} transaction {self.id}: it has {state.name} "
                f"splits; unreconcile them first"
            )

    # -- queries -----------------------------------------------------------
    @property
    def account_ids(self) -> tuple[UUID, ...]:
        return tuple(s.account_id for s in self.splits)

    def split_for(self, account_id: UUID) -> Split | None:
        for split in self.splits:
            if split.account_id == account_id:
                return split
        return None

    def splits_for(self, account_id: UUID) -> tuple[Split, ...]:
        return tuple(s for s in self.splits if s.account_id == account_id)

    def get_split(self, split_id: UUID) -> Split:
        for split in self.splits:
            if split.id == split_id:
                return split
        raise ValidationError(f"transaction {self.id} has no split {split_id}")

    @property
    def commodities(self) -> tuple[Commodity, ...]:
        out: dict[UUID, Commodity] = {}
        for split in self.splits:
            out.setdefault(split.commodity.id, split.commodity)
        return tuple(out.values())

    @property
    def is_multi_currency(self) -> bool:
        return len(self.commodities) > 1 or any(
            c.id != self.currency.id for c in self.commodities
        )

    def total_debits(self) -> Amount:
        """Sum of positive values; equals total credits in a valid transaction."""
        return Amount(
            self.currency,
            sum(s.value.minor for s in self.splits if s.value.minor > 0),
        )

    def replace_splits(self, splits: tuple[Split, ...]) -> Transaction:
        return replace(self, splits=tuple(splits))

    def with_split(self, split: Split) -> Transaction:
        """Replace one split by id, keeping order."""
        found = False
        out = []
        for existing in self.splits:
            if existing.id == split.id:
                out.append(split)
                found = True
            else:
                out.append(existing)
        if not found:
            raise ValidationError(f"transaction {self.id} has no split {split.id}")
        return replace(self, splits=tuple(out))

    def __str__(self) -> str:
        return (
            f"{self.date} {self.memo or '(no memo)'} "
            f"[{len(self.splits)} splits, {self.currency}]"
        )
