"""Command handlers for transactions (decisions #1, #3, #4, #11).

Splits are always replaced wholesale rather than patched one at a time: a
partial edit can leave the transaction unbalanced, and an unbalanced row must
never exist, not even transiently."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any
from uuid import UUID

from .. import commands as cmd
from ..account import Account
from ..changelog import EntityKind
from ..commodity import Commodity
from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id
from ..posting import LegSpec, book_legs
from ..transaction import Split, Transaction

if TYPE_CHECKING:  # pragma: no cover
    from ..book import Book


def account_for_posting(book: Book, account_id: UUID) -> Account:
    account = book.account(account_id)
    if account.placeholder:
        raise ValidationError(
            f"{book.path(account)!r} is a placeholder and accepts no postings"
        )
    if account.closed:
        raise ValidationError(
            f"{book.path(account)!r} is closed and accepts no postings"
        )
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
    splits = build_splits(book,
        tuple(command.legs),
        currency,
        command.rounding,
        command.imbalance_account_id,
    )
    for split in splits:
        if split.payee_id is not None:
            book.payee(split.payee_id)
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
    if book.store.get(EntityKind.TRANSACTION, transaction.id) is not None:
        raise ConflictError(f"transaction {transaction.id} already exists")
    return book.put(EntityKind.TRANSACTION, transaction, created=True)


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
        splits = build_splits(book,
            tuple(command.legs),
            currency,
            rounding,
            command.imbalance_account_id,
        )
        for split in splits:
            if split.payee_id is not None:
                book.payee(split.payee_id)
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
