"""Valuation quality metrics.

Why these, specifically
----------------------
The obvious metric — MAPE — is the wrong headline for resale pricing. Percentage
error is unbounded above and bounded below by -100%, so a single item predicted
at $200 that sold for $5 contributes +3900% and swamps a hundred good
predictions. Thrift data has exactly that shape: mostly $5-$60 items with a long
tail of genuine finds.

So the headline is **MdAPE** (median), which is robust to that tail, with MAPE
reported alongside to expose it rather than hide it.

Beyond point accuracy, three things matter more for trust than the number itself:

* **Coverage** — how often the true price falls inside the predicted range. A
  range that never contains the answer is worse than useless because it looks
  authoritative. Target ~80% at the stated interval.

* **Calibration** — whether "High confidence" is actually more accurate than
  "Low confidence". If it isn't, the confidence score is decoration. Measured as
  Expected Calibration Error over confidence buckets.

* **Consistency** — how much the price moves across repeat runs on the *same*
  image. This is what the v1 temperature-1.0 default destroyed, and it is
  directly observable without any ground truth at all.

Every function here is pure and takes plain numbers, so the harness can be
tested without a model, an API key, or a network.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field


def ape(predicted: float, actual: float) -> float | None:
    """Absolute percentage error. None when `actual` is 0 (undefined)."""
    if actual == 0:
        return None
    return abs(predicted - actual) / abs(actual) * 100.0


def mdape(pairs: list[tuple[float, float]]) -> float | None:
    """Median absolute percentage error — the headline accuracy metric."""
    errors = [e for p, a in pairs if (e := ape(p, a)) is not None]
    return statistics.median(errors) if errors else None


def mape(pairs: list[tuple[float, float]]) -> float | None:
    """Mean APE. Reported alongside MdAPE to expose tail behaviour."""
    errors = [e for p, a in pairs if (e := ape(p, a)) is not None]
    return sum(errors) / len(errors) if errors else None


def within_tolerance(pairs: list[tuple[float, float]], tolerance: float = 25.0) -> float | None:
    """Fraction of predictions within `tolerance` percent.

    More legible to a product decision than MdAPE: "68% of estimates land within
    25%" is something you can put in front of a user.
    """
    errors = [e for p, a in pairs if (e := ape(p, a)) is not None]
    if not errors:
        return None
    return sum(1 for e in errors if e <= tolerance) / len(errors)


def range_coverage(triples: list[tuple[float, float, float]]) -> float | None:
    """Fraction of actual prices falling within [low, high].

    A range nobody's true price lands in is actively harmful — it reads as
    precision while being wrong.
    """
    valid = [(lo, hi, a) for lo, hi, a in triples if hi >= lo]
    if not valid:
        return None
    return sum(1 for lo, hi, a in valid if lo <= a <= hi) / len(valid)


def mean_range_width(triples: list[tuple[float, float, float]]) -> float | None:
    """Mean high/low ratio. Coverage is trivial to achieve with a wide enough
    band, so it is only meaningful read together with this."""
    ratios = [hi / lo for lo, hi, _ in triples if lo > 0 and hi >= lo]
    return sum(ratios) / len(ratios) if ratios else None


@dataclass
class CalibrationBucket:
    lower: int
    upper: int
    count: int = 0
    hits: int = 0                      # predictions within tolerance

    @property
    def accuracy(self) -> float:
        return self.hits / self.count if self.count else 0.0

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2 / 100.0


@dataclass
class Calibration:
    buckets: list[CalibrationBucket] = field(default_factory=list)
    ece: float = 0.0                   # expected calibration error, 0 = perfect

    def as_table(self) -> list[dict]:
        return [
            {"range": f"{b.lower}-{b.upper}", "n": b.count,
             "predicted": round(b.midpoint, 3), "actual": round(b.accuracy, 3)}
            for b in self.buckets if b.count
        ]


def calibration(
    scored: list[tuple[int, float, float]], tolerance: float = 25.0, bucket_size: int = 20
) -> Calibration:
    """Bucket predictions by confidence and compare claimed vs. actual accuracy.

    `scored` is (confidence_score, predicted, actual).

    A well-calibrated system where confidence is 80 should be right ~80% of the
    time. ECE is the count-weighted mean gap between the two, so 0 is perfect
    and larger is worse. This is the number that tells you whether the
    confidence score means anything at all.
    """
    buckets = [
        CalibrationBucket(lower=i, upper=min(i + bucket_size, 100))
        for i in range(0, 100, bucket_size)
    ]
    total = 0
    for score, predicted, actual in scored:
        err = ape(predicted, actual)
        if err is None:
            continue
        idx = min(int(score // bucket_size), len(buckets) - 1)
        buckets[idx].count += 1
        buckets[idx].hits += 1 if err <= tolerance else 0
        total += 1

    if not total:
        return Calibration(buckets=buckets, ece=0.0)

    ece = sum(
        (b.count / total) * abs(b.midpoint - b.accuracy) for b in buckets if b.count
    )
    return Calibration(buckets=buckets, ece=ece)


def consistency(runs: list[list[float]]) -> dict:
    """Price stability across repeat runs of the same item.

    `runs` is one list of prices per item. Needs no ground truth, which makes it
    the cheapest useful signal available — and it is exactly what a default
    temperature of 1.0 destroyed.

    Returns the mean and worst coefficient of variation (stddev / mean); lower
    is better. Above ~0.15 means a user rescanning the same jumper sees a
    visibly different price, which reads as the product being broken.
    """
    cvs: list[float] = []
    for prices in runs:
        clean = [p for p in prices if p > 0]
        if len(clean) < 2:
            continue
        mean = sum(clean) / len(clean)
        if mean <= 0:
            continue
        cvs.append(statistics.pstdev(clean) / mean)
    if not cvs:
        return {"mean_cv": None, "worst_cv": None, "n": 0}
    return {"mean_cv": sum(cvs) / len(cvs), "worst_cv": max(cvs), "n": len(cvs)}


def hallucination_rate(records: list[dict]) -> dict:
    """Fraction of responses asserting specifics the photo could not support.

    Three checkable proxies for fabricated specificity — the dominant
    hallucination mode here, because specific answers read as competent:

    * `unsupported_model` — a model name returned with no visual evidence at all.
    * `brand_mismatch` — a brand asserted that contradicts the labelled truth.
    * `evidence_missing` — identification claimed as `certain` with no evidence.

    These are heuristics, not ground truth. They are designed to move in the
    right direction when the prompt improves, not to be an absolute measure.
    """
    if not records:
        return {"rate": None, "n": 0}

    flagged = 0
    reasons: dict[str, int] = {}
    for rec in records:
        issues = []
        evidence = rec.get("visual_evidence") or []
        if rec.get("model_name") and not evidence:
            issues.append("unsupported_model")
        truth_brand = (rec.get("expected_brand") or "").strip().lower()
        got_brand = (rec.get("brand") or "").strip().lower()
        if truth_brand and got_brand not in {"", "unknown"} and got_brand != truth_brand:
            issues.append("brand_mismatch")
        if rec.get("identification_certainty") == "certain" and not evidence:
            issues.append("evidence_missing")
        if issues:
            flagged += 1
            for issue in issues:
                reasons[issue] = reasons.get(issue, 0) + 1

    return {"rate": flagged / len(records), "n": len(records), "reasons": reasons}


def latency_summary(samples_ms: list[float]) -> dict:
    """p50/p95/p99. Users abandon on p95, not on the mean."""
    if not samples_ms:
        return {"p50": None, "p95": None, "p99": None, "n": 0}
    ordered = sorted(samples_ms)

    def pct(p: float) -> float:
        idx = min(len(ordered) - 1, max(0, math.ceil(p / 100 * len(ordered)) - 1))
        return ordered[idx]

    return {"p50": pct(50), "p95": pct(95), "p99": pct(99), "n": len(ordered)}
