"""CI quality gates: fail a build when evaluation regresses.

The contract
------------
A pull request that makes valuations worse should not merge. That requires the
gate to be trustworthy in both directions — a gate that fires spuriously gets
disabled within a fortnight, and a gate that never fires is theatre.

Three rules keep it honest:

1. **Only measured values can fail a build.** `Provenance.can_gate` enforces it.
   Failing a build on a projection means failing on someone's assumption.

2. **Missing data is not a pass.** A run that evaluated nothing returns
   `SKIPPED`, never `PASSED`. Silent success on an empty benchmark is the most
   dangerous possible outcome, because it looks identical to a real pass.

3. **Thresholds are relative to a recorded baseline**, not absolute. Absolute
   thresholds either sit so loose they never trigger, or need editing every time
   the benchmark composition changes — and editing a threshold to make a build
   pass is how gates die.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from eval.provenance import Metric, MetricSet, Provenance

log = logging.getLogger("snapworth.eval.gates")


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    WARNED = "warned"
    SKIPPED = "skipped"

    @property
    def symbol(self) -> str:
        return {"passed": "✅", "failed": "❌", "warned": "⚠️", "skipped": "⏭️"}[self.value]


class Direction(str, Enum):
    LOWER_IS_BETTER = "lower"
    HIGHER_IS_BETTER = "higher"


@dataclass(frozen=True)
class Threshold:
    """One gate rule."""

    metric: str
    direction: Direction
    # Relative regression tolerated before failing, e.g. 0.05 = 5% worse.
    max_regression: float = 0.05
    # Optional hard bound, independent of the baseline.
    absolute_limit: float | None = None
    # Warn instead of fail. For metrics that are noisy or not yet trusted.
    warn_only: bool = False
    description: str = ""

    def evaluate(self, baseline: Metric | None, current: Metric | None) -> "GateResult":
        if current is None or current.value is None:
            return GateResult(self.metric, GateStatus.SKIPPED, None, None,
                              message="metric not produced by this run")

        if not current.provenance.can_gate:
            # A projection must never fail a build.
            return GateResult(
                self.metric, GateStatus.SKIPPED, current.value, None,
                message=f"value is {current.provenance.value}, not measured")

        if self.absolute_limit is not None:
            breached = (current.value > self.absolute_limit
                        if self.direction is Direction.LOWER_IS_BETTER
                        else current.value < self.absolute_limit)
            if breached:
                return GateResult(
                    self.metric,
                    GateStatus.WARNED if self.warn_only else GateStatus.FAILED,
                    current.value, None,
                    message=(f"{current.value:.3f} breaches absolute limit "
                             f"{self.absolute_limit:.3f}"))

        if baseline is None or baseline.value is None:
            return GateResult(self.metric, GateStatus.SKIPPED, current.value, None,
                              message="no baseline recorded — first run")

        if baseline.value == 0:
            return GateResult(self.metric, GateStatus.SKIPPED, current.value,
                              baseline.value, message="baseline is zero")

        delta = (current.value - baseline.value) / abs(baseline.value)
        regression = delta if self.direction is Direction.LOWER_IS_BETTER else -delta

        if regression > self.max_regression:
            return GateResult(
                self.metric,
                GateStatus.WARNED if self.warn_only else GateStatus.FAILED,
                current.value, baseline.value,
                message=(f"regressed {regression:+.1%} "
                         f"({baseline.value:.3f} → {current.value:.3f}), "
                         f"tolerance {self.max_regression:.0%}"))

        return GateResult(self.metric, GateStatus.PASSED, current.value,
                          baseline.value,
                          message=f"{regression:+.1%} vs baseline")


@dataclass(frozen=True)
class GateResult:
    metric: str
    status: GateStatus
    current: float | None
    baseline: float | None
    message: str = ""


# Default gates.
#
# Latency and schema compliance are hard failures because both are cheap to
# measure and unambiguous. Accuracy metrics carry wider tolerance because
# benchmark noise at a few hundred items is real, and a gate that fires on noise
# is a gate that gets switched off.
DEFAULT_THRESHOLDS = (
    Threshold("mdape", Direction.LOWER_IS_BETTER, max_regression=0.05,
              description="median accuracy must not degrade"),
    Threshold("within_25pct", Direction.HIGHER_IS_BETTER, max_regression=0.05,
              description="share of usable estimates"),
    Threshold("bias", Direction.LOWER_IS_BETTER, max_regression=0.15,
              description="systematic over-valuation is the dangerous direction"),
    Threshold("calibration_ece", Direction.LOWER_IS_BETTER, max_regression=0.10,
              description="confidence must keep meaning what it says"),
    Threshold("hallucination_rate", Direction.LOWER_IS_BETTER, max_regression=0.0,
              absolute_limit=5.0,
              description="fabrication must never increase"),
    Threshold("latency_p95", Direction.LOWER_IS_BETTER, max_regression=0.20,
              description="scans must not get visibly slower"),
    Threshold("schema_compliance", Direction.HIGHER_IS_BETTER, max_regression=0.0,
              absolute_limit=99.0,
              description="responses must satisfy the API contract"),
    Threshold("repeatability", Direction.HIGHER_IS_BETTER, max_regression=0.10,
              warn_only=True,
              description="same photo, same price — warn while we build history"),
)


@dataclass
class GateReport:
    status: GateStatus
    results: list[GateResult]
    baseline_ref: str = ""
    current_ref: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def failed(self) -> list[GateResult]:
        return [r for r in self.results if r.status is GateStatus.FAILED]

    @property
    def exit_code(self) -> int:
        return 1 if self.status is GateStatus.FAILED else 0

    def render(self) -> str:
        lines = ["", "═══ evaluation gates ═══"]
        if self.baseline_ref:
            lines.append(f"baseline: {self.baseline_ref}   current: {self.current_ref}")
        lines.append("")
        width = max((len(r.metric) for r in self.results), default=10)
        for result in self.results:
            lines.append(
                f"  {result.status.symbol} {result.metric:<{width}}  {result.message}")

        skipped = sum(1 for r in self.results if r.status is GateStatus.SKIPPED)
        if skipped == len(self.results) and self.results:
            lines += ["", "  ⏭️  every gate skipped — nothing was measured.",
                      "     This is NOT a pass. Build a gold set (docs/EVALUATION.md)."]
        lines += ["", f"RESULT: {self.status.symbol} {self.status.value.upper()}"]
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "baseline_ref": self.baseline_ref,
            "current_ref": self.current_ref,
            "created_at": self.created_at.isoformat(),
            "results": [asdict(r) | {"status": r.status.value} for r in self.results],
        }


def check(
    current: MetricSet,
    baseline: MetricSet | None = None,
    *,
    thresholds: tuple[Threshold, ...] = DEFAULT_THRESHOLDS,
    baseline_ref: str = "",
    current_ref: str = "",
) -> GateReport:
    """Evaluate every gate. Returns FAILED if any hard gate failed."""
    results = [
        threshold.evaluate(baseline.get(threshold.metric) if baseline else None,
                           current.get(threshold.metric))
        for threshold in thresholds
    ]

    if any(r.status is GateStatus.FAILED for r in results):
        status = GateStatus.FAILED
    elif all(r.status is GateStatus.SKIPPED for r in results):
        # Never report an unmeasured run as a pass.
        status = GateStatus.SKIPPED
    elif any(r.status is GateStatus.WARNED for r in results):
        status = GateStatus.WARNED
    else:
        status = GateStatus.PASSED

    return GateReport(status=status, results=results,
                      baseline_ref=baseline_ref, current_ref=current_ref)


# ── Baseline persistence ─────────────────────────────────────────────────────

def save_baseline(metrics: MetricSet, path: str | Path, *, ref: str = "") -> None:
    """Record a run as the comparison baseline for future gates."""
    payload = metrics.to_dict() | {
        "ref": ref,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    Path(path).write_text(json.dumps(payload, indent=2))


def load_baseline(path: str | Path) -> MetricSet | None:
    """Load a recorded baseline, or None when absent or unreadable.

    Returning None rather than raising means a first run on a fresh checkout
    skips gracefully instead of failing the build for a missing file.
    """
    file = Path(path)
    if not file.exists():
        return None
    try:
        payload = json.loads(file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("baseline unreadable, treating as absent: %s", exc)
        return None

    result = MetricSet(label=payload.get("label", "baseline"))
    for name, raw in payload.get("metrics", {}).items():
        try:
            provenance = Provenance(raw.get("provenance", "measured"))
            result.add(Metric(
                name=name, value=raw.get("value"), provenance=provenance,
                unit=raw.get("unit", ""), sample_size=raw.get("sample_size", 0),
                basis=raw.get("basis", ""), ci_low=raw.get("ci_low"),
                ci_high=raw.get("ci_high"),
            ))
        except ValueError as exc:
            log.warning("skipping malformed baseline metric %r: %s", name, exc)
    return result


# ── Schema compliance ────────────────────────────────────────────────────────

REQUIRED_SCAN_FIELDS = (
    "item_name", "brand", "category", "condition_notes",
    "est_value_low_usd", "est_value_high_usd", "confidence",
    "sold_listings_count", "listing_title", "listing_description",
)


def schema_compliance(responses: list[dict]) -> Metric:
    """Share of responses satisfying the v1 client contract.

    Gated hard at 99%, because a missing required field is not a quality
    regression — it is a decode failure on an installed client that cannot be
    fixed without an App Store release.
    """
    if not responses:
        return Metric.unavailable("schema_compliance", "no responses evaluated")

    valid = 0
    for response in responses:
        if all(response.get(f) is not None for f in REQUIRED_SCAN_FIELDS):
            low = response.get("est_value_low_usd")
            high = response.get("est_value_high_usd")
            if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low <= high:
                if response.get("confidence") in {"High", "Medium", "Low"}:
                    valid += 1

    return Metric.measured("schema_compliance", valid / len(responses) * 100,
                           len(responses), unit="%")
