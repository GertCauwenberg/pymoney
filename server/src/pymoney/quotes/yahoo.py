"""Yahoo Finance daily closes, for securities.

The commodity's ``code`` is the Yahoo symbol (``MSFT``, ``VWCE.DE``). The chart
endpoint returns the quote currency alongside the closes, so a security traded
in USD in a EUR book is stored as an X→USD price and valued through the EUR/USD
rate -- which is the honest representation, rather than inventing a EUR price
Yahoo never published.

**Yahoo hands out binary floats.** ``417.8800048828125`` is what arrives for a
close of 417.88. The provider rounds each close to the precision the response
declares for the instrument (``meta.priceHint``, the number of decimals Yahoo
itself displays) and records that decimal as ``raw``. That is the most exact
figure the service offers; the ledger stores it as a rational without further
loss.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .provider import Http, Quote, QuoteError, QuoteProvider, QuoteRequest, parse_json

__all__ = ["YahooProvider"]

BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
# Yahoo refuses requests without a browser-like agent.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PyMoney quotes)",
    "Accept": "application/json",
}
DEFAULT_PRICE_HINT = 4


class YahooProvider(QuoteProvider):
    name = "yahoo"
    description = (
        "Yahoo Finance daily closes (stocks, funds, ETFs; the code is the Yahoo symbol)"
    )

    def fetch(self, request: QuoteRequest, http: Http) -> list[Quote]:
        symbol = request.commodity.code.strip()
        if not symbol:
            raise QuoteError("the commodity has no code to use as a Yahoo symbol")
        # period2 is exclusive and Yahoo wants whole days: ask through the end
        # of the last day.
        start = int(
            datetime.combine(
                request.start, datetime.min.time(), tzinfo=timezone.utc
            ).timestamp()
        )
        end = (
            int(
                datetime.combine(
                    request.end, datetime.max.time(), tzinfo=timezone.utc
                ).timestamp()
            )
            + 1
        )
        url = f"{BASE_URL}/{symbol}?period1={start}&period2={end}&interval=1d&events=history"
        body = parse_json(http(url, HEADERS), "Yahoo")
        return self._parse(body, symbol)

    @staticmethod
    def _parse(body: Any, symbol: str) -> list[Quote]:
        chart = body.get("chart") if isinstance(body, dict) else None
        if not isinstance(chart, dict):
            raise QuoteError(f"Yahoo's answer for {symbol} has no chart")
        error = chart.get("error")
        if error:
            description = error.get("description") if isinstance(error, dict) else error
            raise QuoteError(f"Yahoo refused {symbol}: {description}")
        results = chart.get("result") or []
        if not results:
            return []
        result = results[0]
        meta = result.get("meta") or {}
        currency = str(meta.get("currency") or "").upper()
        if not currency:
            raise QuoteError(f"Yahoo did not say which currency {symbol} trades in")
        hint = meta.get("priceHint")
        places = (
            int(hint)
            if isinstance(hint, int | float) and 0 <= hint <= 10
            else DEFAULT_PRICE_HINT
        )
        quantum = Decimal(1).scaleb(-places)
        try:
            zone: Any = ZoneInfo(str(meta.get("exchangeTimezoneName") or "UTC"))
        except ZoneInfoNotFoundError:
            zone = timezone.utc

        timestamps = result.get("timestamp") or []
        quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quotes.get("close") or []
        out: list[Quote] = []
        for stamp, close in zip(timestamps, closes, strict=False):
            if close is None:
                continue  # a day without a trade
            # Dates are the exchange's, not UTC's: a New York close is the same
            # calendar day in New York even when it is already tomorrow in UTC.
            day = datetime.fromtimestamp(int(stamp), tz=zone).date()
            text = str(
                Decimal(repr(float(close))).quantize(quantum, rounding=ROUND_HALF_EVEN)
            )
            rate = Fraction(text)
            if rate <= 0:
                continue
            out.append(
                Quote(
                    base_code=symbol,
                    quote_code=currency,
                    effective_date=day,
                    rate=rate,
                    raw=text,
                )
            )
        # One close per day: Yahoo sometimes repeats the latest day's bar.
        by_day = {q.effective_date: q for q in out}
        return [by_day[d] for d in sorted(by_day)]
