"""Command handlers for commodities (decision #2)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .. import commands as cmd
from ..changelog import EntityKind
from ..commodity import Commodity
from ..errors import ConflictError, ValidationError
from ..ids import new_id

if TYPE_CHECKING:  # pragma: no cover
    from ..book import Book


def create_commodity(book: Book, command: cmd.CreateCommodity) -> Commodity:
    key = command.code.strip().upper()
    if any(c.key == key for c in book.commodities()):
        raise ConflictError(f"commodity {command.code} already exists")
    commodity = Commodity(
        id=command.id or new_id(),
        book_id=book.id,
        kind=command.kind,
        code=command.code,
        name=command.name or command.code,
        symbol=command.symbol,
        minor_unit_factor=command.minor_unit_factor,
        display_precision=command.display_precision,
        isin=command.isin,
        quote_source=command.quote_source,
    )
    if book.store.exists(EntityKind.COMMODITY, commodity.id):
        raise ConflictError(f"commodity {commodity.id} already exists")
    return book.put(EntityKind.COMMODITY, commodity, created=True)


def update_commodity(book: Book, command: cmd.UpdateCommodity) -> Commodity:
    current = book.commodity(command.commodity_id)
    updated = current
    if not isinstance(command.code, cmd.Unset):
        key = command.code.strip().upper()
        if key != current.key and any(c.key == key for c in book.commodities()):
            raise ConflictError(f"commodity {command.code} already exists")
        updated = replace(updated, code=command.code)
    for field_name in ("name", "symbol", "display_precision", "isin", "quote_source"):
        value = getattr(command, field_name)
        if not isinstance(value, cmd.Unset):
            updated = replace(updated, **{field_name: value})
    if updated == current:
        return current
    return book.put(EntityKind.COMMODITY, updated)


def delete_commodity(book: Book, command: cmd.DeleteCommodity) -> Commodity:
    commodity = book.commodity(command.commodity_id)
    if commodity.id == book.base_currency_id:
        raise ValidationError(
            f"{commodity.code} is the book's base currency and cannot be deleted"
        )
    users = [a for a in book.accounts() if a.commodity.id == commodity.id]
    if users:
        raise ConflictError(
            f"{commodity.code} is used by {len(users)} account(s), "
            f"starting with {book.path(users[0])!r}"
        )
    priced = [p for p in book.prices.all() if commodity.id in (p.base.id, p.quote.id)]
    if priced:
        raise ConflictError(
            f"{commodity.code} has {len(priced)} price(s); delete them first"
        )
    return book.drop(EntityKind.COMMODITY, commodity.id)
