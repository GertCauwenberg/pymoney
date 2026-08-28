"""Price quotes: pluggable providers, a fetching service and a scheduler.

    from pymoney.quotes import ProviderRegistry, QuoteService

    service = QuoteService(ProviderRegistry.discover())
    report = service.run(book)                 # everything since the last run
    report = service.run(book, start=date(2020, 1, 1))   # backfill
    print(report.summary())

A provider (:class:`QuoteProvider`) turns one request -- price this commodity in
that currency over these dates -- into exact rational quotes. Two ship with
PyMoney, the ECB for currencies and Yahoo Finance for securities; a user adds
their own through an entry point or ``PYMONEY_QUOTE_PLUGINS``.

Fetched prices are ordinary :class:`~pymoney.core.price.Price` rows: ``source``
is the provider's name, ``as_of`` the instant they were fetched, the rate the
exact decimal the provider published. They sit beside hand-entered prices, which
the valuation policy ranks above them for the same day.
"""

from __future__ import annotations

from .ecb import EcbProvider
from .provider import Http, Quote, QuoteError, QuoteProvider, QuoteRequest, urllib_http
from .registry import ENTRY_POINT_GROUP, ProviderRegistry
from .scheduler import QuoteScheduler
from .service import FetchProblem, FetchReport, PlannedFetch, QuoteService
from .yahoo import YahooProvider

__all__ = [
    "ENTRY_POINT_GROUP",
    "EcbProvider",
    "FetchProblem",
    "FetchReport",
    "Http",
    "PlannedFetch",
    "ProviderRegistry",
    "Quote",
    "QuoteError",
    "QuoteProvider",
    "QuoteRequest",
    "QuoteScheduler",
    "QuoteService",
    "YahooProvider",
    "urllib_http",
]
