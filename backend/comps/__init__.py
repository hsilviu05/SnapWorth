"""Comparable-sales engine.

Pipeline:

    ItemIdentity → cache → provider fan-out → rank → dedupe → aggregate → evidence

See docs/COMPS-ARCHITECTURE.md for the full design. Nothing here performs
marketplace scraping, and no bundled provider requires credentials — real
integrations are gated on the legal review documented in that file.
"""

from comps.models import (  # noqa: F401
    Comp,
    CompsResult,
    CompsStatus,
    Condition,
    ItemIdentity,
    Marketplace,
    PriceEvidence,
    ProviderHealth,
    ProviderQuery,
    ValuationPrices,
)

__all__ = [
    "Comp", "CompsResult", "CompsStatus", "Condition", "ItemIdentity",
    "Marketplace", "PriceEvidence", "ProviderHealth", "ProviderQuery",
    "ValuationPrices",
]
