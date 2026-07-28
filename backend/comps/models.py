"""Core data models for the comparable-sales engine.

These types are the contract between every layer: providers produce `Comp`s, the
matcher scores them against an `ItemIdentity`, the aggregator turns survivors
into a `PriceEvidence`, and the engine returns a `CompsResult`.

Two decisions worth stating up front.

**Money is `Decimal`, always.** Float arithmetic on prices accumulates error and
produces figures like `62.000000000000004` in a payload a user reads. Every
monetary value here is `Decimal`, converted once at the provider boundary.

**Everything is frozen.** Comps flow through ranking, dedupe and aggregation;
mutating them mid-pipeline would make a scoring bug essentially untraceable.
Where a stage needs to annotate a comp (e.g. attaching a match score) it returns
a new instance via `with_score`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum


class Condition(str, Enum):
    """Canonical condition ladder.

    Deliberately the same four grades the iOS client already persists in
    `ScanResult.conditionRaw`, so no translation layer is needed between the app
    and the comps engine. Marketplace-specific vocabularies normalise *into*
    this (see `normalize.condition`).
    """

    NEW = "new"
    LIKE_NEW = "likeNew"
    GOOD = "good"
    USED = "used"

    @property
    def rank(self) -> int:
        return {"new": 3, "likeNew": 2, "good": 1, "used": 0}[self.value]

    @property
    def price_multiplier(self) -> Decimal:
        """Relative to the `good` thrift baseline.

        Mirrors `Condition.priceMultiplier` in `ios/SnapWorth/Models/ScanResult.swift`.
        Kept in sync deliberately: if the client and server disagree about what
        condition is worth, the same item prices differently in two places.
        """
        return {
            "new": Decimal("1.35"),
            "likeNew": Decimal("1.15"),
            "good": Decimal("1.00"),
            "used": Decimal("0.78"),
        }[self.value]

    def distance(self, other: "Condition") -> int:
        return abs(self.rank - other.rank)


class Marketplace(str, Enum):
    """Known providers.

    An enum rather than free strings so reliability weights, currency defaults
    and dedupe precedence are exhaustive by construction — adding a marketplace
    forces the author to consider each of those.
    """

    EBAY = "ebay"
    MERCARI = "mercari"
    STOCKX = "stockx"
    GOAT = "goat"
    GRAILED = "grailed"
    CHRONO24 = "chrono24"
    DISCOGS = "discogs"
    REVERB = "reverb"
    FACEBOOK = "facebook"
    ETSY = "etsy"
    LOCAL = "local"
    FIXTURE = "fixture"          # deterministic test/dev source


# Trust weight per marketplace, 0–1. Multiplies into the weighted aggregate.
#
# These encode *data quality*, not popularity. StockX and GOAT score highest
# because they authenticate every item and report an exact SKU-level last-sale
# price — there is almost no ambiguity about what sold. eBay is the workhorse but
# its titles are seller-written and noisy. Facebook and Etsy score lowest not
# because the marketplaces are bad but because neither exposes *sold* prices, so
# anything sourced from them is an asking price wearing a disguise.
MARKETPLACE_RELIABILITY: dict[Marketplace, float] = {
    Marketplace.STOCKX: 0.98,
    Marketplace.GOAT: 0.95,
    Marketplace.CHRONO24: 0.92,
    Marketplace.DISCOGS: 0.92,
    Marketplace.EBAY: 0.90,
    Marketplace.REVERB: 0.88,
    Marketplace.GRAILED: 0.75,
    Marketplace.MERCARI: 0.72,
    Marketplace.LOCAL: 0.55,
    Marketplace.ETSY: 0.45,
    Marketplace.FACEBOOK: 0.40,
    Marketplace.FIXTURE: 1.00,
}

# Marketplaces that genuinely publish completed-sale prices. Anything else is an
# asking price, and an asking price is a wish — see docs/COMPS-ARCHITECTURE.md.
SOLD_DATA_MARKETPLACES: frozenset[Marketplace] = frozenset({
    Marketplace.EBAY, Marketplace.STOCKX, Marketplace.GOAT,
    Marketplace.DISCOGS, Marketplace.REVERB, Marketplace.MERCARI,
    Marketplace.GRAILED, Marketplace.FIXTURE,
})


@dataclass(frozen=True)
class ItemIdentity:
    """What we are trying to find comparables for.

    Produced from the vision model's identification (see `valuation.Valuation`).
    Every field is optional except `category`, because a real photo frequently
    yields nothing but "a blue jumper" — and the engine must degrade rather than
    require a full identity.
    """

    category: str
    brand: str | None = None
    model: str | None = None
    variant: str | None = None
    size: str | None = None
    material: str | None = None
    colour: str | None = None
    year: int | None = None
    edition: str | None = None
    serial: str | None = None
    condition: Condition = Condition.GOOD

    @property
    def is_searchable(self) -> bool:
        """Whether there is enough identity to be worth querying a provider.

        A brand alone is not enough: "Nike, clothing" would return thousands of
        unrelated comps whose median is meaningless. Requiring a model — or at
        minimum a brand plus something else — is what keeps precision usable.
        """
        if not self.brand or self.brand.strip().lower() in {"", "unknown"}:
            return False
        return bool(self.model or self.variant or self.serial or self.edition)

    def cache_key(self) -> str:
        """Stable hash over the identity-defining fields.

        Excludes `condition` and `size`: those filter and weight results but do
        not change *which* item we are looking for, so including them would
        fragment the cache badly for no precision gain.
        """
        from comps.normalize import normalise_text

        parts = [
            normalise_text(self.brand or ""),
            normalise_text(self.model or ""),
            normalise_text(self.variant or ""),
            normalise_text(self.edition or ""),
            (self.serial or "").strip().lower(),
            str(self.year or ""),
            self.category.strip().lower(),
        ]
        digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]
        return f"comps:v1:{digest}"

    def query_string(self) -> str:
        """Human-readable search string for providers that take free text."""
        pieces = [self.brand, self.model, self.variant, self.edition]
        if self.year:
            pieces.append(str(self.year))
        return " ".join(p.strip() for p in pieces if p and p.strip())


@dataclass(frozen=True)
class Comp:
    """One completed sale from one provider."""

    provider: Marketplace
    external_id: str
    title: str
    price: Decimal                      # already normalised to USD
    sold_at: datetime
    currency_original: str = "USD"
    price_original: Decimal | None = None
    condition: Condition | None = None
    shipping: Decimal | None = None
    url: str | None = None
    # Stable per-marketplace seller identifier where the provider exposes one.
    #
    # Load-bearing for deduplication, not decoration: a relist is the *same
    # seller* re-posting an unsold item, whereas ten different sellers moving a
    # common item at the same price are ten genuine data points. Without this,
    # title+price similarity alone collapses fungible-goods clusters and guts
    # the sample. See dedupe.py.
    seller_id: str | None = None
    seller_rating: float | None = None  # 0–1
    seller_sales_count: int | None = None
    # Populated by the matcher; 0.0 until then.
    match_score: float = 0.0
    match_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.sold_at.tzinfo is None:
            # Naive datetimes silently compare wrong against aware ones and
            # would corrupt every freshness weight.
            object.__setattr__(self, "sold_at", self.sold_at.replace(tzinfo=timezone.utc))

    @property
    def total_price(self) -> Decimal:
        """Item plus shipping.

        Shipping is part of what a buyer paid, and marketplaces differ in whether
        it is bundled. Comparing a free-shipping listing against a
        `$20 + $12 postage` listing on item price alone systematically
        under-prices the latter.
        """
        return self.price + (self.shipping or Decimal("0"))

    def age_days(self, now: datetime | None = None) -> float:
        reference = now or datetime.now(timezone.utc)
        return max(0.0, (reference - self.sold_at).total_seconds() / 86400)

    def with_score(self, score: float, reasons: tuple[str, ...] = ()) -> "Comp":
        return replace(self, match_score=score, match_reasons=reasons)

    def dedupe_signature(self) -> str:
        """Fingerprint for near-duplicate detection within one provider."""
        from comps.normalize import normalise_text

        return f"{self.provider.value}:{normalise_text(self.title)}"


@dataclass(frozen=True)
class ProviderHealth:
    name: str
    available: bool
    configured: bool
    latency_ms: float | None = None
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.available and self.configured


@dataclass(frozen=True)
class PriceEvidence:
    """Robust statistics over a set of matched comps."""

    count: int
    effective_count: float              # freshness-decayed, weight-adjusted
    median: Decimal
    trimmed_mean: Decimal
    weighted_mean: Decimal
    p25: Decimal
    p75: Decimal
    iqr: Decimal
    mad: Decimal                        # median absolute deviation
    ci_low: Decimal
    ci_high: Decimal
    outliers_removed: int
    oldest_sale: datetime | None = None
    newest_sale: datetime | None = None
    providers: tuple[Marketplace, ...] = ()

    @property
    def dispersion(self) -> float:
        """MAD / median. A robust coefficient of variation.

        High dispersion means the comps disagree, which should widen the
        reported range and lower confidence rather than being averaged away.
        """
        if self.median <= 0:
            return 0.0
        return float(self.mad / self.median)


@dataclass(frozen=True)
class ValuationPrices:
    """The four user-facing figures, derived from `PriceEvidence`."""

    quick_sale: Decimal
    expected: Decimal
    suggested_resale: Decimal
    collector: Decimal

    @property
    def coherent(self) -> bool:
        return self.quick_sale <= self.expected <= self.suggested_resale <= self.collector


class CompsStatus(str, Enum):
    """Why a result looks the way it does.

    Distinct statuses rather than an empty list, because the *reason* determines
    what the UI should say and whether it is worth retrying.
    """

    OK = "ok"
    INSUFFICIENT_COMPS = "insufficient_comps"
    NOT_SEARCHABLE = "not_searchable"        # identity too thin to query
    NO_PROVIDERS = "no_providers"            # none configured/enabled
    DISABLED = "disabled"                    # feature flag off
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class CompsResult:
    """Everything the engine returns for one lookup."""

    status: CompsStatus
    identity: ItemIdentity
    comps: tuple[Comp, ...] = ()
    evidence: PriceEvidence | None = None
    prices: ValuationPrices | None = None
    providers_queried: tuple[str, ...] = ()
    providers_failed: tuple[str, ...] = ()
    cache_hit: bool = False
    latency_ms: float = 0.0
    window_days: int = 90
    notes: tuple[str, ...] = ()

    @property
    def has_evidence(self) -> bool:
        """Whether this result may be presented as evidence-backed.

        The single gate on the honesty invariant: `valuation_source` is only
        allowed to read "comps" when this is True.
        """
        return self.status is CompsStatus.OK and self.evidence is not None

    def sample(self, limit: int = 5) -> tuple[Comp, ...]:
        """Best-matching comps to show the user.

        Exists so a valuation is *auditable* — the user can click through and
        verify the claim. That is the entire trust proposition.
        """
        return tuple(sorted(self.comps, key=lambda c: -c.match_score)[:limit])


@dataclass
class ProviderQuery:
    """A normalised request handed to every provider."""

    identity: ItemIdentity
    window_days: int = 90
    limit: int = 50
    sold_only: bool = True
    tags: list[str] = field(default_factory=list)
