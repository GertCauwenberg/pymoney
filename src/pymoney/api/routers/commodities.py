"""Commodities: currencies and securities, uniformly."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ...core import commands as cmd
from ...core.commodity import CommodityKind
from ..deps import IdempotencyKey, ReadableBook, WritableBook
from ..schemas import CommodityIn, CommodityOut
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}/commodities", tags=["commodities"])


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
) -> CommodityOut:
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


@router.delete(
    "/{commodity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unused commodity",
)
def delete_commodity(commodity_id: UUID, book: WritableBook, key: IdempotencyKey) -> None:
    book.execute(cmd.DeleteCommodity(commodity_id=commodity_id, command_id=key))


__all__ = ["router"]
