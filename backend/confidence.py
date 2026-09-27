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

# A likely replica's own explanation, and the one every other doubtful
# authenticity read gets. The first is the verdict, which is Pro detail
# (`main._PRO_ONLY_DETAIL_FIELDS`); `confidence_summary` is not — it keeps the
# free tier's locked "Why this price" teaser alive — so a free user's summary
# is written with the second (`summary_sentence(withhold_authenticity=True)`).
# Before the replica read had its own words, all three doubtful reads produced
# the neutral one, and the free summary never carried the verdict.
REPLICA_REASON = "the item may not be authentic"
UNVERIFIED_AUTHENTICITY_REASON = "authenticity could not be verified from the photo"
REPLICA_CODE = "likely_replica"
UNVERIFIED_AUTHENTICITY_CODE = "authenticity_unverified"

#: Every code `compute` can put in `reason_codes`, which the scan response
#: sends as `confidence_reason_codes`. Written out so `contract/` can list them
#: for the client, whose `ConfidenceReason` must know each one; the tests
#: drive every branch of `compute` and require this to be exactly what they
#: produce, so a code added below without being added here fails them.
REASON_CODES = frozenset({
    "brand_identified", "brand_unidentified",
    "range_unusable", "range_tight", "range_very_wide", "range_moderately_wide",
    "range_single_price",
    "photo_soft", "photo_lighting_uneven", "photo_low_resolution", "photo_low_contrast",
    "photo_clear", "photo_limits_confidence",
    "category_established", "category_varied",
    "item_recognised", "item_uncertain", "model_unconfirmed",
    "authenticity_no_concerns", REPLICA_CODE, UNVERIFIED_AUTHENTICITY_CODE,
    "market_read", "market_read_incomplete",
    "analysis_complete", "analysis_partial",
    "estimate_adjusted",
})

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
    # The explanation as a stable token, sent beside it in
    # `confidence_reason_codes` so a client can word it in its own language.
    # One per distinct explanation; the two that name the category share a
    # code per branch, and the client's wording leaves the name out. A code is
    # contract once sent — reword `explanation` freely, never rename a code.
    code: str


@dataclass(frozen=True)
class ConfidenceResult:
    score: int                          # 0–100
    band: str                           # "High" | "Medium" | "Low"
    signals: list[ConfidenceSignal] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)   # one per reason

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


def _range_tightness(low: float, high: float) -> tuple[float, str, str]:
    """Score how actionable the price band is.

    A wide band is the model telling us it does not know, in the one channel it
    cannot fake — you can assert "High confidence" in a string, but a $20–$200
    spread is self-evidently a guess.
    """
    if low <= 0 or high <= 0 or high < low:
        return 0.0, "the price range is not usable", "range_unusable"
    ratio = high / max(low, 0.01)
    if ratio <= RANGE_RATIO_TIGHT:
        return 1.0, "the price range is tight", "range_tight"
    if ratio >= RANGE_RATIO_USELESS:
        return 0.0, "the price range is very wide", "range_very_wide"
    span = RANGE_RATIO_USELESS - RANGE_RATIO_TIGHT
    return (max(0.0, 1.0 - (ratio - RANGE_RATIO_TIGHT) / span),
            "the price range is moderately wide", "range_moderately_wide")


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
        "brand_identified" if brand_known else "brand_unidentified",
    ))

    # ── Price-range tightness ───────────────────────────────────────────────
    # A range the server opened from a single price is ×1.5 by construction,
    # so it always measured "tight" — the one channel the model cannot fake
    # was being faked on its behalf. No credit: it says nothing about how
    # well the item is priced.
    if range_synthesised:
        tightness, tightness_reason, tightness_code = (
            0.0, "the range was estimated from a single price", "range_single_price")
    else:
        tightness, tightness_reason, tightness_code = _range_tightness(value_low, value_high)
    signals.append(ConfidenceSignal("range", tightness, 0.20, tightness_reason, tightness_code))

    # ── Image quality ───────────────────────────────────────────────────────
    if image_quality is not None and image_quality.measured:
        overall = image_quality.overall
        if overall is not None:
            issue_code, issue = (image_quality.coded_issues() or [
                ("photo_clear", "the photo is clear enough to work from")])[0]
            signals.append(ConfidenceSignal("image", overall, 0.18, issue, issue_code))

    # ── Category familiarity ────────────────────────────────────────────────
    cat = (category or "other").strip().lower()
    familiarity = _CATEGORY_FAMILIARITY.get(cat, _UNFAMILIAR)
    signals.append(ConfidenceSignal(
        "category", familiarity, 0.12,
        f"{cat} has a well-established resale market" if familiarity >= 0.65
        else f"{cat} values vary a lot between individual items",
        "category_established" if familiarity >= 0.65 else "category_varied",
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
            id_reason, id_code = "the item was recognised confidently", "item_recognised"
        elif certainty_key == "uncertain":
            id_reason, id_code = ("the item could not be identified with certainty",
                                  "item_uncertain")
        else:
            id_reason, id_code = "the exact model could not be pinned down", "model_unconfirmed"
        signals.append(ConfidenceSignal("identification", certainty, 0.10, id_reason, id_code))

    # ── Authenticity ────────────────────────────────────────────────────────
    auth_key = (authenticity or "").strip().lower()
    auth = _AUTHENTICITY_SCORE.get(auth_key)
    if auth is not None:
        # A likely replica is a finding, not a gap in the evidence, and read
        # the same as "cannot verify" until it had its own words.
        if auth >= 0.9:
            auth_reason, auth_code = "no authenticity concerns", "authenticity_no_concerns"
        elif auth_key == "likely_replica":
            auth_reason, auth_code = REPLICA_REASON, REPLICA_CODE
        else:
            auth_reason, auth_code = UNVERIFIED_AUTHENTICITY_REASON, UNVERIFIED_AUTHENTICITY_CODE
        signals.append(ConfidenceSignal("authenticity", auth, 0.08, auth_reason, auth_code))

    # ── Market signal completeness ──────────────────────────────────────────
    # Whether the model produced usable demand/supply reads at all. Missing them
    # means it had nothing to say about the market, which is itself a signal.
    #
    # The explanation is shown to the user as a reason, so it says only what
    # this measures: that the model answered. Both reads are its guess with no
    # data behind them — SnapWorth has no market data — and "demand and supply
    # are well understood" claimed exactly the knowledge it does not have.
    market_known = sum([
        (demand or "").strip().lower() in _DEMAND_KNOWN,
        (supply or "").strip().lower() in _SUPPLY_KNOWN,
    ]) / 2
    signals.append(ConfidenceSignal(
        "market", market_known, 0.06,
        "the AI gave a read on demand and supply" if market_known == 1.0
        else "the AI's read on demand and supply is incomplete",
        "market_read" if market_known == 1.0 else "market_read_incomplete",
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
            "analysis_complete" if completeness >= 0.85 else "analysis_partial",
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
                    "photo_limits_confidence",
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
            "estimate_adjusted",
        ))

    score = max(0, min(100, score))

    # Surface the weakest contributors — those are what the user can act on.
    shown = sorted((s for s in signals if s.value < 0.6), key=lambda s: s.value)[:3]
    if not shown:
        shown = sorted(signals, key=lambda s: -s.value)[:2]

    return ConfidenceResult(score=score, band=_band(score), signals=signals,
                            reasons=[s.explanation for s in shown],
                            reason_codes=[s.code for s in shown])


def summary_sentence(result: ConfidenceResult, *, withhold_authenticity: bool = False) -> str:
    """One plain-language sentence explaining the score.

    Deliberately not a metric readout: "72 out of 100" tells a reseller nothing
    they can act on, whereas naming the weak signal does.

    `withhold_authenticity` writes a likely replica as "could not be verified"
    — for a free user, who does not get the authenticity read (see
    REPLICA_REASON). The score and band are unchanged: the cap still applies.
    """
    reasons = result.reasons
    if withhold_authenticity:
        reasons = [UNVERIFIED_AUTHENTICITY_REASON if r == REPLICA_REASON else r
                   for r in reasons]
    if not reasons:
        return f"{result.band} confidence."
    joined = reasons[0]
    if len(reasons) > 1:
        joined = ", and ".join([reasons[0], reasons[1]])
    return f"{result.band} confidence — {joined}."
