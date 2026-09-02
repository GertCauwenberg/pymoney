"""The European Central Bank's daily reference rates.

Free, no key, exact: the ECB publishes decimal text, so every rate becomes a
:class:`~fractions.Fraction` with nothing lost. The one limitation is that the
ECB quotes everything *against the euro*. For a book kept in EUR that is exactly
the rate wanted; for a book kept in anything else the service stores the
EUR-based price and the valuation engine triangulates through EUR -- which means
the book needs a EUR commodity, and the provider says so if it is missing.

Data comes from the SDMX REST API, series ``EXR.D.<CUR>.EUR.SP00.A`` (daily,
spot, average), in CSV because it is the format least likely to change shape.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from fractions import Fraction

from .provider import Http, Quote, QuoteError, QuoteProvider, QuoteRequest

__all__ = ["EcbProvider"]

BASE_URL = "https://data-api.ecb.europa.eu/service/data/EXR"
EURO = "EUR"


class EcbProvider(QuoteProvider):
    name = "ecb"
    description = "European Central Bank daily reference rates (currencies against EUR)"

    def fetch(self, request: QuoteRequest, http: Http) -> list[Quote]:
        # 1 EUR = x CUR. When the commodity *is* the euro, price it in the
        # book's currency instead: the same series, read the other way round by
        # the valuation engine.
        other = (
            request.currency
            if request.commodity.code.upper() == EURO
            else request.commodity
        )
        code = other.code.upper()
        if code == EURO:
            raise QuoteError("EUR against EUR is not a rate; nothing to fetch")
        if not code.isalpha() or len(code) != 3:
            raise QuoteError(
                f"{other.code!r} is not an ISO 4217 currency code; the ECB only quotes currencies"
            )

        url = (
            f"{BASE_URL}/D.{code}.EUR.SP00.A"
            f"?format=csvdata&startPeriod={request.start.isoformat()}&endPeriod={request.end.isoformat()}"
        )
        body = http(url, {"Accept": "text/csv"})
        return self._parse(body.decode("utf-8", errors="replace"), code)

    @staticmethod
    def _parse(text: str, code: str) -> list[Quote]:
        if not text.strip():
            return []  # the ECB answers an empty body for a range with no data
        rows = list(csv.DictReader(io.StringIO(text)))
        if rows and ("TIME_PERIOD" not in rows[0] or "OBS_VALUE" not in rows[0]):
            raise QuoteError(
                "the ECB's CSV no longer has TIME_PERIOD and OBS_VALUE columns"
            )
        out: list[Quote] = []
        for row in rows:
            value = (row.get("OBS_VALUE") or "").strip()
            when = (row.get("TIME_PERIOD") or "").strip()
            if not value or not when:
                continue  # a holiday: the row is there, the observation is not
            try:
                rate = Fraction(value)
                day = date.fromisoformat(when)
            except (ValueError, ZeroDivisionError) as exc:
                raise QuoteError(
                    f"unreadable ECB observation {when!r}={value!r}"
                ) from exc
            out.append(
                Quote(
                    base_code=EURO,
                    quote_code=code,
                    effective_date=day,
                    rate=rate,
                    raw=value,
                )
            )
        out.sort(key=lambda q: q.effective_date)
        return out
