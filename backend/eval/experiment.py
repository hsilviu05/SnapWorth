"""A/B experiment framework for prompts, models and algorithms.

The rule this enforces
----------------------
A change ships when it improves a metric on the gold set, and is rejected
otherwise. Not "looks better in a few spot checks" — those are how prompt
engineering becomes superstition, because a language model will always produce
a convincing-looking answer for whichever example you happen to try.

Design
------
An experiment declares **one primary metric** before it runs. Everything else is
a guardrail. That ordering matters: with twenty secondary metrics, at α=0.05 you
expect one false positive per run, and a framework that lets you pick the winner
afterwards is a machine for manufacturing improvements that do not exist.

Comparisons are **paired** — both arms evaluated on the same items, compared
per-item. Item-to-item variance in resale pricing dwarfs the difference between
two prompts, so unpaired analysis would need a far larger set to detect the same
effect.

Guardrails are checked independently of the primary metric. A prompt that
improves accuracy while doubling latency or hallucination rate is not a win, and
this framework will say so rather than reporting the headline in isolation.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from eval import metrics as metrics_module
from eval import stats
from eval.provenance import Metric, MetricSet, Provenance

log = logging.getLogger("snapworth.eval.experiment")


class Verdict(str, Enum):
    SHIP = "ship"
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"
    BLOCKED_BY_GUARDRAIL = "blocked_by_guardrail"

    @property
    def symbol(self) -> str:
        return {"ship": "✅", "reject": "❌",
                "inconclusive": "⚖️", "blocked_by_guardrail": "🚧"}[self.value]


@dataclass(frozen=True)
class ArmResult:
    """One side of an experiment: per-item outcomes plus derived metrics."""

    label: str
    # Keyed by item id so the two arms can be aligned even if either dropped
    # items — never rely on positional alignment across two model runs.
    absolute_percentage_error: dict[str, float] = field(default_factory=dict)
    latency_ms: dict[str, float] = field(default_factory=dict)
    confidence: dict[str, int] = field(default_factory=dict)
    predicted: dict[str, float] = field(default_factory=dict)
    actual: dict[str, float] = field(default_factory=dict)
    hallucinated: dict[str, bool] = field(default_factory=dict)
    failures: int = 0
    config: dict = field(default_factory=dict)

    @property
    def item_ids(self) -> set[str]:
        return set(self.absolute_percentage_error)

    def metric_set(self) -> MetricSet:
        """Derived metrics, all tagged MEASURED — these come from real runs."""
        result = MetricSet(label=self.label)
        errors = list(self.absolute_percentage_error.values())
        pairs = [(self.predicted[i], self.actual[i])
                 for i in self.predicted if i in self.actual]

        if errors:
            ci = stats.bootstrap_ci(errors, statistics.median)
            result.add(Metric.measured("mdape", statistics.median(errors),
                                       len(errors), unit="%", ci=ci))
            result.add(Metric.measured("mape", sum(errors) / len(errors),
                                       len(errors), unit="%"))
            within = metrics_module.within_tolerance(pairs, 25.0)
            if within is not None:
                result.add(Metric.measured("within_25pct", within * 100,
                                           len(pairs), unit="%"))
        if pairs:
            for name, fn in (("rmse", metrics_module.rmse),
                             ("mae", metrics_module.mae),
                             ("bias", metrics_module.bias)):
                value = fn(pairs)
                if value is not None:
                    unit = "%" if name == "bias" else ""
                    result.add(Metric.measured(name, value, len(pairs), unit=unit))
        if self.latency_ms:
            summary = metrics_module.latency_summary(list(self.latency_ms.values()))
            for key in ("p50", "p95"):
                if summary.get(key) is not None:
                    result.add(Metric.measured(f"latency_{key}", summary[key],
                                               len(self.latency_ms), unit="ms"))
        if self.hallucinated:
            rate = sum(self.hallucinated.values()) / len(self.hallucinated)
            result.add(Metric.measured("hallucination_rate", rate * 100,
                                       len(self.hallucinated), unit="%"))
        if self.confidence and self.predicted and self.actual:
            scored = [(self.confidence[i], self.predicted[i], self.actual[i])
                      for i in self.confidence
                      if i in self.predicted and i in self.actual]
            if scored:
                result.add(Metric.measured(
                    "calibration_ece", metrics_module.calibration(scored).ece,
                    len(scored)))
        return result


@dataclass(frozen=True)
class Guardrail:
    """A metric that must not regress, regardless of the primary outcome."""

    metric: str
    max_relative_increase: float = 0.10   # 10% worse is the default tolerance
    absolute_ceiling: float | None = None
    description: str = ""

    def check(self, baseline: Metric | None, candidate: Metric | None) -> str | None:
        """Return a violation message, or None if the guardrail holds."""
        if candidate is None or candidate.value is None:
            return None
        if self.absolute_ceiling is not None and candidate.value > self.absolute_ceiling:
            return (f"{self.metric} = {candidate.value:.2f} exceeds ceiling "
                    f"{self.absolute_ceiling:.2f}")
        if baseline is None or baseline.value is None:
            return None

        if baseline.value == 0:
            # A zero baseline is the *best possible* value for every guardrail
            # metric here (no hallucinations, no schema violations). Relative
            # increase is undefined, and the earlier `not baseline.value` test
            # silently disabled the guardrail at exactly the baseline most worth
            # protecting. Any move away from zero is a regression.
            if candidate.value > 0:
                return (f"{self.metric} regressed from 0 to {candidate.value:.2f} "
                        f"— any increase from a clean baseline is a regression")
            return None

        increase = (candidate.value - baseline.value) / abs(baseline.value)
        if increase > self.max_relative_increase:
            return (f"{self.metric} regressed {increase:+.1%} "
                    f"({baseline.value:.2f} → {candidate.value:.2f}), "
                    f"limit {self.max_relative_increase:+.0%}")
        return None


DEFAULT_GUARDRAILS = (
    Guardrail("latency_p95", 0.25, description="scans must not get visibly slower"),
    Guardrail("hallucination_rate", 0.0,
              description="fabrication must never increase"),
    Guardrail("calibration_ece", 0.20,
              description="confidence must not become less meaningful"),
    Guardrail("bias", 0.30,
              description="systematic over-valuation is the dangerous direction"),
)


@dataclass
class ExperimentResult:
    name: str
    primary_metric: str
    baseline: ArmResult
    candidate: ArmResult
    comparison: stats.ComparisonResult | None
    verdict: Verdict
    guardrail_violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    paired_items: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "primary_metric": self.primary_metric,
            "verdict": self.verdict.value,
            "paired_items": self.paired_items,
            "baseline": self.baseline.metric_set().to_dict(),
            "candidate": self.candidate.metric_set().to_dict(),
            "comparison": ({
                "n_pairs": self.comparison.n_pairs,
                "median_delta": self.comparison.median_delta,
                "p_value": self.comparison.p_value,
                "effect_size": self.comparison.effect_size,
                "effect_label": self.comparison.effect_label,
                "significant": self.comparison.significant,
                "wins_candidate": self.comparison.wins_b,
                "wins_baseline": self.comparison.wins_a,
                "test": self.comparison.test_used,
            } if self.comparison else None),
            "guardrail_violations": self.guardrail_violations,
            "warnings": self.warnings,
            "created_at": self.created_at.isoformat(),
        }

    def render(self) -> str:
        lines = [
            "",
            f"═══ experiment: {self.name} ═══",
            f"primary metric : {self.primary_metric}",
            f"paired items   : {self.paired_items}",
            "",
            self.baseline.metric_set().render(),
            "",
            self.candidate.metric_set().render(),
            "",
        ]
        if self.comparison:
            c = self.comparison
            p = "n/a" if c.p_value is None else f"{c.p_value:.4f}"
            lines += [
                "Paired comparison (candidate − baseline, lower is better)",
                f"  median delta   {c.median_delta:+.2f}"
                + (f"  [{c.ci_low:+.2f}, {c.ci_high:+.2f}]"
                   if c.ci_low is not None else ""),
                f"  p-value        {p}  ({c.test_used})",
                f"  effect size    {c.effect_size if c.effect_size is None else round(c.effect_size, 3)}"
                f"  ({c.effect_label})",
                f"  wins           candidate {c.wins_b} · baseline {c.wins_a} · ties {c.ties}",
                "",
            ]
        if self.guardrail_violations:
            lines.append("Guardrail violations")
            lines += [f"  🚧 {v}" for v in self.guardrail_violations]
            lines.append("")
        if self.warnings:
            lines.append("Warnings")
            lines += [f"  ⚠️  {w}" for w in self.warnings]
            lines.append("")
        lines.append(f"VERDICT: {self.verdict.symbol} {self.verdict.value.upper()}")
        return "\n".join(lines) + "\n"


# Below this many paired items, a null result means "we could not tell",
# not "there is no difference". Reported as a warning rather than silently
# returning INCONCLUSIVE, so the reader knows which it was.
MIN_PAIRED_ITEMS = 30

# Minimum improvement, relative to the baseline's own error, that is worth
# shipping.
#
# Statistical significance is not practical significance, and effect-size
# measures do not close the gap on their own: Cliff's delta reports "small" for
# a uniform shift of 0.005%, because it measures distributional overlap rather
# than magnitude. With a few hundred paired items a p-value near zero is easy to
# obtain for a difference no user could perceive.
#
# So a change must also move the metric by at least this much in relative terms.
# 1% of the baseline error is deliberately modest — it rejects noise-chasing
# without blocking genuine incremental progress.
MIN_RELATIVE_IMPROVEMENT = 0.01


def run_experiment(
    name: str,
    baseline: ArmResult,
    candidate: ArmResult,
    *,
    primary_metric: str = "mdape",
    guardrails: tuple[Guardrail, ...] = DEFAULT_GUARDRAILS,
    require_significance: bool = True,
) -> ExperimentResult:
    """Compare two arms and return a verdict.

    Pure: takes recorded outcomes, performs no model calls. That makes the
    decision logic testable without an API key, which is what lets the shipping
    criteria themselves be covered by tests.
    """
    warnings: list[str] = []

    shared = sorted(baseline.item_ids & candidate.item_ids)
    if not shared:
        return ExperimentResult(
            name=name, primary_metric=primary_metric, baseline=baseline,
            candidate=candidate, comparison=None, verdict=Verdict.INCONCLUSIVE,
            warnings=["no items evaluated by both arms"], paired_items=0)

    dropped = len(baseline.item_ids ^ candidate.item_ids)
    if dropped:
        warnings.append(
            f"{dropped} item(s) evaluated by only one arm and excluded — "
            "comparison uses the shared subset")

    errors_a = [baseline.absolute_percentage_error[i] for i in shared]
    errors_b = [candidate.absolute_percentage_error[i] for i in shared]
    comparison = stats.compare_paired(errors_a, errors_b)

    if len(shared) < MIN_PAIRED_ITEMS:
        warnings.append(
            f"only {len(shared)} paired items (< {MIN_PAIRED_ITEMS}); "
            "a null result here means underpowered, not equivalent")

    baseline_metrics = baseline.metric_set()
    candidate_metrics = candidate.metric_set()
    violations = [
        message for guardrail in guardrails
        if (message := guardrail.check(baseline_metrics.get(guardrail.metric),
                                       candidate_metrics.get(guardrail.metric)))
    ]

    baseline_primary = baseline_metrics.get(primary_metric)
    verdict = _decide(comparison, violations, require_significance, len(shared),
                      baseline_value=baseline_primary.value if baseline_primary else None)

    if (comparison and comparison.significant
            and verdict is Verdict.INCONCLUSIVE and baseline_primary
            and baseline_primary.value):
        relative = abs(comparison.median_delta) / abs(baseline_primary.value)
        if relative < MIN_RELATIVE_IMPROVEMENT:
            warnings.append(
                f"statistically significant but only {relative:.2%} relative "
                f"change (floor {MIN_RELATIVE_IMPROVEMENT:.0%}) — significance "
                "without magnitude is not a reason to ship")

    return ExperimentResult(
        name=name, primary_metric=primary_metric, baseline=baseline,
        candidate=candidate, comparison=comparison, verdict=verdict,
        guardrail_violations=violations, warnings=warnings,
        paired_items=len(shared),
    )


def _decide(
    comparison: stats.ComparisonResult | None,
    violations: list[str],
    require_significance: bool,
    n_paired: int,
    *,
    baseline_value: float | None = None,
) -> Verdict:
    """Shipping decision.

    Guardrails are absolute and checked first: an accuracy win that breaks one
    is not a win.

    A candidate then has to clear three independent bars, because each catches a
    different way of being fooled:

    * **significance** — the difference is unlikely to be chance;
    * **effect size** — the distributions genuinely separate;
    * **practical magnitude** — the change is large enough relative to the
      baseline to be worth shipping.

    The third is not redundant. With a few hundred paired items, a uniform shift
    far too small for any user to notice produces p ≈ 0 and a non-negligible
    Cliff's delta, because that statistic measures overlap rather than size.
    Without the magnitude floor, the framework would rubber-stamp noise.
    """
    if violations:
        return Verdict.BLOCKED_BY_GUARDRAIL
    if comparison is None:
        return Verdict.INCONCLUSIVE
    if n_paired < MIN_PAIRED_ITEMS:
        return Verdict.INCONCLUSIVE
    if require_significance and not comparison.significant:
        return Verdict.INCONCLUSIVE
    if comparison.effect_label == "negligible":
        return Verdict.INCONCLUSIVE

    if baseline_value:
        relative = abs(comparison.median_delta) / abs(baseline_value)
        if relative < MIN_RELATIVE_IMPROVEMENT:
            return Verdict.INCONCLUSIVE

    return Verdict.SHIP if comparison.median_delta < 0 else Verdict.REJECT


def save_result(result: ExperimentResult, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, indent=2, default=str)
