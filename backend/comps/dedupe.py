"""Duplicate detection.

The same physical item shows up repeatedly: relisted after failing to sell,
cross-posted to several marketplaces, or returned twice by an aggregating
provider.

This matters more than it sounds. Relists cluster at *the price that did not
work* — an item listed three times at $80 and finally sold at $55 contributes
three $80 observations and one $55 one if left undeduplicated, dragging the
median toward a price nobody actually paid. Deduplication is therefore an
accuracy control, not just hygiene.

Three passes, cheapest first, so the expensive comparison only runs on what
survives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from comps import normalize
from comps.models import Comp, Marketplace

log = logging.getLogger("snapworth.comps.dedupe")

# Pass 2 — relists within one provider.
#
# The discriminator is **seller identity**, not title/price similarity. A relist
# is one seller re-posting an item that did not sell; ten different sellers
# moving a common item at a similar price in the same week are ten genuine data
# points, and collapsing them would gut the sample for exactly the fungible
# goods that thrift resale is full of.
#
# So: when both comps expose a seller, a *differing* seller means "not a relist"
# regardless of how similar the listings look. Only when the seller is unknown
# on both sides do we fall back to the title/price heuristic — and there it is
# deliberately strict, because the evidence is much weaker.
RELIST_TITLE_SIMILARITY = 0.90
RELIST_PRICE_TOLERANCE = Decimal("0.02")      # 2%
RELIST_WINDOW_DAYS = 7

# Without a seller id we require an exact title match and near-exact price.
# Two different sellers independently choosing the same wording *and* the same
# price to the cent is rare; the same seller reusing their own listing is not.
RELIST_BLIND_PRICE_TOLERANCE = Decimal("0.005")   # 0.5%

# Safety valve. If the relist pass wants to remove more than this share of one
# provider's results, the "duplicates" are far more likely a fungible-item
# cluster than genuine relists, and removing them would destroy the sample.
MAX_RELIST_REMOVAL_RATIO = 0.4

# Pass 3 — cross-provider. Looser on title (different marketplaces enforce
# different title conventions) but tighter reasoning: identical price across two
# sites for the same normalised item is rarely coincidence.
CROSS_TITLE_SIMILARITY = 0.85
CROSS_PRICE_TOLERANCE = Decimal("0.05")       # 5%

# Which provider's copy to keep when the same sale appears twice. Ordered by
# data quality — see MARKETPLACE_RELIABILITY.
_PROVIDER_PRECEDENCE = [
    Marketplace.STOCKX, Marketplace.GOAT, Marketplace.CHRONO24,
    Marketplace.DISCOGS, Marketplace.EBAY, Marketplace.REVERB,
    Marketplace.GRAILED, Marketplace.MERCARI, Marketplace.LOCAL,
    Marketplace.ETSY, Marketplace.FACEBOOK, Marketplace.FIXTURE,
]


@dataclass(frozen=True)
class DedupeReport:
    kept: tuple[Comp, ...]
    removed: int
    exact_removed: int = 0
    relist_removed: int = 0
    cross_removed: int = 0


def _precedence(comp: Comp) -> int:
    try:
        return _PROVIDER_PRECEDENCE.index(comp.provider)
    except ValueError:
        return len(_PROVIDER_PRECEDENCE)


def _title_similarity(left: str, right: str) -> float:
    """Jaccard over normalised tokens.

    Symmetric here — unlike matching, where asymmetry is deliberate — because
    two listings of the *same* item should describe it about equally fully. A
    large asymmetry is itself evidence they are different items.
    """
    a = normalize.token_set(left).all_tokens
    b = normalize.token_set(right).all_tokens
    return normalize.jaccard(a, b)


def _price_close(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    if left <= 0 or right <= 0:
        return False
    larger = max(left, right)
    return abs(left - right) / larger <= tolerance


def _is_relist(candidate: Comp, kept: Comp) -> bool:
    """Whether `candidate` is the same listing as `kept`, re-posted.

    Seller identity is decisive when available. Two listings from *different*
    sellers are never a relist however alike they look — that is the fungible
    goods case, and merging it would silently shrink the comp set.
    """
    if abs((candidate.sold_at - kept.sold_at).days) > RELIST_WINDOW_DAYS:
        return False

    both_sellers_known = candidate.seller_id and kept.seller_id
    if both_sellers_known and candidate.seller_id != kept.seller_id:
        return False                    # different sellers ⇒ different sales

    if both_sellers_known:
        # Same seller, same window: a price move is normal on a relist, so the
        # title carries the decision.
        return _title_similarity(candidate.title, kept.title) >= RELIST_TITLE_SIMILARITY

    # Seller unknown: much weaker evidence, so demand near-exact agreement.
    if not _price_close(candidate.total_price, kept.total_price,
                        RELIST_BLIND_PRICE_TOLERANCE):
        return False
    return _title_similarity(candidate.title, kept.title) >= 0.99


def _better(a: Comp, b: Comp) -> Comp:
    """Pick the copy to keep: higher provider precedence, then better match,
    then more recent."""
    if _precedence(a) != _precedence(b):
        return a if _precedence(a) < _precedence(b) else b
    if a.match_score != b.match_score:
        return a if a.match_score > b.match_score else b
    return a if a.sold_at >= b.sold_at else b


def deduplicate(comps: list[Comp]) -> DedupeReport:
    """Remove exact, relisted and cross-posted duplicates."""
    if not comps:
        return DedupeReport(kept=(), removed=0)

    # ── Pass 1: exact — (provider, external_id) ─────────────────────────────
    by_id: dict[tuple[Marketplace, str], Comp] = {}
    for comp in comps:
        key = (comp.provider, comp.external_id)
        existing = by_id.get(key)
        by_id[key] = _better(existing, comp) if existing else comp
    stage1 = list(by_id.values())
    exact_removed = len(comps) - len(stage1)

    # ── Pass 2: relists within one provider ─────────────────────────────────
    # Bucketing by provider first keeps this O(n²) only within a provider's own
    # results rather than across the whole fan-out.
    by_provider: dict[Marketplace, list[Comp]] = {}
    for comp in stage1:
        by_provider.setdefault(comp.provider, []).append(comp)

    stage2: list[Comp] = []
    relist_removed = 0
    for provider_comps in by_provider.values():
        survivors: list[Comp] = []
        removed_here = 0
        # Floor of 2: the fungible-cluster risk only arises with many similar
        # comps. On a small set a genuine duplicate must still be removable,
        # and a bare ratio would round to zero and disable the pass entirely.
        max_removable = max(2, int(len(provider_comps) * MAX_RELIST_REMOVAL_RATIO))
        for comp in sorted(provider_comps, key=lambda c: -c.sold_at.timestamp()):
            duplicate_of = None
            if removed_here < max_removable:
                for index, kept in enumerate(survivors):
                    if _is_relist(comp, kept):
                        duplicate_of = index
                        break
            if duplicate_of is None:
                survivors.append(comp)
            else:
                survivors[duplicate_of] = _better(survivors[duplicate_of], comp)
                removed_here += 1
                relist_removed += 1
        stage2.extend(survivors)

    # ── Pass 3: cross-provider ──────────────────────────────────────────────
    stage3: list[Comp] = []
    cross_removed = 0
    for comp in sorted(stage2, key=_precedence):
        duplicate_of = None
        for index, kept in enumerate(stage3):
            if kept.provider == comp.provider:
                continue
            if not _price_close(comp.total_price, kept.total_price,
                                CROSS_PRICE_TOLERANCE):
                continue
            if _title_similarity(comp.title, kept.title) >= CROSS_TITLE_SIMILARITY:
                duplicate_of = index
                break
        if duplicate_of is None:
            stage3.append(comp)
        else:
            stage3[duplicate_of] = _better(stage3[duplicate_of], comp)
            cross_removed += 1

    stage3.sort(key=lambda c: (-c.match_score, -c.sold_at.timestamp()))
    total_removed = len(comps) - len(stage3)
    if total_removed:
        log.debug("deduplicated comps", extra={
            "removed": total_removed, "exact": exact_removed,
            "relist": relist_removed, "cross": cross_removed})

    return DedupeReport(
        kept=tuple(stage3), removed=total_removed,
        exact_removed=exact_removed, relist_removed=relist_removed,
        cross_removed=cross_removed,
    )
