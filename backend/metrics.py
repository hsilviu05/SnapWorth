"""Prometheus-compatible metrics, without the client library.

Why not `prometheus_client`
---------------------------
The same reasoning as `eval/stats.py`: this runs on the request path in every
container, and each dependency is a supply-chain surface and a cold-start cost.
The Prometheus text exposition format is a documented, stable, line-based
format — implementing it is ~150 lines and removes a dependency from the hot
path. Swapping to the official client later is a drop-in change because the
`Counter`/`Gauge`/`Histogram` surface matches it deliberately.

The failure mode this module is designed against
------------------------------------------------
**Unbounded label cardinality.** A metric labelled with a device id, an item
name, or a raw URL path creates one time series per distinct value. That is the
single most common way a metrics layer takes down the monitoring system it was
meant to protect — and it usually happens weeks after launch, when a new code
path starts passing user input as a label.

So `LabelSet` validates against a declared allowlist at registration time, and
`_MAX_SERIES_PER_METRIC` is a hard stop. Exceeding it drops the sample and logs
once, rather than growing without limit.

Thread safety
-------------
The service is async and single-process per container, but uvicorn's default
worker model plus any future thread-pool offload means counters can be touched
concurrently. Every mutation is under a lock. The cost is negligible next to the
I/O these metrics describe.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable

log = logging.getLogger("snapworth.metrics")

# Hard ceiling on distinct label combinations per metric. Chosen well above any
# legitimate combination of the declared label sets below, and far below the
# point at which a scrape becomes expensive.
_MAX_SERIES_PER_METRIC = 200

# Latency buckets in seconds. Tuned to this service's actual shape: a scan is
# dominated by a 2–6 s model call, so the useful resolution is in seconds, not
# the millisecond-heavy default buckets.
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0)

# Upload sizes in bytes. The client now downscales to ~280 KB (see
# ScanAPIClient.encodeForUpload), so buckets cluster below 1 MB — a distribution
# that suddenly shifts right means the client-side downscale regressed.
SIZE_BUCKETS = (50_000, 100_000, 250_000, 500_000, 1_000_000, 2_000_000,
                5_000_000, 10_000_000)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@dataclass(frozen=True)
class LabelSet:
    """Validated label values for one time series."""

    pairs: tuple[tuple[str, str], ...]

    @classmethod
    def of(cls, **labels: str) -> "LabelSet":
        return cls(tuple(sorted((k, str(v)) for k, v in labels.items())))

    def render(self) -> str:
        if not self.pairs:
            return ""
        inner = ",".join(f'{k}="{_escape(v)}"' for k, v in self.pairs)
        return "{" + inner + "}"


class _Metric:
    def __init__(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.labels = tuple(sorted(labels))
        self._lock = threading.Lock()
        self._warned_cardinality = False

    def _check_labels(self, label_set: LabelSet) -> bool:
        got = tuple(k for k, _ in label_set.pairs)
        if got != self.labels:
            log.warning("metric %s given labels %s, expected %s — sample dropped",
                        self.name, got, self.labels)
            return False
        return True

    def _cardinality_ok(self, current: int) -> bool:
        if current < _MAX_SERIES_PER_METRIC:
            return True
        if not self._warned_cardinality:
            # Warn once. Logging per dropped sample would turn a cardinality
            # problem into a log-volume problem.
            log.error(
                "metric %s hit the %d-series cardinality cap; further samples "
                "dropped. A label is almost certainly carrying user input.",
                self.name, _MAX_SERIES_PER_METRIC)
            self._warned_cardinality = True
        return False


class Counter(_Metric):
    """Monotonically increasing count."""

    def __init__(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        super().__init__(name, help_text, labels)
        self._values: dict[LabelSet, float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        label_set = LabelSet.of(**labels)
        if not self._check_labels(label_set):
            return
        with self._lock:
            if label_set not in self._values and not self._cardinality_ok(len(self._values)):
                return
            self._values[label_set] = self._values.get(label_set, 0.0) + amount

    def value(self, **labels: str) -> float:
        return self._values.get(LabelSet.of(**labels), 0.0)

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} counter"]
        with self._lock:
            items = list(self._values.items())
        if not items:
            lines.append(f"{self.name}{LabelSet(()).render()} 0")
        for label_set, value in sorted(items, key=lambda kv: kv[0].pairs):
            lines.append(f"{self.name}{label_set.render()} {value:g}")
        return lines


class Gauge(_Metric):
    """A value that can go up or down."""

    def __init__(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        super().__init__(name, help_text, labels)
        self._values: dict[LabelSet, float] = {}

    def set(self, value: float, **labels: str) -> None:
        label_set = LabelSet.of(**labels)
        if not self._check_labels(label_set):
            return
        with self._lock:
            if label_set not in self._values and not self._cardinality_ok(len(self._values)):
                return
            self._values[label_set] = value

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        label_set = LabelSet.of(**labels)
        if not self._check_labels(label_set):
            return
        with self._lock:
            if label_set not in self._values and not self._cardinality_ok(len(self._values)):
                return
            self._values[label_set] = self._values.get(label_set, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)

    def value(self, **labels: str) -> float:
        return self._values.get(LabelSet.of(**labels), 0.0)

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} gauge"]
        with self._lock:
            items = list(self._values.items())
        for label_set, value in sorted(items, key=lambda kv: kv[0].pairs):
            lines.append(f"{self.name}{label_set.render()} {value:g}")
        return lines


@dataclass
class _HistogramSeries:
    buckets: dict[float, int] = field(default_factory=dict)
    total: float = 0.0
    count: int = 0


class Histogram(_Metric):
    """Cumulative histogram with a sum and count.

    Percentiles are computed by the metrics backend from bucket boundaries, not
    stored here — storing raw observations to compute an exact p99 would make
    memory grow with traffic, which is exactly what a histogram exists to avoid.
    """

    def __init__(self, name: str, help_text: str,
                 buckets: Iterable[float] = LATENCY_BUCKETS,
                 labels: tuple[str, ...] = ()) -> None:
        super().__init__(name, help_text, labels)
        self.buckets = tuple(sorted(buckets))
        self._series: dict[LabelSet, _HistogramSeries] = {}

    def observe(self, value: float, **labels: str) -> None:
        if value is None or math.isnan(value) or math.isinf(value):
            return
        label_set = LabelSet.of(**labels)
        if not self._check_labels(label_set):
            return
        with self._lock:
            if label_set not in self._series:
                if not self._cardinality_ok(len(self._series)):
                    return
                self._series[label_set] = _HistogramSeries(
                    buckets={b: 0 for b in self.buckets})
            series = self._series[label_set]
            series.total += value
            series.count += 1
            for boundary in self.buckets:
                if value <= boundary:
                    series.buckets[boundary] += 1

    def snapshot(self, **labels: str) -> _HistogramSeries | None:
        return self._series.get(LabelSet.of(**labels))

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}",
                 f"# TYPE {self.name} histogram"]
        with self._lock:
            items = list(self._series.items())
        for label_set, series in sorted(items, key=lambda kv: kv[0].pairs):
            base = dict(label_set.pairs)
            for boundary in self.buckets:
                bucket_labels = LabelSet.of(**base, le=f"{boundary:g}")
                lines.append(
                    f"{self.name}_bucket{bucket_labels.render()} {series.buckets[boundary]}")
            inf_labels = LabelSet.of(**base, le="+Inf")
            lines.append(f"{self.name}_bucket{inf_labels.render()} {series.count}")
            lines.append(f"{self.name}_sum{label_set.render()} {series.total:g}")
            lines.append(f"{self.name}_count{label_set.render()} {series.count}")
        return lines


class Registry:
    def __init__(self) -> None:
        self._metrics: list[_Metric] = []

    def register(self, metric: _Metric):
        self._metrics.append(metric)
        return metric

    def render(self) -> str:
        lines: list[str] = []
        for metric in self._metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Test-only. Never call from the request path."""
        for metric in self._metrics:
            with metric._lock:
                for attr in ("_values", "_series"):
                    store = getattr(metric, attr, None)
                    if store is not None:
                        store.clear()


registry = Registry()


def _counter(name, help_text, labels=()) -> Counter:
    return registry.register(Counter(name, help_text, labels))


def _gauge(name, help_text, labels=()) -> Gauge:
    return registry.register(Gauge(name, help_text, labels))


def _histogram(name, help_text, buckets=LATENCY_BUCKETS, labels=()) -> Histogram:
    return registry.register(Histogram(name, help_text, buckets, labels))


# ── Declared metrics ─────────────────────────────────────────────────────────
#
# Label sets are deliberately small and closed. `endpoint` is the *route
# template* (never the raw path), `status_class` is 2xx/4xx/5xx rather than the
# exact code, and no label anywhere carries a device id, subject, item name or
# other user-controlled value.

# HTTP
http_requests = _counter(
    "snapworth_http_requests_total", "HTTP requests served",
    ("endpoint", "method", "status_class"))
http_duration = _histogram(
    "snapworth_http_request_duration_seconds", "HTTP request duration",
    LATENCY_BUCKETS, ("endpoint", "method"))
http_in_flight = _gauge(
    "snapworth_http_in_flight", "Requests currently being served")

# Rate limiting and quota
rate_limited = _counter(
    "snapworth_rate_limited_total", "Requests rejected by rate limiting", ("scope",))
quota_exhausted = _counter(
    "snapworth_quota_exhausted_total", "Scans refused because the free allowance was spent")

# Model
model_calls = _counter(
    "snapworth_model_calls_total", "Calls to the AI provider", ("operation", "outcome"))
model_duration = _histogram(
    "snapworth_model_duration_seconds", "AI provider call duration", LATENCY_BUCKETS,
    ("operation",))
model_retries = _counter(
    "snapworth_model_retries_total", "AI provider call retries", ("operation", "reason"))
model_tokens = _counter(
    "snapworth_model_tokens_total", "Tokens consumed", ("operation", "kind"))

# Image handling
upload_bytes = _histogram(
    "snapworth_upload_bytes", "Uploaded image size in bytes", SIZE_BUCKETS)
image_processing_duration = _histogram(
    "snapworth_image_processing_seconds", "Server-side image validation and analysis",
    (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0))
upload_rejected = _counter(
    "snapworth_upload_rejected_total", "Uploads rejected by validation", ("reason",))

# Cache
cache_operations = _counter(
    "snapworth_cache_operations_total", "Cache operations", ("operation", "outcome"))
cache_degraded = _gauge(
    "snapworth_cache_degraded", "1 when the durable cache is configured but unreachable")

# Valuation quality — the signal that a model or prompt change went wrong in
# production, visible long before a benchmark run would catch it.
confidence_score = _histogram(
    "snapworth_confidence_score", "Computed confidence score per scan",
    (10, 20, 30, 40, 50, 60, 70, 80, 90, 100))
valuation_clamped = _counter(
    "snapworth_valuation_clamped_total", "Valuations clamped to a category band")

# Entitlements
entitlement_operations = _counter(
    "snapworth_entitlement_operations_total", "Entitlement verifications",
    ("outcome",))

# Dependencies
dependency_errors = _counter(
    "snapworth_dependency_errors_total", "Errors talking to a dependency",
    ("dependency", "kind"))

# Build info — a single labelled gauge, the conventional way to expose version
# so a dashboard can annotate deploys and correlate a regression with a release.
build_info = _gauge(
    "snapworth_build_info", "Build metadata", ("version", "python", "commit"))


# ── Helpers ──────────────────────────────────────────────────────────────────

def status_class(code: int) -> str:
    """Bucket a status code. Keeps cardinality at 5 instead of ~40."""
    if code < 200:
        return "1xx"
    if code < 300:
        return "2xx"
    if code < 400:
        return "3xx"
    if code < 500:
        return "4xx"
    return "5xx"


# Route templates we expose. Anything else is reported as "other" — an unbounded
# `endpoint` label taken from the raw path is a cardinality bomb, because a
# scanner probing random URLs would create a series per probe.
KNOWN_ENDPOINTS = frozenset({
    "/scan", "/listing", "/health", "/health/live", "/health/ready",
    "/metrics", "/privacy", "/terms",
    "/auth/challenge", "/auth/attest", "/auth/refresh", "/auth/entitlement",
})


def endpoint_label(path: str) -> str:
    return path if path in KNOWN_ENDPOINTS else "other"


class Timer:
    """Context manager recording elapsed seconds into a histogram."""

    def __init__(self, histogram: Histogram, **labels: str) -> None:
        self._histogram = histogram
        self._labels = labels
        self._start = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc) -> None:
        self._histogram.observe(time.monotonic() - self._start, **self._labels)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start


def render() -> str:
    return registry.render()
