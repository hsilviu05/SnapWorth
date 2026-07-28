"""Computed confidence for a valuation.

The problem this replaces
------------------------
v1 asked the model to rate its own certainty and rendered the answer next to a
checkmark. That is not a measurement. Language models are systematically
overconfident about their own outputs, and the failure mode is precisely the
dangerous one: a soft photo of an unidentifiable jumper returning "High".

Worse, it was uncorrelated with the thing users care about. A user does not want
to know how fluent the model felt. They want to know *how likely this number is
to be right*, which depends on whether the brand was legible, whether the price
band is tight enough to act on, and whether the photo carried enough information
to identify anything at all.

The model here
--------------
Confidence is a weighted sum of independently observable signals, each in 0–1.
None of them is the model's opinion of itself, with one deliberate exception
(`identification_certainty`) which is included at low weight because the model
genuinely does know something about whether it recognised the item — it just
should not be the whole answer.

    score = Σ(signal × weight) / Σ(weight of available signals)

Signals that cannot be measured are *dropped from the denominator* rather than
scored zero. An unmeasurable signal is not a bad signal, and treating it as one
would penalise every HEIC upload on a server without the plugin.

Calibration
-----------
The weights below are a considered prior, not a fitted model — there is no
labelled outcome data yet. `backend/eval/` measures calibration (predicted
confidence vs. actual hit rate) so these become empirical rather than assumed.
Until that runs against a real dataset, treat the absolute numbers as ordinal:
the ranking is meaningful, the exact value is not yet.

Deliberately conservative: the cost of overstating confidence (a user buys a
$40 item that resells for $12) is much higher than understating it (a user
double-checks a good estimate).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from imagequality import ImageQuality

log = logging.getLogger("snapworth.confidence")

# Band thresholds. `confidence` is still emitted as High/Medium/Low for existing
# clients; the numeric score is the new, additive field.
HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 45

# Ratio of high/low beyond which a range is too wide to act on. A $20–$200
# estimate (10×) is not an estimate, it is a shrug.
RANGE_RATIO_TIGHT = 1.8
RANGE_RATIO_USELESS = 6.0

_CERTAINTY_SCORE = {"certain": 1.0, "probable": 0.6, "uncertain": 0.15}
_DEMAND_KNOWN = {"high", "medium", "low"}
_SUPPLY_KNOWN = {"scarce", "moderate", "abundant"}

# Categories whose secondhand markets are dense, well-documented and stable, so
# a model-knowledge estimate is more likely to be close. Fine art or antiques
# vary enormously by individual piece; a Nike sneaker does not.
_CATEGORY_FAMILIARITY = {
    "clothing": 0.90,
    "shoes": 0.90,
    "electronics": 0.80,
    "accessories": 0.70,
    "sports": 0.70,
    "home": 0.65,
    "books": 0.65,
    "toys": 0.60,
    "furniture": 0.50,
    "collectibles": 0.35,   # value is dominated by rarity we cannot see
    "other": 0.30,
}

_AUTHENTICITY_SCORE = {
    "no_concerns": 1.0,
    "minor_concerns": 0.55,
    "cannot_verify": 0.40,
    "likely_replica": 0.10,
}


@dataclass(frozen=True)
class ConfidenceSignal:
    name: str
    value: float            # 0–1
    weight: float
    explanation: str


@dataclass(frozen=True)
class ConfidenceResult:
    score: int                          # 0–100
    band: str                           # "High" | "Medium" | "Low"
    signals: list[ConfidenceSignal] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def as_legacy(self) -> str:
        """The High/Medium/Low string existing clients decode."""
        return self.band


def _band(score: int) -> str:
    if score >= HIGH_THRESHOLD:
        return "High"
    if score >= MEDIUM_THRESHOLD:
        return "Medium"
    return "Low"


def _range_tightness(low: float, high: float) -> tuple[float, str]:
    """Score how actionable the price band is.

    A wide band is the model telling us it does not know, in the one channel it
    cannot fake — you can assert "High confidence" in a string, but a $20–$200
    spread is self-evidently a guess.
    """
    if low <= 0 or high <= 0 or high < low:
        return 0.0, "the price range is not usable"
    ratio = high / max(low, 0.01)
    if ratio <= RANGE_RATIO_TIGHT:
        return 1.0, "the price range is tight"
    if ratio >= RANGE_RATIO_USELESS:
        return 0.0, "the price range is very wide"
    span = RANGE_RATIO_USELESS - RANGE_RATIO_TIGHT
    return max(0.0, 1.0 - (ratio - RANGE_RATIO_TIGHT) / span), "the price range is moderately wide"


def compute(
    *,
    brand: str | None,
    category: str | None,
    identification_certainty: str | None,
    authenticity: str | None,
    demand: str | None,
    supply: str | None,
    value_low: float,
    value_high: float,
    image_quality: ImageQuality | None = None,
    was_clamped: bool = False,
    model_field_count: int = 0,
    expected_field_count: int = 0,
) -> ConfidenceResult:
    """Compute confidence from observable signals.

    Every argument is something we can check independently of the model's
    opinion, except `identification_certainty`, which is included at low weight.
    """
    signals: list[ConfidenceSignal] = []

    # ── Brand identification ────────────────────────────────────────────────
    # The single strongest predictor. Secondhand pricing is brand-anchored: an
    # identified brand collapses the plausible range enormously.
    brand_clean = (brand or "").strip().lower()
    brand_known = bool(brand_clean) and brand_clean not in {"unknown", "unbranded", "n/a", "none", "null"}
    signals.append(ConfidenceSignal(
        "brand", 1.0 if brand_known else 0.0, 0.26,
        "the brand is identified" if brand_known else "the brand could not be identified",
    ))

    # ── Price-range tightness ───────────────────────────────────────────────
    tightness, tightness_reason = _range_tightness(value_low, value_high)
    signals.append(ConfidenceSignal("range", tightness, 0.20, tightness_reason))

    # ── Image quality ───────────────────────────────────────────────────────
    if image_quality is not None and image_quality.measured:
        overall = image_quality.overall
        if overall is not None:
            issues = image_quality.issues()
            signals.append(ConfidenceSignal(
                "image", overall, 0.18,
                issues[0] if issues else "the photo is clear enough to work from",
            ))

    # ── Category familiarity ────────────────────────────────────────────────
    cat = (category or "other").strip().lower()
    familiarity = _CATEGORY_FAMILIARITY.get(cat, 0.30)
    signals.append(ConfidenceSignal(
        "category", familiarity, 0.12,
        f"{cat} has a well-established resale market" if familiarity >= 0.65
        else f"{cat} values vary a lot between individual items",
    ))

    # ── Model's own identification certainty ────────────────────────────────
    # Included, but at low weight: it is self-reported and therefore the least
    # trustworthy input here. It is not zero-information — the model does know
    # whether it recognised something — it just must not dominate.
    certainty = _CERTAINTY_SCORE.get((identification_certainty or "").strip().lower())
    if certainty is not None:
        signals.append(ConfidenceSignal(
            "identification", certainty, 0.10,
            "the item was recognised confidently" if certainty >= 0.9
            else "the exact model could not be pinned down",
        ))

    # ── Authenticity ────────────────────────────────────────────────────────
    auth = _AUTHENTICITY_SCORE.get((authenticity or "").strip().lower())
    if auth is not None:
        signals.append(ConfidenceSignal(
            "authenticity", auth, 0.08,
            "no authenticity concerns" if auth >= 0.9
            else "authenticity could not be verified from the photo",
        ))

    # ── Market signal completeness ──────────────────────────────────────────
    # Whether the model produced usable demand/supply reads at all. Missing them
    # means it had nothing to say about the market, which is itself a signal.
    market_known = sum([
        (demand or "").strip().lower() in _DEMAND_KNOWN,
        (supply or "").strip().lower() in _SUPPLY_KNOWN,
    ]) / 2
    signals.append(ConfidenceSignal(
        "market", market_known, 0.06,
        "demand and supply are well understood" if market_known == 1.0
        else "limited read on current demand",
    ))

    # ── Response completeness ───────────────────────────────────────────────
    # A response missing many optional fields suggests the model struggled or
    # was truncated. Only scored when the caller tells us what to expect.
    if expected_field_count > 0:
        completeness = min(1.0, model_field_count / expected_field_count)
        signals.append(ConfidenceSignal(
            "completeness", completeness, 0.05,
            "the analysis is complete" if completeness >= 0.85
            else "the analysis came back partial",
        ))

    total_weight = sum(s.weight for s in signals)
    raw = sum(s.value * s.weight for s in signals) / total_weight if total_weight else 0.0
    score = int(round(raw * 100))

    # ── Image-quality ceiling ───────────────────────────────────────────────
    # A weighted sum alone lets a strong brand read plus a tight range outvote a
    # catastrophically bad photo — which is wrong, because *every* downstream
    # claim was derived from that photo. A brand "identified" from an unreadable
    # image is not corroborating evidence; it is the same guess counted twice.
    #
    # So severe degradation caps the score rather than merely contributing to
    # it. This also keeps the band coherent with its own explanation: we must
    # never render "High confidence — the photo is out of focus".
    if image_quality is not None and image_quality.measured:
        overall = image_quality.overall
        if overall is not None and overall < 0.5:
            # Linear ceiling: 0.0 quality caps at 25, 0.5 caps at 69 (just below
            # the High threshold), above 0.5 no ceiling applies.
            ceiling = int(round(25 + (overall / 0.5) * 44))
            if score > ceiling:
                score = ceiling
                signals.append(ConfidenceSignal(
                    "image_ceiling", overall, 0.0,
                    "the photo quality limits how confident this estimate can be",
                ))

    # ── Hard override ───────────────────────────────────────────────────────
    # Clamping means the model produced a number outside the plausible band for
    # its own category — an order-of-magnitude error or an injected value. That
    # invalidates the estimate regardless of how every other signal scored, so it
    # caps rather than merely contributing.
    if was_clamped:
        score = min(score, 30)
        signals.append(ConfidenceSignal(
            "clamped", 0.0, 0.0,
            "the estimate was outside the plausible range for this category and was adjusted",
        ))

    score = max(0, min(100, score))

    # Surface the weakest contributors — those are what the user can act on.
    weak = sorted((s for s in signals if s.value < 0.6), key=lambda s: s.value)
    reasons = [s.explanation for s in weak[:3]]
    if not reasons:
        strongest = sorted(signals, key=lambda s: -s.value)[:2]
        reasons = [s.explanation for s in strongest]

    return ConfidenceResult(score=score, band=_band(score), signals=signals, reasons=reasons)


def summary_sentence(result: ConfidenceResult) -> str:
    """One plain-language sentence explaining the score.

    Deliberately not a metric readout: "72 out of 100" tells a reseller nothing
    they can act on, whereas naming the weak signal does.
    """
    if not result.reasons:
        return f"{result.band} confidence."
    joined = result.reasons[0]
    if len(result.reasons) > 1:
        joined = ", and ".join([result.reasons[0], result.reasons[1]])
    return f"{result.band} confidence — {joined}."
