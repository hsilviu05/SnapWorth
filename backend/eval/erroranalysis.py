"""Automatic failure classification and error reporting.

Why classify rather than just rank by error
-------------------------------------------
A list of the worst predictions tells you *that* something is wrong. It does not
tell you what to fix, and the largest errors are usually the least
representative — one mispriced collectible dominates a report while a systematic
20% under-valuation across 300 clothing items goes unnoticed.

Classification turns a list of failures into a work queue: "brand misread on 34
items, all with no visible tag" is actionable in a way that "item 812 was off by
340%" is not.

The taxonomy is deliberately mechanical
---------------------------------------
Every classifier below is a rule over observable fields, not a model. That is a
constraint, not a shortcut: a learned failure classifier would need its own
labelled data and its own evaluation, and would fail in ways that are harder to
audit than the failures it describes. Rules are inspectable, deterministic, and
can be argued with.

Where a rule cannot decide, it says `UNCLASSIFIED` rather than guessing. The
share of unclassified failures is itself a reported metric — a taxonomy that
explains 40% of failures should not be presented as if it explains them all.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum


class FailureMode(str, Enum):
    # Identification
    WRONG_BRAND = "wrong_brand"
    WRONG_CATEGORY = "wrong_category"
    WRONG_MODEL = "wrong_model"
    WRONG_CONDITION = "wrong_condition"
    ABSTAINED_ON_IDENTIFIABLE = "abstained_on_identifiable"

    # Input quality
    POOR_IMAGE = "poor_image"
    NO_VISIBLE_TAG = "no_visible_tag"
    BACKGROUND_CLUTTER = "background_clutter"

    # Market / pricing
    RARE_COLLECTIBLE = "rare_collectible"
    REGIONAL_PRICING = "regional_pricing"
    INSUFFICIENT_COMPS = "insufficient_comps"
    CONDITION_PREMIUM_MISSED = "condition_premium_missed"
    SYSTEMATIC_OVERVALUATION = "systematic_overvaluation"
    SYSTEMATIC_UNDERVALUATION = "systematic_undervaluation"

    # Model behaviour
    PROMPT_HALLUCINATION = "prompt_hallucination"
    COUNTERFEIT_CONFUSION = "counterfeit_confusion"
    OVERCONFIDENT = "overconfident"
    SCHEMA_VIOLATION = "schema_violation"

    UNCLASSIFIED = "unclassified"

    @property
    def actionable_by(self) -> str:
        """Who can actually fix this class of failure.

        Included because the most common way an error report fails is by
        producing findings nobody owns.
        """
        return {
            "wrong_brand": "prompt / OCR / catalog",
            "wrong_category": "prompt",
            "wrong_model": "prompt / catalog",
            "wrong_condition": "prompt",
            "abstained_on_identifiable": "prompt",
            "poor_image": "client capture UX",
            "no_visible_tag": "client capture UX",
            "background_clutter": "client capture UX",
            "rare_collectible": "comps coverage",
            "regional_pricing": "comps regionalisation",
            "insufficient_comps": "comps coverage",
            "condition_premium_missed": "condition multipliers",
            "systematic_overvaluation": "calibration",
            "systematic_undervaluation": "calibration",
            "prompt_hallucination": "prompt",
            "counterfeit_confusion": "authenticity gating",
            "overconfident": "confidence weights",
            "schema_violation": "generation config",
            "unclassified": "needs manual review",
        }[self.value]


# A prediction is a failure when it misses by more than this. Not arbitrary:
# below ~25% the estimate is still directionally useful for a buy/skip decision,
# which is what the product is actually for.
FAILURE_THRESHOLD_PCT = 25.0

# Beyond this, the estimate is not merely wrong but misleading.
SEVERE_THRESHOLD_PCT = 100.0


@dataclass
class FailureCase:
    """One item that missed, with everything needed to classify it."""

    item_id: str
    category: str
    predicted: float
    actual: float

    # Ground truth
    truth_brand: str | None = None
    truth_model: str | None = None
    truth_condition: str | None = None
    region: str = "US"
    difficulty: str = "typical"
    authenticity: str = "unverified"
    tags: list[str] = field(default_factory=list)

    # What the system produced
    predicted_brand: str | None = None
    predicted_category: str | None = None
    predicted_model: str | None = None
    predicted_condition: str | None = None
    confidence: int = 0
    image_quality: float | None = None
    visual_evidence: list[str] = field(default_factory=list)
    comps_count: int = 0
    valuation_source: str = "model"
    schema_valid: bool = True

    @property
    def error_pct(self) -> float:
        if self.actual == 0:
            return 0.0
        return abs(self.predicted - self.actual) / self.actual * 100

    @property
    def signed_error_pct(self) -> float:
        if self.actual == 0:
            return 0.0
        return (self.predicted - self.actual) / self.actual * 100

    @property
    def is_failure(self) -> bool:
        return self.error_pct > FAILURE_THRESHOLD_PCT

    @property
    def is_severe(self) -> bool:
        return self.error_pct > SEVERE_THRESHOLD_PCT

    @property
    def value_at_risk(self) -> float:
        """Absolute currency error.

        Ranking by this rather than by percentage surfaces the failures that
        actually cost a user money: being 200% wrong on a $4 item matters far
        less than 40% wrong on a $600 one, and a percentage-ranked report buries
        the second behind the first.
        """
        return abs(self.predicted - self.actual)


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


_UNKNOWN = {"", "unknown", "n/a", "none", "null"}


def classify(case: FailureCase) -> list[FailureMode]:
    """Assign every applicable failure mode. Rules only — no inference.

    A case can carry several modes: a blurry photo causing a brand misread is
    genuinely both, and forcing a single label would hide one of them.
    """
    modes: list[FailureMode] = []

    if not case.schema_valid:
        modes.append(FailureMode.SCHEMA_VIOLATION)

    # ── Identification ──────────────────────────────────────────────────────
    predicted_brand, truth_brand = _norm(case.predicted_brand), _norm(case.truth_brand)
    if truth_brand and truth_brand not in _UNKNOWN:
        if predicted_brand in _UNKNOWN:
            # Abstaining is only a failure when the brand *was* knowable.
            if case.difficulty in {"easy", "typical"}:
                modes.append(FailureMode.ABSTAINED_ON_IDENTIFIABLE)
        elif predicted_brand != truth_brand:
            modes.append(FailureMode.WRONG_BRAND)

    if (_norm(case.predicted_category) and _norm(case.category)
            and _norm(case.predicted_category) != _norm(case.category)):
        modes.append(FailureMode.WRONG_CATEGORY)

    predicted_model, truth_model = _norm(case.predicted_model), _norm(case.truth_model)
    if truth_model and predicted_model and predicted_model not in _UNKNOWN:
        if predicted_model != truth_model:
            modes.append(FailureMode.WRONG_MODEL)

    if (_norm(case.predicted_condition) and _norm(case.truth_condition)
            and _norm(case.predicted_condition) != _norm(case.truth_condition)):
        modes.append(FailureMode.WRONG_CONDITION)

    # ── Input quality ───────────────────────────────────────────────────────
    if case.image_quality is not None and case.image_quality < 0.4:
        modes.append(FailureMode.POOR_IMAGE)
    if "no_tag" in case.tags:
        modes.append(FailureMode.NO_VISIBLE_TAG)
    if "cluttered" in case.tags or "background_clutter" in case.tags:
        modes.append(FailureMode.BACKGROUND_CLUTTER)

    # ── Market ──────────────────────────────────────────────────────────────
    if case.category in {"collectibles", "toys"} and case.is_severe:
        modes.append(FailureMode.RARE_COLLECTIBLE)
    if case.region != "US" and abs(case.signed_error_pct) > FAILURE_THRESHOLD_PCT:
        # US-sourced comps mispricing a non-US sale is a distinct, fixable
        # problem from the model simply being wrong.
        modes.append(FailureMode.REGIONAL_PRICING)
    if case.valuation_source == "comps" and case.comps_count < 5:
        modes.append(FailureMode.INSUFFICIENT_COMPS)

    # ── Model behaviour ─────────────────────────────────────────────────────
    if predicted_model and predicted_model not in _UNKNOWN and not case.visual_evidence:
        # A specific model asserted with nothing in frame to support it.
        modes.append(FailureMode.PROMPT_HALLUCINATION)
    if (case.authenticity == "verified_replica"
            and case.signed_error_pct > FAILURE_THRESHOLD_PCT):
        modes.append(FailureMode.COUNTERFEIT_CONFUSION)
    if case.confidence >= 70 and case.is_failure:
        modes.append(FailureMode.OVERCONFIDENT)

    return modes or [FailureMode.UNCLASSIFIED]


@dataclass(frozen=True)
class ModeSummary:
    mode: FailureMode
    count: int
    share: float
    median_error_pct: float
    total_value_at_risk: float
    example_ids: tuple[str, ...]

    @property
    def owner(self) -> str:
        return self.mode.actionable_by


@dataclass
class ErrorReport:
    total_evaluated: int
    total_failures: int
    severe_failures: int
    failure_rate: float
    unclassified_share: float
    by_mode: list[ModeSummary]
    worst_by_percentage: list[FailureCase]
    worst_by_value: list[FailureCase]
    by_category: dict[str, dict]
    systematic_bias: dict[str, float]

    def render(self, limit: int = 10) -> str:
        lines = [
            "",
            "═══ error analysis ═══",
            f"evaluated {self.total_evaluated} · failures {self.total_failures} "
            f"({self.failure_rate:.1%}) · severe {self.severe_failures}",
            f"unclassified {self.unclassified_share:.1%} "
            f"— rules explain {1 - self.unclassified_share:.0%} of failures",
            "",
            "Most common failure modes",
        ]
        for summary in self.by_mode[:limit]:
            lines.append(
                f"  {summary.count:>4}  {summary.share:>5.1%}  "
                f"{summary.mode.value:<30} median {summary.median_error_pct:>6.1f}%  "
                f"→ {summary.owner}")

        lines += ["", "Highest value at risk (absolute currency error)"]
        for case in self.worst_by_value[:limit]:
            lines.append(
                f"  {case.item_id:<20} ${case.value_at_risk:>9.2f}  "
                f"predicted ${case.predicted:.0f} vs ${case.actual:.0f}  "
                f"({case.category})")

        lines += ["", "Largest percentage errors"]
        for case in self.worst_by_percentage[:limit]:
            lines.append(
                f"  {case.item_id:<20} {case.error_pct:>8.1f}%  "
                f"predicted ${case.predicted:.0f} vs ${case.actual:.0f}")

        if self.systematic_bias:
            lines += ["", "Systematic bias by category (median signed error)"]
            for category, value in sorted(
                    self.systematic_bias.items(), key=lambda kv: -abs(kv[1])):
                direction = "over" if value > 0 else "under"
                lines.append(f"  {category:<16} {value:+7.1f}%  ({direction}-valuing)")
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict:
        return {
            "total_evaluated": self.total_evaluated,
            "total_failures": self.total_failures,
            "severe_failures": self.severe_failures,
            "failure_rate": self.failure_rate,
            "unclassified_share": self.unclassified_share,
            "by_mode": [
                {"mode": s.mode.value, "count": s.count, "share": s.share,
                 "median_error_pct": s.median_error_pct,
                 "total_value_at_risk": s.total_value_at_risk,
                 "owner": s.owner, "examples": list(s.example_ids)}
                for s in self.by_mode
            ],
            "by_category": self.by_category,
            "systematic_bias": self.systematic_bias,
        }


def analyse(cases: list[FailureCase], *, top_n: int = 20) -> ErrorReport:
    """Classify every case and build a prioritised report."""
    if not cases:
        return ErrorReport(0, 0, 0, 0.0, 0.0, [], [], [], {}, {})

    failures = [c for c in cases if c.is_failure]
    severe = [c for c in failures if c.is_severe]

    mode_cases: dict[FailureMode, list[FailureCase]] = {}
    for case in failures:
        for mode in classify(case):
            mode_cases.setdefault(mode, []).append(case)

    summaries = [
        ModeSummary(
            mode=mode,
            count=len(group),
            share=len(group) / len(failures),
            median_error_pct=statistics.median([c.error_pct for c in group]),
            total_value_at_risk=sum(c.value_at_risk for c in group),
            example_ids=tuple(c.item_id for c in group[:3]),
        )
        for mode, group in mode_cases.items()
    ]
    summaries.sort(key=lambda s: -s.count)

    unclassified = mode_cases.get(FailureMode.UNCLASSIFIED, [])
    unclassified_share = len(unclassified) / len(failures) if failures else 0.0

    by_category: dict[str, dict] = {}
    systematic_bias: dict[str, float] = {}
    for category in sorted({c.category for c in cases}):
        subset = [c for c in cases if c.category == category]
        subset_failures = [c for c in subset if c.is_failure]
        by_category[category] = {
            "n": len(subset),
            "failures": len(subset_failures),
            "failure_rate": len(subset_failures) / len(subset) if subset else 0.0,
            "median_error_pct": statistics.median([c.error_pct for c in subset]),
        }
        signed = [c.signed_error_pct for c in subset if c.actual > 0]
        if len(signed) >= 3:
            median_signed = statistics.median(signed)
            # Only report as systematic when the whole category leans one way;
            # a median near zero with high spread is noise, not bias.
            if abs(median_signed) > 10:
                systematic_bias[category] = round(median_signed, 2)

    return ErrorReport(
        total_evaluated=len(cases),
        total_failures=len(failures),
        severe_failures=len(severe),
        failure_rate=len(failures) / len(cases),
        unclassified_share=unclassified_share,
        by_mode=summaries,
        worst_by_percentage=sorted(failures, key=lambda c: -c.error_pct)[:top_n],
        worst_by_value=sorted(failures, key=lambda c: -c.value_at_risk)[:top_n],
        by_category=by_category,
        systematic_bias=systematic_bias,
    )


def recurring_patterns(cases: list[FailureCase], *, min_support: int = 3) -> list[dict]:
    """Find co-occurring (mode, attribute) combinations.

    Single failure modes are useful; combinations are what point at a root
    cause. "wrong_brand on items tagged no_tag" is a specific, fixable problem,
    whereas "wrong_brand" alone could be a dozen different things.
    """
    failures = [c for c in cases if c.is_failure]
    combos: dict[tuple[str, str], list[FailureCase]] = {}
    for case in failures:
        attributes = [("category", case.category), ("difficulty", case.difficulty),
                      ("region", case.region)]
        attributes += [("tag", tag) for tag in case.tags]
        for mode in classify(case):
            for attr_name, attr_value in attributes:
                combos.setdefault((mode.value, f"{attr_name}={attr_value}"), []).append(case)

    patterns = [
        {
            "mode": mode,
            "attribute": attribute,
            "count": len(group),
            "median_error_pct": round(statistics.median(
                [c.error_pct for c in group]), 2),
            "value_at_risk": round(sum(c.value_at_risk for c in group), 2),
        }
        for (mode, attribute), group in combos.items()
        if len(group) >= min_support
    ]
    patterns.sort(key=lambda p: (-p["count"], -p["value_at_risk"]))
    return patterns
