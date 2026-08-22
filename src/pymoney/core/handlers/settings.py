"""Command handlers for the book row itself (decisions #5, #6).

Creating a book, renaming it and changing its reporting currency are operations
like any other: they get a command, a revision and a change record, so a replica
is told about them instead of having to guess."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .. import commands as cmd
from ..changelog import EntityKind
from ..commodity import Commodity
from ..errors import ConflictError, ValidationError
from ..settings import BookSettings

if TYPE_CHECKING:  # pragma: no cover
    from ..book import Book


def initialise_book(book: Book, command: cmd.InitialiseBook) -> BookSettings:
    if book.revision != 0:
        raise ConflictError(
            f"book {book.id} is already initialised (r{book.revision})"
        )
    book.set_book_field("name", command.name, note="book created")
    if command.base_currency is not None:
        commodity: Commodity = command.base_currency
        stored = book.put(
            EntityKind.COMMODITY,
            replace(commodity, book_id=book.id),
            created=True,
        )
        book.set_book_field(
            "_base_currency_id",
            stored.id,
            note=f"base currency {stored.code}",
        )
    return book.settings


def set_base_currency(book: Book, command: cmd.SetBaseCurrency) -> BookSettings:
    currency = book.commodity(command.commodity_id)
    if not currency.is_currency:
        raise ValidationError(f"{currency.code} is a {currency.kind}, not a currency")
    if currency.id == book.base_currency_id:
        return book.settings
    previous = (
        book.base_currency.code
        if book.base_currency_id is not None
        else None
    )
    return book.set_book_field(
        "_base_currency_id",
        currency.id,
        note=f"base currency {previous} -> {currency.code}",
    )


def rename_book(book: Book, command: cmd.RenameBook) -> BookSettings:
    if not command.name.strip():
        raise ValidationError("book name must not be empty")
    if command.name == book.name:
        return book.settings
    return book.set_book_field(
        "name", command.name, note=f"renamed from {book.name!r}"
    )
