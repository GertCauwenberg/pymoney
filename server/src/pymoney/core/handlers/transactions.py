"""Command handlers for transactions (decisions #1, #3, #4, #11).

Splits are always replaced wholesale rather than patched one at a time: a
partial edit can leave the transaction unbalanced, and an unbalanced row must
never exist, not even transiently."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date
from fractions import Fraction
from typing import TYPE_CHECKING, Any
from uuid import UUID

from .. import commands as cmd
from ..account import Account
from ..changelog import EntityKind
from ..commodity import Commodity
from ..errors import ConflictError, NoPriceError, NotFoundError, ValidationError
from ..ids import new_id
from ..posting import LegSpec, book_legs
from ..price import Price, PriceSource
from ..transaction import Split, Transaction

if TYPE_CHECKING:  # pragma: no cover
    from ..book import Book

log = logging.getLogger("pymoney.core.transactions")


def account_for_posting(book: Book, account_id: UUID) -> Account:
    account = book.account(account_id)
    if account.placeholder:
        raise ValidationError(
            f"{book.path(account)!r} is a placeholder and accepts no postings"
        )
    if account.closed:
        raise ValidationError(f"{book.path(account)!r} is closed and accepts no postings")
    return account


def build_splits(
    book: Book,
    legs: tuple[LegSpec, ...],
    currency: Commodity,
    rounding: Any,
    imbalance_account_id: UUID | None,
) -> tuple[Split, ...]:
    """Resolve and book legs, refusing accounts that cannot take a posting."""
    booked = book_legs(
        legs,
        currency=currency,
        resolve_account=lambda account_id: account_for_posting(book, account_id),
        rounding=rounding,
        imbalance_account_id=imbalance_account_id,
    )
    return booked.splits


def _looked_up_rate(
    book: Book, commodity: Commodity, currency: Commodity, on: date, account: Account
) -> Fraction:
    """The book's own rate for ``commodity -> currency`` on ``on``.

    Uses the price effective on the transaction date, or -- with a warning --
    the most recent one before it.  A foreign leg that states neither a
    quantity nor a price has nothing else to go on, so without any usable
    price at all the transaction cannot be booked.
    """
    policy = book.default_policy(as_of=on).for_target(currency)
    try:
        conversion = book.prices.convert(commodity, currency, policy)
    except NoPriceError:
        raise ValidationError(
            f"{book.path(account)!r} is held in {commodity.code}, not the "
            f"transaction currency {currency.code}: enter a quantity, or add "
            f"a {commodity.code}->{currency.code} price on or before {on}"
        ) from None
    used = max((price.effective_date for price in conversion.prices), default=on)
    if used != on:
        log.warning(
            "no %s->%s price for %s; valuing the leg at the rate from %s (1 %s = %s %s)",
            commodity.code,
            currency.code,
            on.isoformat(),
            used.isoformat(),
            commodity.code,
            conversion.rate,
            currency.code,
        )
    return conversion.rate


def price_foreign_legs(
    book: Book, legs: tuple[LegSpec, ...], currency: Commodity, on: date
) -> tuple[tuple[LegSpec, ...], tuple[Price, ...]]:
    """Give every foreign-commodity leg a price, the way the register forms do.

    A leg that states *both* its value and its quantity has defined a rate by
    the act of entering the trade: that becomes a fresh
    :attr:`~pymoney.core.price.PriceSource.TRANSACTION` price.  A leg that
    states only one of the two (or the residual leg, which states neither)
    borrows the book's existing price history instead.

    Returns the legs with their ``price`` filled in and the new prices to
    record.  Legs that already carry a price, and legs in the transaction
    currency, are passed through untouched.
    """
    resolved: list[LegSpec] = []
    implied: list[Price] = []
    for leg in legs:
        account = book.account(leg.account_id)
        commodity = account.commodity
        if commodity.id == currency.id or leg.price is not None:
            resolved.append(leg)
            continue
        value, quantity = leg.value, leg.quantity
        if value is not None and value != 0 and quantity is not None and quantity != 0:
            rate = abs(value) / abs(quantity)
            resolved.append(replace(leg, price=rate))
            implied.append(
                Price.of(
                    commodity,
                    currency,
                    rate,
                    effective_date=on,
                    as_of=book.clock.now(),
                    source=PriceSource.TRANSACTION,
                    id=new_id(),
                    book_id=book.id,
                )
            )
        elif (value is not None and value != 0) or leg.is_residual:
            rate = _looked_up_rate(book, commodity, currency, on, account)
            resolved.append(replace(leg, price=rate))
        else:
            # Quantity only, or a genuine zero leg: book_legs decides.
            resolved.append(leg)
    return tuple(resolved), tuple(implied)


def _validate_split_references(book: Book, splits: tuple[Split, ...]) -> None:
    """Every payee and tag a leg names must exist in the book."""
    for split in splits:
        if split.payee_id is not None:
            book.payee(split.payee_id)
        for tag_id in split.tag_ids:
            book.tag(tag_id)


def post_transaction(book: Book, command: cmd.PostTransaction) -> Transaction:
    currency = (
        book.commodity(command.currency_id)
        if command.currency_id is not None
        else book.base_currency
    )
    if command.payee_id is not None:
        book.payee(command.payee_id)
    if (
        command.imported_id is not None
        and book.store.find_imported(command.imported_id) is not None
    ):
        raise ConflictError(
            f"a transaction with imported_id {command.imported_id!r} already exists"
        )
    legs: tuple[LegSpec, ...] = tuple(command.legs)
    implied_prices: tuple[Price, ...] = ()
    if command.price_foreign_legs:
        legs, implied_prices = price_foreign_legs(book, legs, currency, command.date)
    splits = build_splits(
        book,
        legs,
        currency,
        command.rounding,
        command.imbalance_account_id,
    )
    _validate_split_references(book, splits)
    transaction = Transaction(
        id=command.id or new_id(),
        book_id=book.id,
        date=command.date,
        currency=currency,
        splits=splits,
        memo=command.memo,
        payee_id=command.payee_id,
        number=command.number,
        rounding=command.rounding,
        entered_at=command.entered_at or book.clock.now(),
        imported_id=command.imported_id,
    )
    transaction.validate()
    if book.store.exists(EntityKind.TRANSACTION, transaction.id):
        raise ConflictError(f"transaction {transaction.id} already exists")
    stored = book.put(EntityKind.TRANSACTION, transaction, created=True)
    for price in implied_prices:
        book.put(EntityKind.PRICE, price, created=True)
    return stored


def update_transaction(book: Book, command: cmd.UpdateTransaction) -> Transaction:
    current = book.transaction(command.transaction_id)
    current.require_mutable("modify")

    currency = current.currency
    if not isinstance(command.currency_id, cmd.Unset):
        currency = book.commodity(command.currency_id)
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
        book.payee(updated.payee_id)

    if not isinstance(command.legs, cmd.Unset):
        splits = build_splits(
            book,
            tuple(command.legs),
            currency,
            rounding,
            command.imbalance_account_id,
        )
        _validate_split_references(book, splits)
        updated = replace(updated, splits=splits)
    elif currency is not current.currency:
        raise ValidationError(
            "changing the transaction currency requires re-supplying the legs"
        )

    updated.validate()
    if updated == current:
        return current
    return book.put(EntityKind.TRANSACTION, updated)


def delete_transaction(book: Book, command: cmd.DeleteTransaction) -> Transaction:
    transaction = book.transaction(command.transaction_id)
    transaction.require_mutable("delete")
    return book.drop(EntityKind.TRANSACTION, transaction.id)


def set_split_reconcile_state(
    book: Book, command: cmd.SetSplitReconcileState
) -> Transaction:
    transaction = book.transaction(command.transaction_id)
    split = transaction.get_split(command.split_id)
    updated_split = split.with_reconcile_state(command.state, on=command.on)
    if updated_split == split:
        return transaction
    return book.put(
        EntityKind.TRANSACTION,
        transaction.with_split(updated_split),
        note=f"split {split.id} -> {command.state.name}",
    )


def reconcile_account(book: Book, command: cmd.ReconcileAccount) -> Account:
    account = book.account(command.account_id)
    wanted = set(command.split_ids)
    found: set[UUID] = set()
    for transaction in book.store.transactions(account_ids=[account.id]):
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
            book.put(
                EntityKind.TRANSACTION,
                transaction.replace_splits(tuple(splits)),
                note=f"reconciled {command.state.name}",
            )
    missing = wanted - found
    if missing:
        raise NotFoundError(
            f"{len(missing)} split(s) are not in {book.path(account)!r}: "
            f"{sorted(str(m) for m in missing)[0]}..."
        )
    return account
