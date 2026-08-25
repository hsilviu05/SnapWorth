"""Internal knowledge base: brands, aliases, model mappings, category priors.

Purpose
-------
The vision model returns free text. "patagonia", "Patagonia Inc.", "PATAGONIA"
and the misspelling "Patagucci" all mean one brand, and a comps query built from
the raw string will miss listings that spell it differently. This catalog is the
normalisation authority that sits between identification and retrieval.

It also carries pricing-relevant priors that a general model does not reliably
know: which brands hold value, which are heavily counterfeited (so authenticity
matters more), and which categories are collectible (so variant precision
matters more than condition).

Why a hand-curated table rather than an embedding
-------------------------------------------------
Brand resolution needs to be *exact and auditable*. "Nike" and "Mike" are one
character apart and an embedding will happily rate them similar; a lookup table
will not. When a brand resolves wrongly, every downstream comp is wrong, so this
is precisely the wrong place for fuzzy inference. Fuzzy matching is available
via `resolve_fuzzy` but is bounded, explicit, and only reached after exact and
alias lookup both fail.

Scope
-----
Deliberately seeded rather than exhaustive. A few hundred entries covering the
brands that dominate thrift resale is far more valuable than a scraped list of
50,000, because every entry here is a claim we are making about the market.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass
from enum import Enum

from comps import normalize

log = logging.getLogger("snapworth.comps.catalog")


class BrandTier(str, Enum):
    """How a brand behaves in the secondhand market.

    Drives confidence, not price directly: a luxury item's value depends far
    more on authenticity and condition than a mass-market item's does, so the
    tier changes how much weight those signals carry.
    """

    LUXURY = "luxury"              # Hermès, Rolex — authenticity dominates
    PREMIUM = "premium"            # Patagonia, Arc'teryx — strong resale floor
    MAINSTREAM = "mainstream"      # Nike, Levi's — deep liquid market
    VALUE = "value"                # H&M, Primark — minimal resale value
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Brand:
    canonical: str
    tier: BrandTier = BrandTier.UNKNOWN
    aliases: frozenset[str] = frozenset()
    # Frequently counterfeited. Raises the bar on authenticity before comps are
    # trusted, because a replica priced against authentic comps is dangerously,
    # confidently wrong.
    counterfeit_risk: bool = False
    categories: frozenset[str] = frozenset()
    # Known model lines, used to pull a model out of a free-text item name.
    models: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CategoryProfile:
    """Per-category behaviour used by matching and aggregation."""

    name: str
    collectible: bool = False
    # Long-tail markets need a wider search window to find any sales at all.
    low_liquidity: bool = False
    # Variant precision matters more than condition here (sneakers, vinyl).
    variant_sensitive: bool = False
    typical_window_days: int = 90


# ── Seed data ────────────────────────────────────────────────────────────────
#
# Chosen for thrift-resale relevance, not brand size. These are the names that
# actually appear on scanned items.

_BRANDS: list[Brand] = [
    Brand("Patagonia", BrandTier.PREMIUM, frozenset({"patagucci"}),
          categories=frozenset({"clothing"}),
          models=frozenset({"better sweater", "synchilla", "nano puff",
                            "retro-x", "houdini", "torrentshell"})),
    Brand("The North Face", BrandTier.PREMIUM, frozenset({"tnf", "north face", "northface"}),
          categories=frozenset({"clothing"}),
          models=frozenset({"nuptse", "denali", "thermoball", "resolve"})),
    Brand("Arc'teryx", BrandTier.PREMIUM, frozenset({"arcteryx", "arc teryx", "arcterix"}),
          categories=frozenset({"clothing"}),
          models=frozenset({"beta", "atom", "gamma", "alpha sv", "cerium"})),
    Brand("Carhartt", BrandTier.MAINSTREAM, frozenset({"carhart", "carhartt wip"}),
          categories=frozenset({"clothing"}),
          models=frozenset({"detroit", "chore coat", "double knee", "active jacket"})),
    Brand("Levi's", BrandTier.MAINSTREAM, frozenset({"levis", "levi", "levi strauss"}),
          categories=frozenset({"clothing"}),
          models=frozenset({"501", "505", "511", "550", "big e", "trucker"})),
    Brand("Nike", BrandTier.MAINSTREAM, frozenset({"nike sb", "nikelab"}),
          counterfeit_risk=True, categories=frozenset({"shoes", "clothing"}),
          models=frozenset({"air max", "air force 1", "dunk", "blazer",
                            "jordan", "vapormax", "pegasus", "cortez"})),
    Brand("Adidas", BrandTier.MAINSTREAM, frozenset({"adidas originals", "addidas"}),
          counterfeit_risk=True, categories=frozenset({"shoes", "clothing"}),
          models=frozenset({"samba", "gazelle", "stan smith", "superstar",
                            "ultraboost", "campus", "spezial"})),
    Brand("New Balance", BrandTier.MAINSTREAM, frozenset({"nb", "newbalance"}),
          categories=frozenset({"shoes"}),
          models=frozenset({"990", "991", "992", "993", "550", "574", "2002r"})),
    Brand("Dr. Martens", BrandTier.MAINSTREAM,
          frozenset({"dr martens", "doc martens", "docs", "drmartens"}),
          categories=frozenset({"shoes"}),
          models=frozenset({"1460", "1461", "2976", "jadon"})),
    Brand("Birkenstock", BrandTier.MAINSTREAM, frozenset({"birkenstocks", "birks"}),
          categories=frozenset({"shoes"}),
          models=frozenset({"arizona", "boston", "gizeh", "madrid"})),
    Brand("Ralph Lauren", BrandTier.PREMIUM,
          frozenset({"polo ralph lauren", "polo", "rl", "rrl"}),
          counterfeit_risk=True, categories=frozenset({"clothing"})),
    Brand("Tommy Hilfiger", BrandTier.MAINSTREAM, frozenset({"tommy"}),
          counterfeit_risk=True, categories=frozenset({"clothing"})),
    Brand("Stone Island", BrandTier.LUXURY, frozenset({"stoneisland"}),
          counterfeit_risk=True, categories=frozenset({"clothing"})),
    Brand("Supreme", BrandTier.LUXURY, frozenset(),
          counterfeit_risk=True, categories=frozenset({"clothing"})),
    Brand("Gucci", BrandTier.LUXURY, frozenset(), counterfeit_risk=True,
          categories=frozenset({"accessories", "clothing", "shoes"})),
    Brand("Louis Vuitton", BrandTier.LUXURY, frozenset({"lv", "louisvuitton"}),
          counterfeit_risk=True, categories=frozenset({"accessories"})),
    Brand("Chanel", BrandTier.LUXURY, frozenset(), counterfeit_risk=True,
          categories=frozenset({"accessories"})),
    Brand("Prada", BrandTier.LUXURY, frozenset(), counterfeit_risk=True,
          categories=frozenset({"accessories"})),
    Brand("Hermès", BrandTier.LUXURY, frozenset({"hermes"}), counterfeit_risk=True,
          categories=frozenset({"accessories"})),
    Brand("Coach", BrandTier.PREMIUM, frozenset(), counterfeit_risk=True,
          categories=frozenset({"accessories"})),
    Brand("Rolex", BrandTier.LUXURY, frozenset(), counterfeit_risk=True,
          categories=frozenset({"accessories"}),
          models=frozenset({"submariner", "datejust", "daytona", "explorer"})),
    Brand("Seiko", BrandTier.PREMIUM, frozenset(), categories=frozenset({"accessories"}),
          models=frozenset({"skx", "seiko 5", "presage", "prospex"})),
    Brand("Omega", BrandTier.LUXURY, frozenset(), counterfeit_risk=True,
          categories=frozenset({"accessories"}),
          models=frozenset({"speedmaster", "seamaster"})),
    Brand("Apple", BrandTier.PREMIUM, frozenset(), categories=frozenset({"electronics"}),
          models=frozenset({"iphone", "ipad", "macbook", "airpods", "watch"})),
    Brand("Sony", BrandTier.PREMIUM, frozenset(), categories=frozenset({"electronics"}),
          models=frozenset({"wh-1000xm3", "wh-1000xm4", "wh-1000xm5", "playstation"})),
    Brand("Bose", BrandTier.PREMIUM, frozenset(), categories=frozenset({"electronics"}),
          models=frozenset({"quietcomfort", "soundlink"})),
    Brand("Nintendo", BrandTier.PREMIUM, frozenset(),
          categories=frozenset({"electronics", "toys"}),
          models=frozenset({"switch", "game boy", "nes", "snes", "n64"})),
    Brand("Le Creuset", BrandTier.PREMIUM, frozenset({"lecreuset"}),
          categories=frozenset({"home"}),
          models=frozenset({"dutch oven", "braiser"})),
    Brand("Pyrex", BrandTier.MAINSTREAM, frozenset(), categories=frozenset({"home"})),
    Brand("KitchenAid", BrandTier.PREMIUM, frozenset({"kitchen aid"}),
          categories=frozenset({"home"}),
          models=frozenset({"artisan", "classic", "professional"})),
    Brand("Lululemon", BrandTier.PREMIUM, frozenset({"lulu"}),
          counterfeit_risk=True, categories=frozenset({"clothing"}),
          models=frozenset({"align", "wunder under", "define", "scuba"})),
    Brand("Stanley", BrandTier.MAINSTREAM, frozenset(),
          categories=frozenset({"home", "sports"}),
          models=frozenset({"quencher", "adventure"})),
    Brand("Zara", BrandTier.VALUE, frozenset(), categories=frozenset({"clothing"})),
    Brand("H&M", BrandTier.VALUE, frozenset({"h and m", "hm", "handm"}),
          categories=frozenset({"clothing"})),
    Brand("Primark", BrandTier.VALUE, frozenset(), categories=frozenset({"clothing"})),
    Brand("Shein", BrandTier.VALUE, frozenset(), categories=frozenset({"clothing"})),
]

_CATEGORIES: list[CategoryProfile] = [
    CategoryProfile("clothing", typical_window_days=90),
    CategoryProfile("shoes", variant_sensitive=True, typical_window_days=90),
    CategoryProfile("accessories", variant_sensitive=True, typical_window_days=120),
    CategoryProfile("electronics", typical_window_days=60),
    CategoryProfile("home", typical_window_days=120),
    CategoryProfile("books", collectible=True, typical_window_days=180),
    CategoryProfile("sports", typical_window_days=90),
    CategoryProfile("toys", collectible=True, typical_window_days=150),
    CategoryProfile("furniture", low_liquidity=True, typical_window_days=180),
    CategoryProfile("collectibles", collectible=True, low_liquidity=True,
                    variant_sensitive=True, typical_window_days=180),
    CategoryProfile("other", typical_window_days=120),
]


class Catalog:
    """Indexed lookup over the seed data."""

    def __init__(self, brands: list[Brand] | None = None,
                 categories: list[CategoryProfile] | None = None) -> None:
        self._brands = list(brands if brands is not None else _BRANDS)
        self._categories = {
            c.name: c for c in (categories if categories is not None else _CATEGORIES)
        }
        self._index: dict[str, Brand] = {}
        for brand in self._brands:
            for key in {brand.canonical, *brand.aliases}:
                normalised = normalize.normalise_text(key)
                if normalised:
                    self._index[normalised] = brand

    # ── Brands ───────────────────────────────────────────────────────────────

    @property
    def brands(self) -> list[Brand]:
        return list(self._brands)

    def resolve(self, raw: str | None) -> Brand | None:
        """Exact or alias lookup. No fuzziness — see module docstring."""
        if not raw:
            return None
        return self._index.get(normalize.normalise_text(raw))

    def resolve_fuzzy(self, raw: str | None, *, cutoff: float = 0.86) -> Brand | None:
        """Bounded fuzzy fallback for OCR noise and misspellings.

        Only reached after exact and alias lookup fail. The cutoff is high on
        purpose: a wrong brand resolution corrupts every comp that follows, so
        returning None is strictly better than a plausible guess.
        """
        exact = self.resolve(raw)
        if exact:
            return exact
        if not raw:
            return None
        needle = normalize.normalise_text(raw)
        if len(needle) < 3:
            return None            # too short for similarity to mean anything
        matches = difflib.get_close_matches(needle, self._index.keys(), n=1, cutoff=cutoff)
        if not matches:
            return None
        brand = self._index[matches[0]]
        log.debug("fuzzy brand resolution", extra={"input": raw, "resolved": brand.canonical})
        return brand

    def canonical_brand(self, raw: str | None) -> str | None:
        brand = self.resolve_fuzzy(raw)
        return brand.canonical if brand else (raw.strip() if raw else None)

    def extract_model(self, brand: Brand | None, text: str | None) -> str | None:
        """Pull a known model line out of free text.

        Longest match wins, so "air max 97" resolves to "air max" rather than a
        shorter overlapping entry, and the trailing designator is preserved by
        the caller's token handling.
        """
        if not brand or not text or not brand.models:
            return None
        haystack = normalize.normalise_text(text)
        found = [m for m in brand.models if normalize.normalise_text(m) in haystack]
        return max(found, key=len) if found else None

    # ── Categories ───────────────────────────────────────────────────────────

    def category(self, name: str | None) -> CategoryProfile:
        key = (name or "other").strip().lower()
        return self._categories.get(key, self._categories["other"])

    @property
    def categories(self) -> list[CategoryProfile]:
        return list(self._categories.values())

    # ── Derived views ────────────────────────────────────────────────────────

    def luxury_brands(self) -> list[Brand]:
        return [b for b in self._brands if b.tier is BrandTier.LUXURY]

    def counterfeit_risk_brands(self) -> list[Brand]:
        return [b for b in self._brands if b.counterfeit_risk]

    def requires_authentication(self, raw_brand: str | None) -> bool:
        """Whether authenticity should gate comps weighting for this brand.

        A replica priced against authentic comps is the single most damaging
        output this system can produce, so counterfeit-prone brands need an
        authenticity read before evidence is trusted.
        """
        brand = self.resolve_fuzzy(raw_brand)
        if brand is None:
            return False
        return brand.counterfeit_risk or brand.tier is BrandTier.LUXURY


catalog = Catalog()
