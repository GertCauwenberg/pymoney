"""Fetching quotes: on demand, with backfill, and the last run's report.

The scheduler does the routine work; these endpoints are for the moments a
person is waiting: "fetch now", "fill in last year", "why is there no price?".
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ...quotes import QuoteService
from ..deps import CurrentUser, ReadableBook, WritableBook, get_quotes
from ..schemas import QuoteFetchIn, QuoteProviderOut, QuoteRunOut
from ._shared import set_revision

router = APIRouter(tags=["quotes"])


@router.get(
    "/quote-providers",
    response_model=list[QuoteProviderOut],
    summary="The installed quote providers",
)
def list_providers(
    actor: CurrentUser, quotes: Annotated[QuoteService, Depends(get_quotes)]
) -> list[QuoteProviderOut]:
    """What a commodity's `quote_source` may be set to."""
    del actor
    return [
        QuoteProviderOut(name=p.name, description=p.description) for p in quotes.registry
    ]


@router.post(
    "/books/{book_id}/quotes/fetch",
    response_model=QuoteRunOut,
    summary="Fetch quotes now, optionally backfilling",
)
def fetch_quotes(
    body: QuoteFetchIn,
    book: WritableBook,
    response: Response,
    quotes: Annotated[QuoteService, Depends(get_quotes)],
) -> QuoteRunOut:
    """Runs synchronously under the book's lock and returns the report. A
    provider failing is a problem in the report, not an error response."""
    report = quotes.run(
        book,
        start=body.start,
        end=body.end,
        commodity_ids=body.commodity_ids,
        providers=body.providers,
    )
    set_revision(response, book)
    return QuoteRunOut.of(report)


@router.get(
    "/books/{book_id}/quotes/last",
    response_model=QuoteRunOut | None,
    summary="The last quote run for this book, if any since the server started",
)
def last_run(
    book: ReadableBook,
    response: Response,
    quotes: Annotated[QuoteService, Depends(get_quotes)],
) -> QuoteRunOut | None:
    set_revision(response, book)
    report = quotes.last_runs.get(book.id)
    return None if report is None else QuoteRunOut.of(report)


__all__ = ["router"]
