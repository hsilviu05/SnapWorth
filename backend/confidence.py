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

import categories
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
# a model-knowledge estimate is more likely to be close. The weights live in
# `categories`, the one table of categories; this is a view of it.
_CATEGORY_FAMILIARITY = {c.name: c.familiarity for c in categories.CATEGORIES}
_UNFAMILIAR = categories.BY_NAME[categories.OTHER].familiarity

_AUTHENTICITY_SCORE = {
    "no_concerns": 1.0,
    "minor_concerns": 0.55,
    "cannot_verify": 0.40,
    "likely_replica": 0.10,
}

# Two reads that cap the score rather than only feeding the average, for the
# same reason a bad photo does: in a weighted sum they were worth 8 and 10
# points of ~105, so a strong brand read and a tight range outvoted them. A
# Louis Vuitton the model marked `likely_replica` scored 88 — "High confidence"
# on the price of a genuine bag, for an item the model itself thinks is fake.
#
# A likely replica caps where a clamped valuation does: the price is for an
# item the photo probably is not. An uncertain identification caps just below
# High: the range may be fine, but "we are not sure what this is" cannot sit
# under a High badge.
REPLICA_CEILING = 30
UNCERTAIN_ID_CEILING = HIGH_THRESHOLD - 1

#: Brand values that mean "no brand was identified". One list, shared with the
#: operator's brand tallies (`notify._clean_brand`) and the eval's
#: hallucination check (`eval.metrics`). The three copies had drifted: notify
#: dropped "Generic", this module scored it as an identified brand, so one
#: wording choice by the model moved the score by about 25 points — "Generic"
#: read 84 High where "Unknown" read 60.
UNKNOWN_BRANDS = frozenset({"", "unknown", "unbranded", "generic", "n/a", "none", "null"})


def brand_is_known(brand: str | None) -> bool:
    """True when `brand` names an actual brand, not a way of saying none."""
    return (brand or "").strip().lower() not in UNKNOWN_BRANDS


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
    range_synthesised: bool = False,
) -> ConfidenceResult:
    """Compute confidence from observable signals.

    Every argument is something we can check independently of the model's
    opinion, except `identification_certainty`, which is included at low weight.

    `range_synthesised` means the model gave one price and the server opened
    it into `value_low`–`value_high` (`valuation.PricePoints.single_price`).
    """
    signals: list[ConfidenceSignal] = []

    # ── Brand identification ────────────────────────────────────────────────
    # The single strongest predictor. Secondhand pricing is brand-anchored: an
    # identified brand collapses the plausible range enormously.
    brand_known = brand_is_known(brand)
    signals.append(ConfidenceSignal(
        "brand", 1.0 if brand_known else 0.0, 0.26,
        "the brand is identified" if brand_known else "the brand could not be identified",
    ))

    # ── Price-range tightness ───────────────────────────────────────────────
    # A range the server opened from a single price is ×1.5 by construction,
    # so it always measured "tight" — the one channel the model cannot fake
    # was being faked on its behalf. No credit: it says nothing about how
    # well the item is priced.
    if range_synthesised:
        tightness, tightness_reason = 0.0, "the range was estimated from a single price"
    else:
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
    familiarity = _CATEGORY_FAMILIARITY.get(cat, _UNFAMILIAR)
    signals.append(ConfidenceSignal(
        "category", familiarity, 0.12,
        f"{cat} has a well-established resale market" if familiarity >= 0.65
        else f"{cat} values vary a lot between individual items",
    ))

    # ── Model's own identification certainty ────────────────────────────────
    # Included, but at low weight: it is self-reported and therefore the least
    # trustworthy input here. It is not zero-information — the model does know
    # whether it recognised something — it just must not dominate.
    certainty_key = (identification_certainty or "").strip().lower()
    certainty = _CERTAINTY_SCORE.get(certainty_key)
    if certainty is not None:
        # "uncertain" is about what the item is, not which model it is, and it
        # now caps the score (UNCERTAIN_ID_CEILING) — so it names that.
        if certainty >= 0.9:
            id_reason = "the item was recognised confidently"
        elif certainty_key == "uncertain":
            id_reason = "the item could not be identified with certainty"
        else:
            id_reason = "the exact model could not be pinned down"
        signals.append(ConfidenceSignal("identification", certainty, 0.10, id_reason))

    # ── Authenticity ────────────────────────────────────────────────────────
    auth_key = (authenticity or "").strip().lower()
    auth = _AUTHENTICITY_SCORE.get(auth_key)
    if auth is not None:
        # A likely replica is a finding, not a gap in the evidence, and read
        # the same as "cannot verify" until it had its own words.
        if auth >= 0.9:
            auth_reason = "no authenticity concerns"
        elif auth_key == "likely_replica":
            auth_reason = "the item may not be authentic"
        else:
            auth_reason = "authenticity could not be verified from the photo"
        signals.append(ConfidenceSignal("authenticity", auth, 0.08, auth_reason))

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

    # ── Identification and authenticity ceilings ────────────────────────────
    # See REPLICA_CEILING. No extra signal is appended: the authenticity or
    # identification signal above already carries the explanation, and at 0.10
    # and 0.15 it is among the weakest, so it is what the summary names.
    if certainty_key == "uncertain":
        score = min(score, UNCERTAIN_ID_CEILING)
    if auth_key == "likely_replica":
        score = min(score, REPLICA_CEILING)

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
