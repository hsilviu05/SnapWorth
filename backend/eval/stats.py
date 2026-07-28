"""Statistical primitives: bootstrap intervals, paired tests, effect size.

Pure Python, no numpy or scipy. That is a deliberate constraint rather than a
limitation: this code runs in CI on every pull request, and a quality gate that
needs a 60 MB scientific stack to start is a gate people disable.

Everything here is exact or explicitly approximate, and the approximations are
named. Where a normal approximation is used (Wilcoxon at large n) it is stated
in the docstring, because a p-value whose derivation is unclear is worse than
no p-value.

Why paired tests specifically
-----------------------------
Prompt A and prompt B are evaluated on **the same items**. Treating those as two
independent samples throws away the pairing and badly loses power — the
item-to-item variance in resale pricing dwarfs the difference between two
prompts. A paired test asks the right question: on each item, did B beat A?
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass


# ── Bootstrap ────────────────────────────────────────────────────────────────

def bootstrap_ci(
    values: list[float],
    statistic=statistics.median,
    *,
    confidence: float = 0.95,
    iterations: int = 2000,
    seed: int = 20260728,
) -> tuple[float, float] | None:
    """Percentile bootstrap confidence interval.

    Used rather than a closed-form interval because the statistic we care about
    most is the *median* of a skewed distribution, which has no convenient
    parametric interval. The bootstrap makes no distributional assumption, which
    is the right trade for resale price data.

    Deterministic by default: a CI gate that returns a different interval on
    each run produces flaky builds.
    """
    if len(values) < 3:
        return None
    rng = random.Random(seed)
    n = len(values)
    samples: list[float] = []
    for _ in range(iterations):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        try:
            samples.append(float(statistic(resample)))
        except statistics.StatisticsError:
            continue
    if not samples:
        return None
    samples.sort()
    alpha = (1 - confidence) / 2
    low = samples[max(0, int(alpha * len(samples)))]
    high = samples[min(len(samples) - 1, int((1 - alpha) * len(samples)))]
    return low, high


# ── Paired comparison ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ComparisonResult:
    """Outcome of comparing two systems on the same items."""

    n_pairs: int
    mean_delta: float                # positive ⇒ B is worse (errors compared)
    median_delta: float
    wins_b: int                      # items where B had lower error
    wins_a: int
    ties: int
    p_value: float | None
    effect_size: float | None        # Cliff's delta, −1..1
    ci_low: float | None = None
    ci_high: float | None = None
    test_used: str = ""

    @property
    def significant(self) -> bool:
        return self.p_value is not None and self.p_value < 0.05

    @property
    def direction(self) -> str:
        if self.median_delta == 0:
            return "no change"
        return "B better" if self.median_delta < 0 else "A better"

    @property
    def effect_label(self) -> str:
        """Cliff's delta magnitude, using the conventional thresholds.

        Reported alongside the p-value because significance and importance are
        different questions: with 1,000 paired items a trivial difference can be
        highly significant, and shipping on p alone is how teams chase noise.
        """
        if self.effect_size is None:
            return "unknown"
        magnitude = abs(self.effect_size)
        if magnitude < 0.147:
            return "negligible"
        if magnitude < 0.33:
            return "small"
        if magnitude < 0.474:
            return "medium"
        return "large"


def wilcoxon_signed_rank(deltas: list[float]) -> tuple[float, float] | None:
    """Two-sided Wilcoxon signed-rank test. Returns `(statistic, p_value)`.

    Non-parametric and paired — the right test here because per-item error
    distributions are heavily skewed and a t-test's normality assumption does
    not hold for percentage error on resale prices.

    Uses the normal approximation with continuity correction and tie handling.
    That approximation is good for n ≥ 10; below that the p-value is reported
    but should be treated as indicative only, which `ExperimentResult` surfaces
    as an explicit warning rather than hiding.
    """
    non_zero = [d for d in deltas if d != 0]
    n = len(non_zero)
    if n < 6:
        return None

    ranked = sorted(non_zero, key=abs)
    # Average ranks across ties in |delta|.
    ranks: list[float] = [0.0] * n
    index = 0
    while index < n:
        end = index
        while end + 1 < n and abs(ranked[end + 1]) == abs(ranked[index]):
            end += 1
        average = (index + end) / 2 + 1
        for position in range(index, end + 1):
            ranks[position] = average
        index = end + 1

    w_plus = sum(r for value, r in zip(ranked, ranks) if value > 0)
    w_minus = sum(r for value, r in zip(ranked, ranks) if value < 0)
    statistic = min(w_plus, w_minus)

    mean = n * (n + 1) / 4
    variance = n * (n + 1) * (2 * n + 1) / 24
    if variance <= 0:
        return None
    z = (statistic - mean + 0.5) / math.sqrt(variance)   # continuity correction
    p_value = 2 * (1 - _normal_cdf(abs(z)))
    return statistic, max(0.0, min(1.0, p_value))


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def cliffs_delta(a: list[float], b: list[float]) -> float | None:
    """Cliff's delta: P(a > b) − P(a < b), in −1..1.

    Non-parametric effect size. Chosen over Cohen's d because it makes no
    assumption about the shape of the distributions, which matters for the
    long-tailed error distributions this benchmark produces.

    O(n·m); capped to keep CI fast on large sets.
    """
    if not a or not b:
        return None
    limit = 400
    left = a[:limit]
    right = b[:limit]
    greater = sum(1 for x in left for y in right if x > y)
    lesser = sum(1 for x in left for y in right if x < y)
    total = len(left) * len(right)
    return (greater - lesser) / total if total else None


def compare_paired(
    errors_a: list[float],
    errors_b: list[float],
    *,
    seed: int = 20260728,
) -> ComparisonResult | None:
    """Compare two systems' per-item errors on the same items.

    `errors_a[i]` and `errors_b[i]` must describe the same benchmark item.
    Lower is better throughout, so a negative delta means B improved.
    """
    if len(errors_a) != len(errors_b) or not errors_a:
        return None

    deltas = [b - a for a, b in zip(errors_a, errors_b)]
    wins_b = sum(1 for d in deltas if d < 0)
    wins_a = sum(1 for d in deltas if d > 0)
    ties = sum(1 for d in deltas if d == 0)

    test = wilcoxon_signed_rank(deltas)
    p_value = test[1] if test else None
    ci = bootstrap_ci(deltas, statistics.median, seed=seed)

    return ComparisonResult(
        n_pairs=len(deltas),
        mean_delta=sum(deltas) / len(deltas),
        median_delta=statistics.median(deltas),
        wins_b=wins_b, wins_a=wins_a, ties=ties,
        p_value=p_value,
        effect_size=cliffs_delta(errors_b, errors_a),
        ci_low=ci[0] if ci else None,
        ci_high=ci[1] if ci else None,
        test_used="wilcoxon-signed-rank" if test else "insufficient-n",
    )


# ── Sample size ──────────────────────────────────────────────────────────────

def required_sample_size(
    effect: float, *, power: float = 0.8, alpha: float = 0.05
) -> int:
    """Approximate paired-test sample size for a standardised effect.

    Normal-approximation formula. Exists so "we need more data" can be a number
    rather than an opinion — and so a run that is underpowered says so instead
    of reporting a confident null.
    """
    if effect <= 0:
        return 0
    z_alpha = _z_for(1 - alpha / 2)
    z_beta = _z_for(power)
    return max(6, int(math.ceil(((z_alpha + z_beta) / effect) ** 2)))


def _z_for(p: float) -> float:
    """Inverse normal CDF via bisection. Adequate precision, no scipy."""
    low, high = -8.0, 8.0
    for _ in range(200):
        mid = (low + high) / 2
        if _normal_cdf(mid) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2
