"""Today's ECB reference rates as US-dollar multipliers, for the app's
"≈ 185 lei, converted from US prices" line under an estimate (1.8.0).

Estimates are US resale prices and stay in dollars. This only lets the app
show the same number in the phone's currency, labelled as a conversion: it
is not a local market price, and nothing here pretends it is.

The source is the ECB's daily reference feed, the same publisher as the
eval's pinned table (`eval/fx.py`). It is parsed with a pattern, not an XML
parser, so nothing in the document can reach an entity resolver. Kept in
process for `TTL` seconds; a failed fetch keeps serving the last good table
for up to `STALE_AFTER`, then serves nothing, and the app shows no line.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable

import httpx

log = logging.getLogger("snapworth.fxlive")

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

#: `SaleCurrency.all` in the app, less USD itself.
CURRENCIES: tuple[str, ...] = ("EUR", "GBP", "RON", "PLN", "CHF", "CAD", "AUD", "CNY",
                               "MXN", "SEK", "DKK", "NOK", "CZK", "HUF", "BGN")

#: The lev's fixed rate since Bulgaria joined the euro (2026-01-01), when the
#: ECB stopped publishing it. See `eval/fx.py`.
BGN_PER_EUR = 1.95583

TTL = 6 * 3600
STALE_AFTER = 4 * 24 * 3600

_RATE = re.compile(r"currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([0-9]+(?:\.[0-9]+)?)['\"]")
_TIME = re.compile(r"time=['\"](\d{4}-\d{2}-\d{2})['\"]")

_table: dict | None = None
_fetched_at = 0.0
_good_at = 0.0


def parse(xml: str) -> dict | None:
    """`{"date": "2026-11-05", "rates": {"EUR": 0.93, ...}}`: units of each
    currency per US dollar. None when the feed has no dollar rate or no date."""
    per_eur = {code: float(rate) for code, rate in _RATE.findall(xml)}
    day = _TIME.search(xml)
    usd = per_eur.get("USD")
    if not day or not usd or usd <= 0:
        return None
    per_eur["EUR"] = 1.0
    per_eur.setdefault("BGN", BGN_PER_EUR)
    rates = {code: round(per_eur[code] / usd, 6)
             for code in CURRENCIES if per_eur.get(code, 0) > 0}
    return {"date": day.group(1), "rates": rates}


async def _fetch() -> str:
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(ECB_DAILY_URL)
        r.raise_for_status()
        return r.text


async def rates(fetch: Callable[[], Awaitable[str]] = _fetch,
                now: Callable[[], float] = time.time) -> dict | None:
    """The current table, fetching when it is older than `TTL`."""
    global _table, _fetched_at, _good_at
    t = now()
    if _table is not None and t - _fetched_at < TTL:
        return _table
    _fetched_at = t
    try:
        table = parse(await fetch())
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("ecb rates fetch failed: %s", type(exc).__name__)
        table = None
    if table is not None:
        _table, _good_at = table, t
    elif _table is not None and t - _good_at > STALE_AFTER:
        _table = None
    return _table


def reset() -> None:
    """For tests."""
    global _table, _fetched_at, _good_at
    _table, _fetched_at, _good_at = None, 0.0, 0.0
