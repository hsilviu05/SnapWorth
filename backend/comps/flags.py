"""Feature flags for the comps engine.

Comps ship dark. Every stage of the rollout in docs/COMPS-ARCHITECTURE.md is
gated here, because the failure mode we must avoid is serving an evidence-backed
claim before the evidence pipeline has been validated — that is how the original
screenshot problem happened, and repeating it with real infrastructure behind it
would be worse.

Three states matter, and they are deliberately separate flags rather than one
enum, because a deploy should be able to move between them independently:

* **off** — the engine is not called at all. Zero latency cost.
* **shadow** — the engine runs, results are logged and measured, but the user
  sees a model-only valuation. This is where phase 1 lives.
* **live** — comps may set `valuation_source = "comps"`.

`shadow` is the important one. It buys real production measurement (hit rate,
latency, agreement with the model prior) at zero user risk, and the exit
criterion for going live is a number rather than a feeling.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger("snapworth.comps.flags")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class CompsFlags:
    """Resolved once at startup; overridable in tests by constructing directly."""

    # Master switch. Off means the engine is never invoked.
    enabled: bool = False

    # Run the engine but never let it influence what the user sees.
    shadow_mode: bool = True

    # Providers permitted to run, by name. Empty means "all registered and
    # enabled". Lets a single provider be rolled out without touching the
    # registry.
    allowed_providers: frozenset[str] = frozenset()

    # Categories the engine will attempt. Empty means all. Phase 1 restricts to
    # clothing and shoes, where identification is strongest.
    allowed_categories: frozenset[str] = frozenset()

    # Wall-clock budget for the entire provider fan-out. Comps must never add a
    # visible second to a scan — whatever has returned by this deadline is used
    # and the rest are cancelled.
    fanout_budget_ms: float = 800.0

    # Per-provider timeout, necessarily below the fan-out budget.
    provider_timeout_ms: float = 700.0

    window_days: int = 90
    # Low-liquidity categories need a longer window to find any sales at all.
    long_window_days: int = 180
    long_window_categories: frozenset[str] = frozenset({"furniture", "collectibles"})

    max_results_per_provider: int = 50

    cache_ttl_seconds: int = 86_400          # 24h — comps move slowly
    negative_cache_ttl_seconds: int = 21_600  # 6h — obscure items stay obscure

    @property
    def influences_user_output(self) -> bool:
        """Whether a comps result may set `valuation_source = "comps"`."""
        return self.enabled and not self.shadow_mode

    def window_for(self, category: str) -> int:
        key = (category or "").strip().lower()
        return self.long_window_days if key in self.long_window_categories else self.window_days

    def permits_category(self, category: str) -> bool:
        if not self.allowed_categories:
            return True
        return (category or "").strip().lower() in self.allowed_categories

    def permits_provider(self, name: str) -> bool:
        if not self.allowed_providers:
            return True
        return name in self.allowed_providers


def _csv(name: str) -> frozenset[str]:
    raw = os.environ.get(name, "")
    return frozenset(v.strip().lower() for v in raw.split(",") if v.strip())


def from_env() -> CompsFlags:
    """Build flags from the environment.

    Defaults are deliberately conservative: disabled, and shadow-mode-on if
    someone enables it without thinking. Turning comps live must be an explicit,
    two-variable decision.
    """
    flags = CompsFlags(
        enabled=_bool("COMPS_ENABLED", False),
        shadow_mode=_bool("COMPS_SHADOW_MODE", True),
        allowed_providers=_csv("COMPS_PROVIDERS"),
        allowed_categories=_csv("COMPS_CATEGORIES"),
        fanout_budget_ms=_float("COMPS_FANOUT_BUDGET_MS", 800.0),
        provider_timeout_ms=_float("COMPS_PROVIDER_TIMEOUT_MS", 700.0),
        window_days=_int("COMPS_WINDOW_DAYS", 90),
        long_window_days=_int("COMPS_LONG_WINDOW_DAYS", 180),
        max_results_per_provider=_int("COMPS_MAX_RESULTS", 50),
        cache_ttl_seconds=_int("COMPS_CACHE_TTL", 86_400),
        negative_cache_ttl_seconds=_int("COMPS_NEGATIVE_CACHE_TTL", 21_600),
    )
    if flags.enabled:
        log.info("comps engine enabled", extra={
            "shadow": flags.shadow_mode,
            "providers": sorted(flags.allowed_providers) or "all",
            "categories": sorted(flags.allowed_categories) or "all",
        })
    return flags


# Process-wide default. Rebuilt at startup; tests construct their own.
flags = from_env()


def reload_flags() -> CompsFlags:
    global flags
    flags = from_env()
    return flags
