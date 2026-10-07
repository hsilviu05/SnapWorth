"""Converting a sale to US dollars for scoring, from a pinned rate table (#225).

The estimates are in USD; a sale in lei or euros has to be converted before
it can be scored, or the error metric measures the exchange rate. The
conversion happens **in scoring, never in the labels**: a gold record keeps
the price and currency it sold in, which `label_fingerprint` hashes, so a
label converted in place would read as drift.

The table is ECB euro reference rates, committed under `data/fx/` and named
by the day it was cut, so a run is reproducible: the same table converts the
same sale to the same dollars, and `run.json` records which table it used.
A sale converts at its `sold_date`, or the nearest earlier business day
within a week. A sale with no date, or one outside the table, is not
converted at all — never at today's rate — and the caller counts it as
excluded with the reason.

Bulgaria adopted the euro on 2026-01-01 and the ECB stopped publishing BGN;
from then on the lev converts at its fixed conversion rate, 1.95583 per euro.

To refresh: download https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip,
keep the columns of `CURRENCIES` from 2024-01-01, and commit the result as
`data/fx/ecb-<last date>.csv` beside the old one; `DEFAULT_TABLE` names the
one runs use. A refresh changes no historical rate the ECB has published, so
scores of existing sales do not move.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "fx"
DEFAULT_TABLE = DATA / "ecb-2026-10-06.csv"

#: How far back a weekend or holiday may look for the last published rate.
MAX_LOOKBACK_DAYS = 7

BGN_FIXED = 1.95583
EURO_ADOPTION = {"BGN": date(2026, 1, 1)}


class FxUnavailable(ValueError):
    """No rate for this currency on or shortly before this day."""


@dataclass(frozen=True)
class RateTable:
    name: str
    sha: str
    #: day → currency → units of that currency per euro
    rates: dict[date, dict[str, float]] = field(repr=False)

    @property
    def version(self) -> str:
        return f"{self.name}@{self.sha}"

    def per_euro(self, currency: str, on: date) -> tuple[float, date]:
        """Units of `currency` per euro on `on`, and the day the rate is from."""
        if currency == "EUR":
            return 1.0, on
        adopted = EURO_ADOPTION.get(currency)
        if adopted and on >= adopted:
            return BGN_FIXED, on
        for back in range(MAX_LOOKBACK_DAYS + 1):
            day = on - timedelta(days=back)
            rate = self.rates.get(day, {}).get(currency)
            if rate:
                return rate, day
        raise FxUnavailable(f"no ECB rate for {currency} within "
                            f"{MAX_LOOKBACK_DAYS} days before {on.isoformat()}")

    def to_usd(self, amount: float, currency: str, on: date | None) -> float:
        """`amount` in `currency` as US dollars on `on`."""
        currency = currency.upper()
        if currency == "USD":
            return amount
        if on is None:
            raise FxUnavailable("no sale date: a sale is never converted at today's rate")
        usd_per_euro, _ = self.per_euro("USD", on)
        local_per_euro, _ = self.per_euro(currency, on)
        return amount * usd_per_euro / local_per_euro


@lru_cache(maxsize=4)
def load(path: str | Path = DEFAULT_TABLE) -> RateTable:
    path = Path(path)
    raw = path.read_bytes()
    rates: dict[date, dict[str, float]] = {}
    for row in csv.DictReader(raw.decode("utf-8").splitlines()):
        day = date.fromisoformat(row.pop("date"))
        rates[day] = {cur: float(v) for cur, v in row.items() if v}
    return RateTable(path.name, hashlib.sha256(raw).hexdigest()[:12], rates)
