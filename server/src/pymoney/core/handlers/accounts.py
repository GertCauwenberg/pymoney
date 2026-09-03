"""Command handlers for the account tree (decisions #1, #13).

Renames and moves are cheap because paths are derived, never stored. Merges are
first-class: they repoint splits and children in a single revision rather than
leaving a client to reconcile a pile of row updates."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import UUID

from .. import commands as cmd
from ..account import Account
from ..changelog import EntityKind
from ..errors import ConflictError, ValidationError
from ..ids import new_id

if TYPE_CHECKING:  # pragma: no cover
    from ..book import Book


def check_sibling_name(
    book: Book,
    parent_id: UUID | None,
    name: str,
    *,
    exclude: UUID | None = None,
) -> None:
    """Refuse a name already taken by a sibling, case-insensitively."""
    key = name.strip().casefold()
    for sibling in book.children(parent_id):
        if sibling.id != exclude and sibling.key == key:
            where = "the top level" if parent_id is None else book.path(parent_id)
            raise ConflictError(f"{where} already has an account named {name!r}")


def create_account(book: Book, command: cmd.CreateAccount) -> Account:
    commodity = (
        book.commodity(command.commodity_id)
        if command.commodity_id is not None
        else book.base_currency
    )
    parent = None
    if command.parent_id is not None:
        parent = book.account(command.parent_id)
        if parent.account_class is not command.type.account_class:
            raise ValidationError(
                f"cannot put a {command.type} account under "
                f"{book.path(parent)!r}, which is a {parent.account_class} "
                f"account"
            )
    check_sibling_name(book, command.parent_id, command.name)
    account = Account(
        id=command.id or new_id(),
        book_id=book.id,
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
    if book.store.exists(EntityKind.ACCOUNT, account.id):
        raise ConflictError(f"account {account.id} already exists")
    return book.put(EntityKind.ACCOUNT, account, created=True)


def update_account(book: Book, command: cmd.UpdateAccount) -> Account:
    current = book.account(command.account_id)
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
        and book.store.posting_count(current.id)
    ):
        raise ValidationError(
            f"{book.path(current)!r} has postings and cannot become a placeholder"
        )
    if updated == current:
        return current
    return book.put(EntityKind.ACCOUNT, updated)


def rename_account(book: Book, command: cmd.RenameAccount) -> Account:
    current = book.account(command.account_id)
    if current.name == command.name:
        return current
    check_sibling_name(book, current.parent_id, command.name, exclude=current.id)
    return book.put(
        EntityKind.ACCOUNT,
        replace(current, name=command.name),
        note=f"renamed from {current.name!r}",
    )


def set_account_commodity(book: Book, command: cmd.SetAccountCommodity) -> Account:
    current = book.account(command.account_id)
    commodity = book.commodity(command.commodity_id)
    if commodity.id == current.commodity.id:
        return current
    posted = book.store.posting_count(current.id)
    if posted:
        raise ConflictError(
            f"{book.path(current)!r} already has {posted} "
            f"transaction(s) denominated in {current.commodity.code}; "
            f"changing its commodity to {commodity.code} would silently "
            f"reinterpret them. Create a {commodity.code} account and "
            f"transfer the balance instead"
        )
    return book.put(
        EntityKind.ACCOUNT,
        replace(current, commodity=commodity),
        note=f"commodity {current.commodity.code} -> {commodity.code}",
    )


def move_account(book: Book, command: cmd.MoveAccount) -> Account:
    current = book.account(command.account_id)
    if current.parent_id == command.new_parent_id:
        return current
    if command.new_parent_id is not None:
        new_parent = book.account(command.new_parent_id)
        if new_parent.id == current.id:
            raise ValidationError("an account cannot be its own parent")
        if any(a.id == new_parent.id for a in book.descendants(current)):
            raise ValidationError(
                f"cannot move {book.path(current)!r} under its own "
                f"descendant {book.path(new_parent)!r}"
            )
        if new_parent.account_class is not current.account_class:
            raise ValidationError(
                f"cannot move a {current.account_class} account under a "
                f"{new_parent.account_class} account"
            )
    check_sibling_name(book, command.new_parent_id, current.name, exclude=current.id)
    old_path = book.path(current)
    return book.put(
        EntityKind.ACCOUNT,
        replace(current, parent_id=command.new_parent_id),
        note=f"moved from {old_path!r}",
    )


def set_account_closed(book: Book, command: cmd.SetAccountClosed) -> Account:
    current = book.account(command.account_id)
    if current.closed == command.closed:
        return current
    if command.closed:
        open_children = [c for c in book.descendants(current) if not c.closed]
        if open_children:
            raise ValidationError(
                f"close the {len(open_children)} open sub-account(s) of "
                f"{book.path(current)!r} first"
            )
    return book.put(EntityKind.ACCOUNT, replace(current, closed=command.closed))


def merge_accounts(book: Book, command: cmd.MergeAccounts) -> Account:
    source = book.account(command.source_id)
    target = book.account(command.target_id)
    if source.id == target.id:
        raise ValidationError("cannot merge an account into itself")
    if source.commodity.id != target.commodity.id:
        raise ValidationError(
            f"cannot merge {source.commodity.code} account "
            f"{book.path(source)!r} into {target.commodity.code} account "
            f"{book.path(target)!r}"
        )
    if source.account_class is not target.account_class:
        raise ValidationError(
            f"cannot merge a {source.account_class} account into a "
            f"{target.account_class} account"
        )
    if any(a.id == target.id for a in book.descendants(source)):
        raise ValidationError(
            f"cannot merge {book.path(source)!r} into its own descendant"
        )

    # A merge moves the source's postings onto the target, so the target must
    # be an account that accepts postings -- the same rule posting itself
    # enforces. A merge of an empty account (a pure subtree reshuffle) may
    # still land on a placeholder: no posting arrives.
    transactions = book.store.transactions(account_ids=[source.id])
    if transactions and not target.accepts_postings:
        why = "a placeholder" if target.placeholder else "closed"
        raise ValidationError(
            f"cannot merge {book.path(source)!r} into {book.path(target)!r}: "
            f"the target is {why} and accepts no postings, and the source has "
            f"{len(transactions)} transaction(s) to move"
        )
    # A frozen split is a closed accounting period (decision #11): what its
    # books said, including which account a leg was on, is never rewritten.
    frozen = [
        t
        for t in transactions
        if any(s.is_locked and s.reconcile_state.is_final for s in t.splits)
    ]
    if frozen:
        raise ValidationError(
            f"cannot merge {book.path(source)!r}: {len(frozen)} of its "
            f"transaction(s) are in a frozen period, which is never rewritten"
        )

    source_path = book.path(source)
    note = f"merged from {source_path!r}"

    # Re-point the children first, so paths stay valid as we go.
    for child in book.children(source.id):
        check_sibling_name(book, target.id, child.name)
        book.put(
            EntityKind.ACCOUNT,
            replace(child, parent_id=target.id),
            note=note,
        )

    for transaction in transactions:
        splits = tuple(
            replace(s, account_id=target.id) if s.account_id == source.id else s
            for s in transaction.splits
        )
        book.put(
            EntityKind.TRANSACTION,
            transaction.replace_splits(splits),
            note=note,
        )

    book.drop(EntityKind.ACCOUNT, source.id, note=f"merged into {target.id}")
    return book.account(target.id)


def delete_account(book: Book, command: cmd.DeleteAccount) -> Account:
    account = book.account(command.account_id)
    if book.children(account.id):
        raise ConflictError(
            f"{book.path(account)!r} has sub-accounts; move or delete them first"
        )
    count = book.store.posting_count(account.id)
    if count:
        raise ConflictError(
            f"{book.path(account)!r} is used by {count} transaction(s); "
            f"merge it into another account instead"
        )
    return book.drop(EntityKind.ACCOUNT, account.id)
