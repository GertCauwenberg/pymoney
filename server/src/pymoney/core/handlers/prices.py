"""Command handlers for prices (decisions #2, #9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import commands as cmd
from ..changelog import EntityKind
from ..errors import NotFoundError
from ..ids import new_id
from ..price import Price
from ..rounding import to_fraction

if TYPE_CHECKING:  # pragma: no cover
    from ..book import Book


def add_price(book: Book, command: cmd.AddPrice) -> Price:
    base = book.commodity(command.base_id)
    quote = book.commodity(command.quote_id)
    price = Price.of(
        base,
        quote,
        to_fraction(command.rate),
        effective_date=command.effective_date,
        as_of=command.as_of or book.clock.now(),
        source=command.source,
        id=command.id or new_id(),
        book_id=book.id,
    )
    created = book.prices.get(price.id) is None
    return book.put(EntityKind.PRICE, price, created=created)


def delete_price(book: Book, command: cmd.DeletePrice) -> Price:
    if book.prices.get(command.price_id) is None:
        raise NotFoundError(f"no price {command.price_id}")
    return book.drop(EntityKind.PRICE, command.price_id)
