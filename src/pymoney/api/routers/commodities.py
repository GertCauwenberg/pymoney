"""Commodities: currencies and securities, uniformly."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ...core import commands as cmd
from ...core.commands import UNSET
from ...core.commodity import CommodityKind
from ...quotes import QuoteService
from ..deps import IdempotencyKey, ReadableBook, WritableBook, get_quotes
from ..schemas import CommodityIn, CommodityOut, CommodityPatch
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}/commodities", tags=["commodities"])

# Starlette renamed this status constant; 422 is the number either way.
UNPROCESSABLE = 422


def _check_quote_source(source: str | None, quotes: QuoteService) -> None:
    """A quote source names an installed provider, or nothing.

    The domain accepts any string (an importer has to round-trip whatever a
    file says), but a user setting one by hand gets told now rather than
    finding out when the scheduler reports "no such provider".
    """
    if source and source not in quotes.registry:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=(
                f"no quote provider called {source!r}; installed: "
                f"{', '.join(quotes.registry.names) or 'none'}"
            ),
        )


@router.get("", response_model=list[CommodityOut], summary="List commodities")
def list_commodities(
    book: ReadableBook,
    response: Response,
    kind: CommodityKind | None = Query(default=None),
) -> list[CommodityOut]:
    set_revision(response, book)
    return [
        CommodityOut.of(c) for c in book.commodities() if kind is None or c.kind is kind
    ]


@router.post(
    "",
    response_model=CommodityOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a commodity",
)
def create_commodity(
    body: CommodityIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
    quotes: Annotated[QuoteService, Depends(get_quotes)],
) -> CommodityOut:
    _check_quote_source(body.quote_source, quotes)
    result = book.execute(
        cmd.CreateCommodity(
            code=body.code,
            name=body.name,
            kind=body.kind,
            minor_unit_factor=body.factor(),
            display_precision=body.display_precision,
            symbol=body.symbol,
            isin=body.isin,
            quote_source=body.quote_source,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return CommodityOut.of(result.entity)


@router.get("/{commodity_id}", response_model=CommodityOut, summary="One commodity")
def get_commodity(
    commodity_id: UUID, book: ReadableBook, response: Response
) -> CommodityOut:
    set_revision(response, book)
    return CommodityOut.of(book.commodity(commodity_id))


@router.patch(
    "/{commodity_id}",
    response_model=CommodityOut,
    summary="Update a commodity's display fields",
)
def update_commodity(
    commodity_id: UUID,
    body: CommodityPatch,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
    quotes: Annotated[QuoteService, Depends(get_quotes)],
) -> CommodityOut:
    """Name, code, symbol, precision and provenance. The minor unit factor is
    fixed for life: every booked amount is a count of it."""
    fields = body.model_dump(exclude_unset=True)
    _check_quote_source(fields.get("quote_source"), quotes)
    result = book.execute(
        cmd.UpdateCommodity(
            commodity_id=commodity_id,
            code=fields.get("code", UNSET),
            name=fields.get("name", UNSET),
            symbol=fields.get("symbol", UNSET),
            display_precision=fields.get("display_precision", UNSET),
            isin=fields.get("isin", UNSET),
            quote_source=fields.get("quote_source", UNSET),
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return CommodityOut.of(result.entity)


@router.delete(
    "/{commodity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unused commodity",
)
def delete_commodity(commodity_id: UUID, book: WritableBook, key: IdempotencyKey) -> None:
    book.execute(cmd.DeleteCommodity(commodity_id=commodity_id, command_id=key))


__all__ = ["router"]
