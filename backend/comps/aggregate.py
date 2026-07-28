"""Robust price statistics over a matched comp set.

Why not the mean
----------------
Resale comp sets are small (5–40), skewed, and reliably contain junk: bundle
sales, data-entry errors, "buy it now $1" relist artefacts, and the occasional
genuinely rare variant. A mean is the wrong estimator for every one of those
properties — one $1,200 outlier in a set of thirty $40 sales moves the mean by
$38 and the median by nothing.

So the pipeline is: **reject outliers → weight what survives → report robust
statistics with an interval.**

Why MAD rather than IQR for rejection
-------------------------------------
IQR fences need a reasonable sample to estimate the quartiles, and at n=6 the
quartiles are themselves noisy. The median absolute deviation has a breakdown
point of 50% — half the data can be arbitrarily corrupt before it fails — which
is the right property when a third of a small comp set might be junk. IQR is
still *reported*, because it is what a user intuitively reads as a range.

Weighting
---------
Every surviving comp carries a weight, the product of four independent factors:

* **freshness** — exponential decay, 45-day half-life. Last week's sale says
  more about today's market than one from ten weeks ago.
* **marketplace reliability** — a StockX authenticated sale is better evidence
  than a Facebook asking price.
* **match quality** — the score from `matching`, so a marginal comp contributes
  proportionally less.
* **condition alignment** — a deadstock comp misprices a worn item.
* **seller reputation** — where available; neutral where not.

`effective_count` is the sum of those weights, not the raw count. Twelve stale,
marginal comps should not carry the authority of twelve fresh, exact ones, and
reporting the raw count would let them.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from comps.models import (
    Comp,
    Condition,
    MARKETPLACE_RELIABILITY,
    Marketplace,
    PriceEvidence,
    ValuationPrices,
)

log = logging.getLogger("snapworth.comps.aggregate")

# Minimum comps before we will claim evidence at all. Below this the median is
# too unstable to present as fact — see CompsStatus.INSUFFICIENT_COMPS.
MIN_COMPS = 5

# Effective count at which comps carry full weight against the model prior.
FULL_WEIGHT_COUNT = 12.0

FRESHNESS_HALF_LIFE_DAYS = 45.0

# Modified z-score threshold for outlier rejection. 3.5 is the conventional
# cutoff for the MAD-based statistic (Iglewicz & Hoaglin).
MAD_THRESHOLD = 3.5

# 0.6745 is the 0.75 quantile of the standard normal — the constant that makes
# MAD a consistent estimator of σ for normally distributed data.
_MAD_SCALE = 0.6745

_CENTS = Decimal("0.01")


def _money(value: Decimal | float) -> Decimal:
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class WeightedComp:
    comp: Comp
    weight: float
    price: Decimal


def freshness_weight(age_days: float) -> float:
    return 0.5 ** (max(0.0, age_days) / FRESHNESS_HALF_LIFE_DAYS)


def seller_weight(comp: Comp) -> float:
    """Reputation factor, neutral when unknown.

    Returning 1.0 for unknown rather than penalising it matters: most providers
    do not expose seller data, and penalising absence would systematically
    down-weight entire marketplaces for a property we simply cannot observe.
    """
    if comp.seller_rating is None:
        return 1.0
    rating = max(0.0, min(1.0, comp.seller_rating))
    # Compress into 0.8–1.05: reputation is a real signal but a weak one next to
    # freshness and match quality, and should never dominate them.
    base = 0.8 + rating * 0.25
    if comp.seller_sales_count is not None and comp.seller_sales_count < 5:
        base *= 0.95        # thin history, rating is not yet meaningful
    return base


def condition_weight(target: Condition, comp: Comp) -> float:
    if comp.condition is None:
        return 0.85
    distance = target.distance(comp.condition)
    return {0: 1.0, 1: 0.75, 2: 0.45}.get(distance, 0.25)


def compute_weight(comp: Comp, target_condition: Condition, now: datetime) -> float:
    reliability = MARKETPLACE_RELIABILITY.get(comp.provider, 0.5)
    match = comp.match_score if comp.match_score > 0 else 0.7
    return (
        freshness_weight(comp.age_days(now))
        * reliability
        * match
        * condition_weight(target_condition, comp)
        * seller_weight(comp)
    )


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def mad(values: list[float], centre: float | None = None) -> float:
    """Median absolute deviation."""
    if not values:
        return 0.0
    mid = centre if centre is not None else _median(values)
    return _median([abs(v - mid) for v in values])


def reject_outliers(values: list[float]) -> tuple[list[float], list[float]]:
    """Split into (kept, rejected) using a MAD-based modified z-score.

    Falls back to keeping everything when MAD is zero — which happens when more
    than half the comps share an identical price. That is a legitimate pattern
    (fixed-price marketplaces like StockX), and rejecting on a zero-scale
    statistic would discard the entire set.
    """
    if len(values) < 4:
        return values, []           # too few to identify an outlier reliably

    centre = _median(values)
    deviation = mad(values, centre)

    kept: list[float] = []
    rejected: list[float] = []

    if deviation > 0:
        for value in values:
            z = _MAD_SCALE * (value - centre) / deviation
            (kept if abs(z) <= MAD_THRESHOLD else rejected).append(value)
    else:
        # MAD is zero when over half the comps share one price — common on
        # fixed-price marketplaces. That does NOT mean there are no outliers;
        # a very tight middle makes a stray value more conspicuous, not less.
        # Fall back to an IQR fence, and finally to exact-equality with the
        # median when the IQR is degenerate too.
        ordered = sorted(values)
        q1 = ordered[len(ordered) // 4]
        q3 = ordered[(3 * len(ordered)) // 4]
        iqr = q3 - q1
        if iqr > 0:
            low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            for value in values:
                (kept if low <= value <= high else rejected).append(value)
        else:
            # Every quartile identical: anything off that value is an outlier.
            for value in values:
                (kept if value == centre else rejected).append(value)

    # Never let rejection gut the sample; if it would, the data is dispersed
    # rather than contaminated and the spread is genuine information.
    if len(kept) < max(3, len(values) // 2):
        return values, []
    return kept, rejected


def trimmed_mean(values: list[float], proportion: float = 0.10) -> float:
    """Mean after discarding `proportion` from each tail."""
    if not values:
        return 0.0
    if len(values) < 5:
        return sum(values) / len(values)
    ordered = sorted(values)
    cut = int(len(ordered) * proportion)
    core = ordered[cut: len(ordered) - cut] or ordered
    return sum(core) / len(core)


def weighted_quantile(pairs: list[tuple[float, float]], q: float) -> float:
    """Weighted quantile over (value, weight) pairs.

    Used rather than a plain quantile so freshness and reliability shape the
    distribution itself, not merely a summary statistic computed from it.
    """
    if not pairs:
        return 0.0
    ordered = sorted(pairs, key=lambda p: p[0])
    total = sum(w for _, w in ordered)
    if total <= 0:
        values = [v for v, _ in ordered]
        idx = min(len(values) - 1, max(0, int(q * len(values))))
        return values[idx]
    target = q * total
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


def _confidence_interval(
    values: list[float], weights: list[float], median: float
) -> tuple[float, float]:
    """Approximate 90% CI for the median.

    Uses the MAD-derived robust standard error rather than a bootstrap: with
    n typically under 40 a bootstrap adds cost and complexity for an interval
    that is dominated by sample size anyway. The 1.253 factor is the asymptotic
    efficiency of the median relative to the mean for normal data.
    """
    n = len(values)
    if n < 3:
        spread = median * 0.35
        return median - spread, median + spread

    deviation = mad(values, median)
    sigma = deviation / _MAD_SCALE if deviation > 0 else statistics.pstdev(values)
    if sigma <= 0:
        return median, median

    effective_n = (sum(weights) ** 2 / sum(w * w for w in weights)) if weights else n
    standard_error = 1.253 * sigma / math.sqrt(max(1.0, effective_n))
    margin = 1.645 * standard_error         # 90% two-sided
    return max(0.0, median - margin), median + margin


def aggregate(
    comps: list[Comp],
    *,
    target_condition: Condition = Condition.GOOD,
    now: datetime | None = None,
) -> PriceEvidence | None:
    """Reduce matched comps to robust statistics. None when below `MIN_COMPS`."""
    if len(comps) < MIN_COMPS:
        return None

    now = now or datetime.now(timezone.utc)
    weighted = [
        WeightedComp(comp=c, weight=compute_weight(c, target_condition, now),
                     price=c.total_price)
        for c in comps
    ]
    weighted = [w for w in weighted if w.price > 0 and w.weight > 0]
    if len(weighted) < MIN_COMPS:
        return None

    raw_values = [float(w.price) for w in weighted]
    kept_values, rejected = reject_outliers(raw_values)
    kept_set = set(kept_values)

    surviving = [w for w in weighted if float(w.price) in kept_set]
    if len(surviving) < 3:
        surviving = weighted            # rejection was too aggressive

    values = [float(w.price) for w in surviving]
    weights = [w.weight for w in surviving]
    pairs = list(zip(values, weights))

    median_value = weighted_quantile(pairs, 0.50)
    p25 = weighted_quantile(pairs, 0.25)
    p75 = weighted_quantile(pairs, 0.75)
    total_weight = sum(weights)
    weighted_mean_value = (
        sum(v * w for v, w in pairs) / total_weight if total_weight else 0.0
    )
    ci_low, ci_high = _confidence_interval(values, weights, median_value)

    sold_dates = [w.comp.sold_at for w in surviving]
    providers = tuple(sorted({w.comp.provider for w in surviving}, key=lambda m: m.value))

    return PriceEvidence(
        count=len(surviving),
        effective_count=round(total_weight, 3),
        median=_money(median_value),
        trimmed_mean=_money(trimmed_mean(values)),
        weighted_mean=_money(weighted_mean_value),
        p25=_money(p25),
        p75=_money(p75),
        iqr=_money(max(0.0, p75 - p25)),
        mad=_money(mad(values, median_value)),
        ci_low=_money(ci_low),
        ci_high=_money(ci_high),
        outliers_removed=len(rejected),
        oldest_sale=min(sold_dates) if sold_dates else None,
        newest_sale=max(sold_dates) if sold_dates else None,
        providers=providers,
    )


def to_prices(evidence: PriceEvidence) -> ValuationPrices:
    """Map statistics onto the four user-facing figures.

    Each answers a genuinely different question a reseller asks:

    * **quick sale** — p25: priced to move within days.
    * **expected** — the weighted median: the most likely actual sale price.
    * **suggested resale** — between median and p75: what to *list* at, leaving
      negotiating room without stalling.
    * **collector** — p75 nudged by dispersion: what the right buyer pays. Only
      meaningfully above suggested when the comps genuinely disagree, which is
      exactly the signature of a variant-sensitive market.
    """
    median = evidence.median
    suggested = median + (evidence.p75 - median) * Decimal("0.5")

    # Dispersion premium: a tight comp set has no collector tail to speak of, so
    # inflating one would invent value the evidence does not support.
    dispersion = Decimal(str(min(0.5, max(0.0, evidence.dispersion))))
    collector = evidence.p75 * (Decimal("1.0") + dispersion * Decimal("0.4"))

    quick = min(evidence.p25, median)
    expected = max(quick, median)
    suggested = max(expected, suggested)
    collector = max(suggested, collector)

    return ValuationPrices(
        quick_sale=_money(quick),
        expected=_money(expected),
        suggested_resale=_money(suggested),
        collector=_money(collector),
    )


def blend_with_prior(
    evidence: PriceEvidence,
    prior_expected: Decimal | None,
) -> tuple[Decimal, float]:
    """Blend the comps median with the model's own estimate.

    Comps do not automatically win. With four comps the median is noisy and the
    model prior — trained on far more data than four sales — is often the better
    estimator. Weight ramps with `effective_count` so evidence takes over only
    once there is enough of it.

    Returns `(blended_price, comps_weight)`.
    """
    comps_weight = min(1.0, evidence.effective_count / FULL_WEIGHT_COUNT)
    if prior_expected is None or prior_expected <= 0:
        return evidence.median, 1.0
    blended = (
        Decimal(str(comps_weight)) * evidence.median
        + Decimal(str(1.0 - comps_weight)) * prior_expected
    )
    return _money(blended), round(comps_weight, 3)
