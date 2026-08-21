"""Turning user intent into booked splits (decisions #1, #3, #4).

This is the hardest correctness problem in the domain, so it lives in one small
module with one entry point, :func:`book_legs`.

A :class:`LegSpec` is what a client *says*: "put 10/3 EUR against Expenses:Fuel",
"buy 3 shares at 127.5501 USD each", "whatever is left over goes to the current
account".  Amounts in a leg are exact rationals, not yet booked.

:func:`book_legs` resolves them into :class:`~pymoney.core.transaction.Split`
objects with integer minor units, guaranteeing:

1. the ``value`` column sums to **exactly** zero;
2. every booked value is ``floor`` or ``ceil`` of its exact value, so no leg is
   ever distorted by more than one minor unit;
3. a leg whose exact value is already representable is **never** adjusted -- the
   rounding error is always pushed onto a leg that was inexact anyway;
4. splits denominated in the transaction currency keep ``quantity == value``.

Property 3 is the one that matters in practice: if you pay an invoice of
exactly 100.00 EUR out of a USD account at a rate of 10/3, the 100.00 stays
100.00 and the cent lands on the USD side.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any
from uuid import UUID

from .account import Account
from .amount import Amount
from .commodity import Commodity
from .errors import UnbalancedTransactionError, ValidationError
from .ids import new_id
from .rounding import (
    DEFAULT_ROUNDING,
    Numeric,
    RoundingMode,
    allocate_to_zero_sum,
    round_fraction,
    to_fraction,
)
from .transaction import ReconcileState, Split

__all__ = ["BookedLegs", "LegSpec", "book_legs"]

AccountResolver = Callable[[UUID], Account]


def _maybe_fraction(value: Numeric | None) -> Fraction | None:
    return None if value is None else to_fraction(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class LegSpec:
    """One leg of a transaction as the user expressed it, before booking.

    Supply any two of ``value``, ``quantity`` and ``price``; the third is
    derived.  For an account held in the transaction currency, ``value`` and
    ``quantity`` are the same thing and one is enough.  Exactly one leg in a
    transaction may leave all three unset, making it the *residual* leg that
    absorbs whatever is needed to balance.

    Supplying all three is allowed but *checked*: ``value`` and
    ``quantity * price`` must agree to within one minor unit on each side.  The
    booked columns win and ``price`` is retained as the stated intent, which is
    what lets a split record "127.5501 per share" while booking 382.65.
    """

    account_id: UUID
    value: Fraction | None = None
    """Worth in the transaction currency, major units, exact."""
    quantity: Fraction | None = None
    """Movement in the account's commodity, major units, exact."""
    price: Fraction | None = None
    """Value per unit of quantity, exact."""
    memo: str = ""
    payee_id: UUID | None = None
    action: str = ""
    number: str = ""
    reconcile_state: ReconcileState = ReconcileState.NOT_RECONCILED
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _maybe_fraction(self.value))
        object.__setattr__(self, "quantity", _maybe_fraction(self.quantity))
        object.__setattr__(self, "price", _maybe_fraction(self.price))
        if self.price is not None and self.price <= 0:
            raise ValidationError(f"leg price must be positive, got {self.price}")

    @property
    def is_residual(self) -> bool:
        return self.value is None and self.quantity is None

    @classmethod
    def of(
        cls,
        account_id: UUID,
        value: Numeric | None = None,
        *,
        quantity: Numeric | None = None,
        price: Numeric | None = None,
        **kwargs: Any,
    ) -> LegSpec:
        """Convenience constructor accepting ``str``/``Decimal``/``int``."""
        return cls(
            account_id=account_id,
            value=_maybe_fraction(value),
            quantity=_maybe_fraction(quantity),
            price=_maybe_fraction(price),
            **kwargs,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class BookedLegs:
    """Result of booking: the splits plus what rounding had to do."""

    splits: tuple[Split, ...]
    exact_values: tuple[Fraction, ...]
    """The pre-rounding value of each split, in transaction-currency major units."""
    rounding_adjustments: tuple[Fraction, ...]
    """Per-split difference, in minor units, between booked and exact value.

    Exact by construction, and each element is strictly within (-1, 1)."""

    @property
    def total_adjustment(self) -> Fraction:
        """Total absolute distortion, in minor units of the transaction currency."""
        return sum((abs(a) for a in self.rounding_adjustments), Fraction(0))

    @property
    def was_rounded(self) -> bool:
        return self.total_adjustment != 0


def book_legs(
    legs: Sequence[LegSpec],
    *,
    currency: Commodity,
    resolve_account: AccountResolver,
    rounding: RoundingMode = DEFAULT_ROUNDING,
    imbalance_account_id: UUID | None = None,
) -> BookedLegs:
    """Resolve and book ``legs`` into balanced splits.

    :param currency: the transaction currency that values are expressed in.
    :param resolve_account: maps an account id to its :class:`Account`, so the
        account's commodity is known.
    :param rounding: mode for booking *quantities*.  Values are booked by the
        zero-sum allocator, which only ever floors or ceils.
    :param imbalance_account_id: if given, a leg is appended to soak up any
        residual instead of raising.
    :raises UnbalancedTransactionError: if the exact values do not sum to zero
        and there is neither a residual leg nor an imbalance account.
    """
    if len(legs) < 1:
        raise ValidationError("a transaction needs at least one leg")

    residual_indexes = [i for i, leg in enumerate(legs) if leg.is_residual]
    if len(residual_indexes) > 1:
        raise ValidationError(
            f"at most one leg may be left to balance, got {len(residual_indexes)}"
        )

    accounts = [resolve_account(leg.account_id) for leg in legs]
    resolved: list[_Resolved] = [
        _resolve(leg, account, currency)
        for leg, account in zip(legs, accounts, strict=True)
    ]

    # -- balance the exact values -----------------------------------------
    known_total = sum(
        (r.value for i, r in enumerate(resolved) if i not in residual_indexes),
        Fraction(0),
    )
    if residual_indexes:
        index = residual_indexes[0]
        resolved[index] = _complete_residual(
            resolved[index], -known_total, legs[index], accounts[index], currency
        )
    elif known_total != 0:
        if imbalance_account_id is None:
            raise UnbalancedTransactionError(
                f"legs do not balance: off by {known_total} {currency.code}; "
                f"add a residual leg or an imbalance account"
            )
        imbalance_account = resolve_account(imbalance_account_id)
        imbalance_leg = LegSpec(
            account_id=imbalance_account_id,
            value=-known_total,
            memo="Imbalance",
        )
        legs = [*list(legs), imbalance_leg]
        accounts = [*accounts, imbalance_account]
        resolved.append(_resolve(imbalance_leg, imbalance_account, currency))

    exact_values = [r.value for r in resolved]
    total = sum(exact_values, Fraction(0))
    if total != 0:  # pragma: no cover - defensive; the branches above ensure it
        raise UnbalancedTransactionError(
            f"internal error: values sum to {total}, not zero"
        )

    # -- book the value column so it still sums to zero --------------------
    factor = currency.minor_unit_factor
    exact_minor = [v * factor for v in exact_values]
    booked_minor = allocate_to_zero_sum(exact_minor)
    adjustments = [
        booked - exact for booked, exact in zip(booked_minor, exact_minor, strict=True)
    ]

    # -- book the quantity column -----------------------------------------
    splits: list[Split] = []
    for leg, account, r, value_minor in zip(
        legs, accounts, resolved, booked_minor, strict=True
    ):
        commodity = account.commodity
        if commodity.id == currency.id:
            # Must stay identical to the value, including any adjustment.
            quantity = Amount(commodity, value_minor)
        else:
            quantity = Amount(
                commodity,
                round_fraction(r.quantity * commodity.minor_unit_factor, rounding),
            )
        splits.append(
            Split(
                id=leg.id,
                account_id=leg.account_id,
                quantity=quantity,
                value=Amount(currency, value_minor),
                memo=leg.memo,
                payee_id=leg.payee_id,
                action=leg.action,
                number=leg.number,
                reconcile_state=leg.reconcile_state,
                price=r.price,
            )
        )

    return BookedLegs(
        splits=tuple(splits),
        exact_values=tuple(exact_values),
        rounding_adjustments=tuple(adjustments),
    )


@dataclass(frozen=True, slots=True)
class _Resolved:
    value: Fraction
    quantity: Fraction
    price: Fraction | None


def _check_price_agrees(
    value: Fraction,
    quantity: Fraction,
    price: Fraction,
    account: Account,
    currency: Commodity,
) -> None:
    """Reject an over-specified leg whose three numbers contradict each other.

    When a client supplies ``value``, ``quantity`` *and* ``price``, the booked
    columns (value and quantity) win and the price is kept as the user's stated
    intent.  But ``value`` and ``quantity * price`` must still agree, or the
    split is simply wrong -- ``value=10, quantity=2, price=3`` is not a
    transaction, it is a typo.

    Exact equality is too strict: a UI or an OFX file routinely sends figures
    that have already been rounded for display, so 3 shares at 127.5501 arrive
    as a value of 382.65 rather than 382.6503.  The tolerance is therefore one
    minor unit on each side -- one of the transaction currency for a pre-rounded
    value, plus ``price`` of the account's commodity for a pre-rounded quantity.
    """
    commodity = account.commodity
    tolerance = Fraction(1, currency.minor_unit_factor) + price * Fraction(
        1, commodity.minor_unit_factor
    )
    residual = abs(value - quantity * price)
    if residual > tolerance:
        implied = "undefined" if quantity == 0 else str(value / quantity)
        raise ValidationError(
            f"leg on {account.name} is over-specified and inconsistent: "
            f"value {value} {currency.code} with quantity {quantity} "
            f"{commodity.code} implies a price of {implied}, not {price} "
            f"(off by {residual}, tolerance {tolerance}); supply any two of "
            f"value, quantity and price and let the third be derived"
        )


def _resolve(leg: LegSpec, account: Account, currency: Commodity) -> _Resolved:
    """Fill in the missing one of value/quantity/price for a non-residual leg."""
    commodity = account.commodity
    same = commodity.id == currency.id
    value, quantity, price = leg.value, leg.quantity, leg.price

    if leg.is_residual:
        # Completed later, once the rest of the transaction is known.
        return _Resolved(Fraction(0), Fraction(0), price)

    if same:
        if value is not None and quantity is not None and value != quantity:
            raise ValidationError(
                f"account {account.name} is held in the transaction currency "
                f"{currency.code}, so value ({value}) and quantity "
                f"({quantity}) must be equal"
            )
        resolved = value if value is not None else quantity
        assert resolved is not None
        if price is not None:
            _check_price_agrees(resolved, resolved, price, account, currency)
        return _Resolved(resolved, resolved, price)

    if value is not None and quantity is not None:
        if price is not None:
            _check_price_agrees(value, quantity, price, account, currency)
        return _Resolved(value, quantity, price)
    if value is not None:
        if price is None:
            raise ValidationError(
                f"leg on {account.name} ({commodity.code}) in a "
                f"{currency.code} transaction needs a price or a quantity"
            )
        return _Resolved(value, value / price, price)
    assert quantity is not None
    if price is None:
        raise ValidationError(
            f"leg on {account.name} ({commodity.code}) in a {currency.code} "
            f"transaction needs a price or a value"
        )
    return _Resolved(quantity * price, quantity, price)


def _complete_residual(
    current: _Resolved,
    value: Fraction,
    leg: LegSpec,
    account: Account,
    currency: Commodity,
) -> _Resolved:
    """Give the residual leg the value that balances the transaction."""
    commodity = account.commodity
    if commodity.id == currency.id:
        return _Resolved(value, value, current.price)
    if current.price is None:
        raise ValidationError(
            f"residual leg on {account.name} ({commodity.code}) in a "
            f"{currency.code} transaction needs a price"
        )
    return _Resolved(value, value / current.price, current.price)
