"""Dashboard data models.

Produces the JSON an internal evaluation dashboard renders. No frontend here —
a chart library would be the least durable part of this platform, whereas the
data contract is what everything else depends on.

The one rule the dashboard must enforce
---------------------------------------
Every panel carries the provenance of its numbers, and a panel containing any
projected value is flagged at the panel level as well as the metric level. This
is not decoration: a dashboard is where numbers get screenshotted, and a
screenshot strips context. If a chart cannot say whether it is measured, it
should not render.

`DashboardPayload.integrity` summarises this for the whole page, so a dashboard
built from zero measurements says so loudly rather than showing empty axes that
read as "nothing wrong".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from eval.provenance import Metric, MetricSet, Provenance


class PanelKind(str, Enum):
    SCALAR = "scalar"
    TIME_SERIES = "time_series"
    BREAKDOWN = "breakdown"
    CALIBRATION = "calibration"
    COMPARISON = "comparison"
    TABLE = "table"


@dataclass(frozen=True)
class Panel:
    """One dashboard panel."""

    key: str
    title: str
    kind: PanelKind
    metrics: list[Metric] = field(default_factory=list)
    series: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    subtitle: str = ""

    @property
    def provenance(self) -> Provenance:
        """Worst provenance in the panel.

        Worst rather than best on purpose: a panel mixing measured and projected
        values must be labelled by the weaker one, or the label is misleading
        for exactly the number a reader is most likely to misread.
        """
        if not self.metrics:
            return Provenance.UNAVAILABLE
        if any(m.provenance is Provenance.UNAVAILABLE for m in self.metrics):
            return Provenance.UNAVAILABLE
        if any(m.provenance is Provenance.PROJECTED for m in self.metrics):
            return Provenance.PROJECTED
        return Provenance.MEASURED

    @property
    def renderable(self) -> bool:
        return bool(self.metrics or self.series or self.rows)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "subtitle": self.subtitle,
            "kind": self.kind.value,
            "provenance": self.provenance.value,
            "provenance_marker": self.provenance.marker,
            "renderable": self.renderable,
            "metrics": [m.to_dict() for m in self.metrics],
            "series": self.series,
            "rows": self.rows,
        }


@dataclass
class DashboardPayload:
    generated_at: datetime
    dataset_version: str
    panels: list[Panel] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def integrity(self) -> dict:
        """Page-level honesty summary.

        `is_evidence_backed` is the single field a reader should look at first:
        False means nothing on this page was measured, regardless of how
        complete the layout looks.
        """
        measured = sum(1 for p in self.panels if p.provenance is Provenance.MEASURED)
        projected = sum(1 for p in self.panels if p.provenance is Provenance.PROJECTED)
        unavailable = sum(1 for p in self.panels
                          if p.provenance is Provenance.UNAVAILABLE)
        return {
            "measured_panels": measured,
            "projected_panels": projected,
            "unavailable_panels": unavailable,
            "is_evidence_backed": measured > 0,
            "warning": (
                "No panel on this dashboard contains measured data. Every value "
                "shown is projected or unavailable — do not cite any of it as a "
                "result." if measured == 0 else ""
            ),
        }

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "dataset_version": self.dataset_version,
            "integrity": self.integrity,
            "panels": [p.to_dict() for p in self.panels],
            "notes": self.notes,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def render_summary(self) -> str:
        integrity = self.integrity
        lines = [
            "",
            "═══ evaluation dashboard ═══",
            f"dataset: {self.dataset_version or '(none)'}   "
            f"generated: {self.generated_at:%Y-%m-%d %H:%M UTC}",
            f"panels: {integrity['measured_panels']} measured · "
            f"{integrity['projected_panels']} projected · "
            f"{integrity['unavailable_panels']} unavailable",
        ]
        if integrity["warning"]:
            lines += ["", f"  ⚠️  {integrity['warning']}"]
        lines.append("")
        for panel in self.panels:
            lines.append(f"  {panel.provenance.marker} {panel.title}")
            for metric in panel.metrics[:4]:
                lines.append(f"      {metric.name:<22} {metric.format()}")
        return "\n".join(lines) + "\n"


# ── Builders ─────────────────────────────────────────────────────────────────

def overall_panel(metrics: MetricSet) -> Panel:
    keys = ("mdape", "mape", "within_25pct", "rmse", "mae", "bias")
    return Panel(
        key="overall", title="Overall accuracy", kind=PanelKind.SCALAR,
        subtitle="headline valuation quality",
        metrics=[m for k in keys if (m := metrics.get(k))],
    )


def breakdown_panel(key: str, title: str, rows: dict[str, dict]) -> Panel:
    return Panel(
        key=key, title=title, kind=PanelKind.BREAKDOWN,
        rows=[{"group": group, **values} for group, values in sorted(rows.items())],
        metrics=[
            Metric.measured(f"{key}_groups", len(rows), max(1, len(rows)))
        ] if rows else [],
    )


def calibration_panel(calibration: dict | None) -> Panel:
    if not calibration or calibration.get("ece") is None:
        return Panel(
            key="calibration", title="Confidence calibration",
            kind=PanelKind.CALIBRATION,
            metrics=[Metric.unavailable(
                "calibration_ece",
                "no labelled outcomes yet — confidence weights remain a prior, "
                "not a fit")],
        )
    return Panel(
        key="calibration", title="Confidence calibration",
        kind=PanelKind.CALIBRATION,
        subtitle="claimed confidence vs observed accuracy",
        metrics=[Metric.measured("calibration_ece", calibration["ece"],
                                 calibration.get("n", 0))],
        rows=calibration.get("buckets", []),
    )


def latency_panel(metrics: MetricSet) -> Panel:
    return Panel(
        key="latency", title="Latency", kind=PanelKind.SCALAR,
        metrics=[m for k in ("latency_p50", "latency_p95", "latency_p99")
                 if (m := metrics.get(k))],
    )


def failures_panel(report) -> Panel:
    """Top failure modes, from `erroranalysis.ErrorReport`."""
    if report is None or not report.by_mode:
        return Panel(key="failures", title="Top failures", kind=PanelKind.TABLE,
                     metrics=[Metric.unavailable("failures", "no evaluation run yet")])
    return Panel(
        key="failures", title="Top failure modes", kind=PanelKind.TABLE,
        subtitle=f"{report.total_failures} failures across "
                 f"{report.total_evaluated} items",
        metrics=[Metric.measured("failure_rate", report.failure_rate * 100,
                                 report.total_evaluated, unit="%")],
        rows=[{"mode": s.mode.value, "count": s.count,
               "share": round(s.share, 4),
               "median_error_pct": round(s.median_error_pct, 2),
               "owner": s.owner}
              for s in report.by_mode[:10]],
    )


def trend_panel(history: list[dict]) -> Panel:
    """Metric trend across recorded runs.

    Needs at least two runs to mean anything, and says so rather than drawing a
    single point as if it were a trend.
    """
    if len(history) < 2:
        return Panel(
            key="trend", title="Trend over time", kind=PanelKind.TIME_SERIES,
            metrics=[Metric.unavailable(
                "trend", f"only {len(history)} recorded run(s); a trend needs at "
                         "least two")],
        )
    return Panel(
        key="trend", title="Trend over time", kind=PanelKind.TIME_SERIES,
        series=history,
        metrics=[Metric.measured("recorded_runs", len(history), len(history))],
    )


def comparison_panel(experiment_dict: dict | None) -> Panel:
    if not experiment_dict:
        return Panel(key="comparison", title="Model / prompt comparison",
                     kind=PanelKind.COMPARISON,
                     metrics=[Metric.unavailable("comparison",
                                                 "no experiment recorded")])
    comparison = experiment_dict.get("comparison") or {}
    return Panel(
        key="comparison",
        title=f"Comparison: {experiment_dict.get('name', 'unnamed')}",
        kind=PanelKind.COMPARISON,
        subtitle=f"verdict: {experiment_dict.get('verdict', 'unknown')}",
        rows=[{
            "paired_items": experiment_dict.get("paired_items", 0),
            "median_delta": comparison.get("median_delta"),
            "p_value": comparison.get("p_value"),
            "effect": comparison.get("effect_label"),
            "significant": comparison.get("significant"),
        }],
    )


def build(
    *,
    metrics: MetricSet,
    dataset_version: str = "",
    by_category: dict[str, dict] | None = None,
    by_brand: dict[str, dict] | None = None,
    calibration: dict | None = None,
    error_report=None,
    history: list[dict] | None = None,
    experiment: dict | None = None,
    notes: list[str] | None = None,
) -> DashboardPayload:
    """Assemble the full dashboard payload from whatever is available.

    Every section degrades to an explicit "unavailable" panel rather than being
    omitted. An absent panel reads as "we do not track that"; an unavailable one
    reads as "we track it and have not measured it yet", which is the truth.
    """
    panels = [
        overall_panel(metrics),
        breakdown_panel("by_category", "Accuracy by category", by_category or {}),
        breakdown_panel("by_brand", "Accuracy by brand", by_brand or {}),
        calibration_panel(calibration),
        latency_panel(metrics),
        failures_panel(error_report),
        trend_panel(history or []),
        comparison_panel(experiment),
    ]
    return DashboardPayload(
        generated_at=datetime.now(timezone.utc),
        dataset_version=dataset_version,
        panels=panels,
        notes=notes or [],
    )
