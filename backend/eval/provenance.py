"""Provenance tagging: measured, projected, or unavailable.

Why this is a module and not a convention
-----------------------------------------
The single easiest way for an evaluation platform to mislead is to present a
projected number in the same typeface as a measured one. Once a figure is copied
into a slide or a README, its origin is gone — and "MdAPE 18%" reads identically
whether it came from 1,000 verified sales or from someone's estimate of what the
improvement ought to be.

So provenance is not a comment or a naming convention here; it is carried in the
type. `Metric` cannot be constructed without declaring where its value came from,
and every renderer in this package is required to display that tag. A projected
number that loses its label is a bug, not a style issue.

Three states, deliberately not two
----------------------------------
* ``MEASURED``   — computed from real labelled outcomes. The only kind that may
  be used to claim an improvement.
* ``PROJECTED``  — an estimate, model, or extrapolation. Always displayed with a
  marker and never permitted to satisfy a CI gate.
* ``UNAVAILABLE`` — genuinely not computable yet (no dataset, no runs). Distinct
  from zero, because zero silently reads as "perfect" for error metrics.

The third state exists because the honest answer to most questions about this
system today is "we have not measured that yet", and a framework that cannot
express that will invent something instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Provenance(str, Enum):
    MEASURED = "measured"
    PROJECTED = "projected"
    UNAVAILABLE = "unavailable"

    @property
    def marker(self) -> str:
        return {"measured": "✓", "projected": "≈", "unavailable": "—"}[self.value]

    @property
    def can_gate(self) -> bool:
        """Whether a value with this provenance may fail a CI build.

        Only measured values. Gating on a projection would mean a build fails
        because of an assumption someone typed, which is worse than no gate.
        """
        return self is Provenance.MEASURED


@dataclass(frozen=True)
class Metric:
    """A single number that knows where it came from."""

    name: str
    value: float | None
    provenance: Provenance
    unit: str = ""
    sample_size: int = 0
    # Free-text justification. Required for PROJECTED so an estimate can never
    # be traced back to nothing.
    basis: str = ""
    ci_low: float | None = None
    ci_high: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.provenance is Provenance.PROJECTED and not self.basis:
            raise ValueError(
                f"projected metric {self.name!r} must state its basis — "
                "an unexplained estimate is indistinguishable from a guess"
            )
        if self.provenance is Provenance.MEASURED and self.value is None:
            raise ValueError(
                f"measured metric {self.name!r} has no value; use UNAVAILABLE"
            )
        if self.provenance is Provenance.MEASURED and self.sample_size <= 0:
            raise ValueError(
                f"measured metric {self.name!r} must report its sample size"
            )

    @classmethod
    def measured(cls, name: str, value: float, sample_size: int, *,
                 unit: str = "", ci: tuple[float, float] | None = None,
                 **metadata: Any) -> "Metric":
        return cls(
            name=name, value=value, provenance=Provenance.MEASURED, unit=unit,
            sample_size=sample_size,
            ci_low=ci[0] if ci else None, ci_high=ci[1] if ci else None,
            metadata=metadata,
        )

    @classmethod
    def projected(cls, name: str, value: float, basis: str, *,
                  unit: str = "", **metadata: Any) -> "Metric":
        return cls(name=name, value=value, provenance=Provenance.PROJECTED,
                   unit=unit, basis=basis, metadata=metadata)

    @classmethod
    def unavailable(cls, name: str, reason: str) -> "Metric":
        return cls(name=name, value=None, provenance=Provenance.UNAVAILABLE,
                   basis=reason)

    @property
    def is_measured(self) -> bool:
        return self.provenance is Provenance.MEASURED

    def format(self, precision: int = 2) -> str:
        """Render with the provenance marker attached. Never render without it."""
        if self.value is None:
            return f"{self.provenance.marker} n/a ({self.basis or 'not measured'})"
        body = f"{self.value:.{precision}f}{self.unit}"
        if self.ci_low is not None and self.ci_high is not None:
            body += f" [{self.ci_low:.{precision}f}, {self.ci_high:.{precision}f}]"
        if self.provenance is Provenance.MEASURED:
            return f"{self.provenance.marker} {body} (n={self.sample_size})"
        return f"{self.provenance.marker} {body} — PROJECTED: {self.basis}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "provenance": self.provenance.value,
            "unit": self.unit,
            "sample_size": self.sample_size,
            "basis": self.basis,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            **({"metadata": self.metadata} if self.metadata else {}),
        }


@dataclass
class MetricSet:
    """A named collection of metrics, e.g. one evaluation run."""

    label: str
    metrics: dict[str, Metric] = field(default_factory=dict)

    def add(self, metric: Metric) -> None:
        self.metrics[metric.name] = metric

    def get(self, name: str) -> Metric | None:
        return self.metrics.get(name)

    @property
    def measured(self) -> dict[str, Metric]:
        return {k: m for k, m in self.metrics.items() if m.is_measured}

    @property
    def projected(self) -> dict[str, Metric]:
        return {k: m for k, m in self.metrics.items()
                if m.provenance is Provenance.PROJECTED}

    @property
    def has_any_measurement(self) -> bool:
        return bool(self.measured)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "measured_count": len(self.measured),
            "projected_count": len(self.projected),
            "metrics": {k: m.to_dict() for k, m in self.metrics.items()},
        }

    def render(self, precision: int = 2) -> str:
        if not self.metrics:
            return f"{self.label}: no metrics"
        width = max(len(k) for k in self.metrics)
        lines = [f"{self.label}", "─" * (width + 40)]
        for name, metric in sorted(self.metrics.items()):
            lines.append(f"  {name:<{width}}  {metric.format(precision)}")
        if self.projected:
            lines.append("")
            lines.append(f"  ≈ = projected, not measured ({len(self.projected)} of "
                         f"{len(self.metrics)})")
        return "\n".join(lines)
