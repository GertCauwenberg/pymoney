"""The quote provider interface.

A provider is a small class that knows one thing: how to ask one remote service
for the rate between two commodities over a range of dates. It does no storage,
no scheduling and no policy -- :mod:`pymoney.quotes.service` does those, the
same way for every provider -- so writing one is a page of code, and a user can
ship their own as a plugin (see :mod:`pymoney.quotes.registry`).

The contract a provider keeps:

* ``fetch`` returns every quote it could get for the request's date range,
  oldest first, and raises :class:`QuoteError` for anything it could not do.
  Partial results with a raised error are not a thing: either the call
  succeeded or it did not.
* Rates are exact :class:`~fractions.Fraction` objects built from the
  provider's *decimal text*, never from a float that was parsed in between.
  Where the service itself only hands out binary floats (Yahoo), the provider
  rounds them to the precision the service declares and says so in ``raw``.
* ``raw`` is the provider's own rendering of the number, kept for the record.
* HTTP goes through the ``http`` callable the provider is given, so tests and
  the scheduler control the network, not the provider.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any

from ..core.commodity import Commodity

__all__ = [
    "Http",
    "Quote",
    "QuoteError",
    "QuoteProvider",
    "QuoteRequest",
    "urllib_http",
]

Http = Callable[[str, Mapping[str, str]], bytes]
"""``http(url, headers) -> body``: a GET that raises :class:`QuoteError` on failure."""

DEFAULT_TIMEOUT = float(os.environ.get("PYMONEY_QUOTE_TIMEOUT", "60"))
"""Seconds per request. The ECB's API routinely takes 20-40 s on a cold series,
so this is generous; PYMONEY_QUOTE_TIMEOUT overrides it."""
RETRIES_ON_TIMEOUT = 1


class QuoteError(Exception):
    """A provider could not answer a request. The message is for the user."""


@dataclass(frozen=True, slots=True, kw_only=True)
class QuoteRequest:
    """Price ``commodity`` in ``currency`` for ``start..end`` (inclusive)."""

    commodity: Commodity
    currency: Commodity
    """The book's base currency. A provider may answer in another currency
    (ECB only quotes against EUR); the service then relies on triangulation."""
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"start {self.start} is after end {self.end}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Quote:
    """``1 base = rate quote`` on ``effective_date``, as the provider said it.

    Codes, not commodities: a provider knows tickers and ISO codes, not the
    book. The service maps them onto the book's commodities and refuses quotes
    it cannot place.
    """

    base_code: str
    quote_code: str
    effective_date: date
    rate: Fraction
    raw: str
    """The number as the provider rendered it, e.g. ``"1.0842"``."""

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError(f"a quote must be positive, got {self.rate}")


class QuoteProvider(ABC):
    """One remote source of prices."""

    name: str
    """Short, stable, lower-case; stored as :attr:`Price.source` on every price
    fetched through this provider and as :attr:`Commodity.quote_source` on the
    commodities it is responsible for."""
    description: str = ""
    """One line for a UI's provider picker."""

    @abstractmethod
    def fetch(self, request: QuoteRequest, http: Http) -> list[Quote]:
        """Every quote available for the request, oldest first.

        :raises QuoteError: if the service could not be reached, refused the
            request, or returned something that could not be read.
        """

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} {self.name!r}>"


def urllib_http(url: str, headers: Mapping[str, str]) -> bytes:
    """The default transport: a plain GET with a timeout.

    Standard library only, like everything else in the server. A provider that
    needs more (authentication, POST) wraps this or brings its own.
    """
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
                return bytes(response.read())
        except urllib.error.HTTPError as exc:
            raise QuoteError(f"{url}: HTTP {exc.code} {exc.reason}") from exc
        except TimeoutError as exc:
            # A slow service, not a broken one: try once more before giving up.
            if attempt <= RETRIES_ON_TIMEOUT:
                continue
            raise QuoteError(
                f"{url}: timed out after {DEFAULT_TIMEOUT:.0f}s (twice); the service is "
                f"slow, raise PYMONEY_QUOTE_TIMEOUT or try again later"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            if (
                isinstance(
                    exc.reason if isinstance(exc, urllib.error.URLError) else None,
                    TimeoutError,
                )
                and attempt <= RETRIES_ON_TIMEOUT
            ):
                continue
            raise QuoteError(f"{url}: {exc}") from exc


def parse_json(body: bytes, what: str) -> Any:
    """Decode a JSON body or explain which service sent garbage."""
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuoteError(f"{what} returned something that is not JSON: {exc}") from exc
