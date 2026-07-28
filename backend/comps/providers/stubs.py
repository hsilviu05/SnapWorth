"""Credential-free providers: stubs and a deterministic fixture source.

Two purposes.

`StubProvider` is a **declared-but-unimplemented** marketplace. Registering one
makes the integration surface real and inspectable — `/health` lists it, the
registry filters on its declared capabilities, and the categories it would serve
are documented in code rather than in a wiki. It always reports
`configured=False` and returns nothing, so it can never contribute a comp.

Every real marketplace in the roadmap is declared here, which turns
docs/COMPS-ARCHITECTURE.md's provider table into something executable: the set
of stubs *is* the integration backlog, and each carries the capability
declaration its eventual implementation must satisfy.

`FixtureProvider` serves comps from an in-memory list. It is how the engine,
ranker, deduper and aggregator are tested end-to-end without a network, and how
a developer can exercise the full path locally.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from comps.models import Comp, Marketplace, ProviderHealth, ProviderQuery
from comps.providers.base import ProviderCapabilities


@dataclass
class StubProvider:
    """A marketplace we intend to support but have not implemented.

    Deliberately inert: `search` returns nothing rather than raising, so a stub
    left registered by accident degrades to "no comps from this source" instead
    of failing a scan.
    """

    name: str
    capabilities: ProviderCapabilities
    note: str = ""

    async def search(self, query: ProviderQuery) -> list[Comp]:
        return []

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.name,
            available=False,
            configured=False,
            error=self.note or "not implemented",
        )


@dataclass
class FixtureProvider:
    """Deterministic provider backed by an in-memory comp list.

    `latency_ms` simulates network delay so fan-out budgeting, timeout handling
    and circuit breaking can be tested for real rather than mocked.
    """

    name: str = "fixture"
    comps: list[Comp] = field(default_factory=list)
    latency_ms: float = 0.0
    fail: bool = False
    capabilities: ProviderCapabilities = field(
        default_factory=lambda: ProviderCapabilities(
            marketplace=Marketplace.FIXTURE,
            categories=frozenset(),
            supports_sold=True,
            requires_credentials=False,
            typical_latency_ms=0.0,
        )
    )

    async def search(self, query: ProviderQuery) -> list[Comp]:
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        if self.fail:
            raise RuntimeError("fixture provider configured to fail")
        window = query.window_days
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return [c for c in self.comps if c.age_days(now) <= window][: query.limit]

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.name, available=not self.fail, configured=True,
            latency_ms=self.latency_ms)


# ── The integration backlog, as code ─────────────────────────────────────────
#
# `supports_sold=False` entries can never contribute a price: `ProviderRegistry
# .eligible` filters them out whenever the caller asks for sold-only data, which
# the engine always does. They are declared so the decision is visible and
# argued rather than an unexplained omission.

def default_stubs() -> list[StubProvider]:
    return [
        StubProvider(
            name="ebay",
            note="Browse + Marketplace Insights API; requires approved app credentials",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.EBAY,
                categories=frozenset(),                     # widest coverage
                supports_sold=True,
                supports_currency=frozenset({"USD", "GBP", "EUR", "CAD", "AUD"}),
                typical_latency_ms=450.0,
            ),
        ),
        StubProvider(
            name="stockx",
            note="No official public API; needs commercial agreement",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.STOCKX,
                categories=frozenset({"shoes", "clothing", "accessories"}),
                supports_sold=True,
                typical_latency_ms=350.0,
            ),
        ),
        StubProvider(
            name="goat",
            note="No official public API; overlaps StockX coverage",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.GOAT,
                categories=frozenset({"shoes"}),
                supports_sold=True,
                typical_latency_ms=400.0,
            ),
        ),
        StubProvider(
            name="discogs",
            note="Official API with generous limits; exact release matching",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.DISCOGS,
                categories=frozenset({"books", "collectibles", "other"}),
                supports_sold=True,
                typical_latency_ms=500.0,
            ),
        ),
        StubProvider(
            name="reverb",
            note="Official API; price guide for instruments",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.REVERB,
                categories=frozenset({"other", "collectibles"}),
                supports_sold=True,
                typical_latency_ms=450.0,
            ),
        ),
        StubProvider(
            name="chrono24",
            note="Partner API only; watches",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.CHRONO24,
                categories=frozenset({"accessories"}),
                supports_sold=True,
                typical_latency_ms=600.0,
            ),
        ),
        StubProvider(
            name="mercari",
            note="No official API; US-centric",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.MERCARI,
                categories=frozenset(),
                supports_sold=True,
                typical_latency_ms=500.0,
            ),
        ),
        StubProvider(
            name="grailed",
            note="No API. Scraping requires legal review before any implementation.",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.GRAILED,
                categories=frozenset({"clothing", "shoes", "accessories"}),
                supports_sold=True,
                typical_latency_ms=700.0,
            ),
        ),
        StubProvider(
            name="facebook",
            note="No sold-price data exposed — asking prices only. Cannot price from this.",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.FACEBOOK,
                categories=frozenset(),
                supports_sold=False,
                typical_latency_ms=800.0,
            ),
        ),
        StubProvider(
            name="etsy",
            note="Official API but exposes no sold prices. Cannot price from this.",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.ETSY,
                categories=frozenset({"home", "collectibles", "accessories"}),
                supports_sold=False,
                typical_latency_ms=500.0,
            ),
        ),
        StubProvider(
            name="local",
            note="Regional classifieds (OLX, Gumtree, Leboncoin). No sold data.",
            capabilities=ProviderCapabilities(
                marketplace=Marketplace.LOCAL,
                categories=frozenset(),
                supports_sold=False,
                typical_latency_ms=900.0,
            ),
        ),
    ]


def register_defaults(registry) -> None:
    """Register every declared marketplace as an inert stub.

    Registered *disabled*: a stub can never serve a comp, and leaving them
    enabled would put eleven no-op awaits into every fan-out.
    """
    for stub in default_stubs():
        if registry.get(stub.name) is None:
            registry.register(stub, enabled=False)
