"""Command handlers for payees (decision #13).

Payees accumulate duplicates faster than anything else in a personal finance
app, so merging them is a real command that lands in the log as one revision."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .. import commands as cmd
from ..changelog import EntityKind
from ..errors import ConflictError, ValidationError
from ..ids import new_id
from ..payee import Payee

if TYPE_CHECKING:  # pragma: no cover
    from ..book import Book


def create_payee(book: Book, command: cmd.CreatePayee) -> Payee:
    existing = book.find_payee(command.name)
    if existing is not None:
        raise ConflictError(
            f"payee {command.name!r} already exists as {existing.name!r}"
        )
    payee = Payee(
        id=command.id or new_id(),
        book_id=book.id,
        name=command.name,
        notes=command.notes,
        address=command.address,
        match_keys=command.match_keys,
        default_account_id=command.default_account_id,
    )
    if payee.default_account_id is not None:
        book.account(payee.default_account_id)
    if book.store.get(EntityKind.PAYEE, payee.id) is not None:
        raise ConflictError(f"payee {payee.id} already exists")
    return book.put(EntityKind.PAYEE, payee, created=True)


def update_payee(book: Book, command: cmd.UpdatePayee) -> Payee:
    current = book.payee(command.payee_id)
    updated = current
    if not isinstance(command.name, cmd.Unset):
        other = book.find_payee(command.name)
        if other is not None and other.id != current.id:
            raise ConflictError(f"payee {command.name!r} already exists")
        updated = replace(updated, name=command.name)
    for field_name in ("notes", "address", "match_keys", "default_account_id"):
        value = getattr(command, field_name)
        if not isinstance(value, cmd.Unset):
            updated = replace(updated, **{field_name: value})
    if updated.default_account_id is not None:
        book.account(updated.default_account_id)
    if updated == current:
        return current
    return book.put(EntityKind.PAYEE, updated)


def merge_payees(book: Book, command: cmd.MergePayees) -> Payee:
    target = book.payee(command.target_id)
    sources = [book.payee(i) for i in command.source_ids]
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
            target = book.put(EntityKind.PAYEE, merged, note=note)

    touched_transactions = {
        t.id: t for source in sources for t in book.store.transactions_for_payee(source.id)
    }
    for transaction in sorted(
        touched_transactions.values(), key=lambda t: (t.date, t.id.int)
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
            book.put(
                EntityKind.TRANSACTION,
                replace(transaction, payee_id=new_payee_id, splits=tuple(splits)),
                note=note,
            )

    for source in sources:
        book.drop(EntityKind.PAYEE, source.id, note=f"merged into {target.id}")
    return book.payee(target.id)


def delete_payee(book: Book, command: cmd.DeletePayee) -> Payee:
    payee = book.payee(command.payee_id)
    users = book.store.transactions_for_payee(payee.id)
    if users:
        raise ConflictError(
            f"payee {payee.name!r} is used by {len(users)} transaction(s); "
            f"merge it instead"
        )
    return book.drop(EntityKind.PAYEE, payee.id)
