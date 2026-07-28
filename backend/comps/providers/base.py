"""Provider abstraction and registry.

Every marketplace differs in auth, rate limits, taxonomy, currency, condition
vocabulary, and whether it publishes completed-sale prices at all. This module
is where that variance is contained: the engine talks only to `CompsProvider`
and knows nothing about eBay or StockX specifically.

No provider implemented here requires credentials. Real integrations are
deliberately out of scope for this milestone — several of them are a **legal
decision before an engineering one** (see docs/COMPS-ARCHITECTURE.md), and
shipping a scraper before that review would be the wrong order.

What *is* here is everything a real provider will need to slot into:
the protocol, capability declaration, health reporting, per-provider circuit
breaking, and a registry with enable/disable control.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from comps.models import (
    Comp,
    Marketplace,
    ProviderHealth,
    ProviderQuery,
    SOLD_DATA_MARKETPLACES,
)

log = logging.getLogger("snapworth.comps.providers")


class ProviderError(Exception):
    """Provider failed to serve a query. Never propagates past the engine."""


class ProviderNotConfigured(ProviderError):
    """Credentials or configuration are missing. Not a transient failure."""


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can actually do.

    Declared rather than discovered so the engine can skip a provider before
    paying for a call — e.g. not asking Discogs about a pair of trainers.
    """

    marketplace: Marketplace
    categories: frozenset[str]              # empty ⇒ all categories
    supports_sold: bool
    requires_credentials: bool = True
    supports_currency: frozenset[str] = frozenset({"USD"})
    max_results: int = 50
    typical_latency_ms: float = 400.0

    def handles(self, category: str) -> bool:
        return not self.categories or category.strip().lower() in self.categories


@runtime_checkable
class CompsProvider(Protocol):
    """The contract every marketplace integration implements."""

    name: str
    capabilities: ProviderCapabilities

    async def search(self, query: ProviderQuery) -> list[Comp]:
        """Return completed sales matching `query`.

        Must not raise for ordinary failure — raise `ProviderError` so the
        engine can record and continue. Returning `[]` means "no matches",
        which is different from "I failed" and is treated differently.
        """
        ...

    async def health(self) -> ProviderHealth:
        ...


# ── Circuit breaker ──────────────────────────────────────────────────────────

@dataclass
class CircuitBreaker:
    """Per-provider failure isolation.

    A struggling provider must not be retried into the fan-out budget on every
    request — that converts one slow dependency into slow scans for everybody.
    After `threshold` failures inside `window`, the circuit opens and the
    provider is skipped entirely until `cooldown` elapses, then a single probe
    decides whether to close it again.
    """

    threshold: int = 5
    window_seconds: float = 30.0
    cooldown_seconds: float = 60.0

    _failures: list[float] = field(default_factory=list)
    _opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            # Half-open: allow one probe through.
            self._opened_at = None
            self._failures.clear()
            return False
        return True

    def record_success(self) -> None:
        self._failures.clear()
        self._opened_at = None

    def record_failure(self) -> None:
        now = time.monotonic()
        self._failures = [t for t in self._failures if now - t <= self.window_seconds]
        self._failures.append(now)
        if len(self._failures) >= self.threshold and self._opened_at is None:
            self._opened_at = now
            log.warning("circuit opened after %d failures", len(self._failures))


# ── Registry ─────────────────────────────────────────────────────────────────

@dataclass
class RegisteredProvider:
    provider: CompsProvider
    enabled: bool = True
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    @property
    def usable(self) -> bool:
        return self.enabled and not self.breaker.is_open


class ProviderRegistry:
    """Holds providers and decides which are eligible for a given query."""

    def __init__(self) -> None:
        self._providers: dict[str, RegisteredProvider] = {}

    def register(self, provider: CompsProvider, *, enabled: bool = True) -> None:
        if provider.name in self._providers:
            raise ValueError(f"provider {provider.name!r} already registered")
        self._providers[provider.name] = RegisteredProvider(provider, enabled=enabled)
        log.info("registered comps provider", extra={
            "provider": provider.name, "enabled": enabled,
            "sold": provider.capabilities.supports_sold})

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def clear(self) -> None:
        self._providers.clear()

    def get(self, name: str) -> RegisteredProvider | None:
        return self._providers.get(name)

    def set_enabled(self, name: str, enabled: bool) -> None:
        entry = self._providers.get(name)
        if entry:
            entry.enabled = enabled

    @property
    def all(self) -> list[RegisteredProvider]:
        return list(self._providers.values())

    def eligible(self, query: ProviderQuery) -> list[RegisteredProvider]:
        """Providers worth querying for this specific request.

        Filters on four independent grounds, cheapest first — each one avoids a
        network call that could only ever produce unusable data:

        1. disabled, or circuit open
        2. does not cover this category
        3. cannot supply completed-sale prices when the caller demands them
        4. marketplace is not a recognised sold-data source
        """
        eligible: list[RegisteredProvider] = []
        for entry in self._providers.values():
            if not entry.usable:
                continue
            caps = entry.provider.capabilities
            if not caps.handles(query.identity.category):
                continue
            if query.sold_only and not caps.supports_sold:
                continue
            if query.sold_only and caps.marketplace not in SOLD_DATA_MARKETPLACES:
                continue
            eligible.append(entry)
        return eligible

    async def health(self) -> list[ProviderHealth]:
        async def probe(entry: RegisteredProvider) -> ProviderHealth:
            try:
                return await entry.provider.health()
            except Exception as exc:
                return ProviderHealth(
                    name=entry.provider.name, available=False,
                    configured=False, error=str(exc)[:200])

        if not self._providers:
            return []
        return list(await asyncio.gather(*(probe(e) for e in self._providers.values())))


# Process-wide default registry. Tests build their own.
registry = ProviderRegistry()
