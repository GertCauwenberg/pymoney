"""Prices, and converting with them.

Bitemporal throughout (decision #9): a correction is a POST of a new row with a
later ``as_of``, never an edit. ``GET /prices`` therefore takes both a date
filter and a knowledge cutoff, so "what did we think the rate was last Tuesday?"
is answerable.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ...core import commands as cmd
from ...core.price import ValuationPolicy
from ..deps import IdempotencyKey, ReadableBook, WritableBook
from ..schemas import (
    ConversionOut,
    PriceIn,
    PriceOut,
    PricePage,
    RateOut,
    to_fraction,
)
from ._shared import decode_cursor, encode_cursor, page_size, set_revision

router = APIRouter(prefix="/books/{book_id}/prices", tags=["prices"])


@router.get("", response_model=PricePage, summary="Price history")
def list_prices(
    book: ReadableBook,
    response: Response,
    base_id: UUID | None = Query(default=None),
    quote_id: UUID | None = Query(default=None),
    source: str | None = Query(default=None),
    on_or_after: date | None = Query(default=None),
    on_or_before: date | None = Query(default=None),
    known_before: datetime | None = Query(
        default=None,
        description=(
            "Only prices learned before this instant, so an old report can be "
            "reproduced exactly."
        ),
    ),
    cursor: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
) -> PricePage:
    set_revision(response, book)
    size = page_size(limit, 200, 1000)
    found = [
        price
        for price in book.prices.all()
        if (base_id is None or price.base.id == base_id)
        and (quote_id is None or price.quote.id == quote_id)
        and (source is None or price.source == source)
        and (on_or_after is None or price.effective_date >= on_or_after)
        and (on_or_before is None or price.effective_date <= on_or_before)
        and (known_before is None or price.as_of < known_before)
    ]
    position = decode_cursor(cursor)
    if position is not None:
        mark = (date.fromisoformat(position["d"]), UUID(position["i"]).int)
        found = [p for p in found if (p.effective_date, p.id.int) > mark]
    window = found[: size + 1]
    has_more = len(window) > size
    window = window[:size]
    next_cursor = (
        encode_cursor({"d": str(window[-1].effective_date), "i": str(window[-1].id)})
        if has_more and window
        else None
    )
    return PricePage(
        items=[PriceOut.of(p) for p in window],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "",
    response_model=PriceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a price",
)
def create_price(
    body: PriceIn, book: WritableBook, response: Response, key: IdempotencyKey
) -> PriceOut:
    result = book.execute(
        cmd.AddPrice(
            base_id=body.base_id,
            quote_id=body.quote_id,
            rate=to_fraction(body.rate),
            effective_date=body.effective_date,
            as_of=body.as_of,
            source=body.source,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return PriceOut.of(result.entity)


@router.delete(
    "/{price_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a price"
)
def delete_price(price_id: UUID, book: WritableBook, key: IdempotencyKey) -> None:
    book.execute(cmd.DeletePrice(price_id=price_id, command_id=key))


@router.get(
    "/convert",
    response_model=ConversionOut,
    summary="Find a rate, and show where it came from",
)
def convert(
    book: ReadableBook,
    response: Response,
    base_id: UUID,
    quote_id: UUID,
    as_of: date | None = Query(default=None, description="Valuation date."),
    known_before: datetime | None = Query(default=None),
    max_hops: int = Query(default=4, ge=1, le=8),
    allow_inverse: bool = Query(default=True),
    allow_triangulation: bool = Query(default=True),
) -> ConversionOut:
    set_revision(response, book)
    base = book.commodity(base_id)
    quote = book.commodity(quote_id)
    policy = ValuationPolicy(
        target=quote,
        as_of=as_of or book.clock.today(),
        knowledge_time=known_before,
        max_hops=max_hops,
        allow_inverse=allow_inverse,
        allow_triangulation=allow_triangulation,
    )
    conversion = book.prices.convert(base, quote, policy)
    return ConversionOut(
        base=base.code,
        quote=quote.code,
        rate=RateOut.of(conversion.rate),
        hops=conversion.hops,
        path=[base.code] + [p.quote.code for p in conversion.prices],
        sources=sorted({p.source for p in conversion.prices}),
    )


__all__ = ["router"]
