"""Comps caching.

Layered on the existing `cache.ResilientCache` rather than introducing a second
caching system — the failure-policy reasoning there (configured vs. connected,
fail-closed on `required`) was hard-won and must not be duplicated or diverged
from.

Comps use `required=False` throughout. That is a deliberate difference from
quota: a cache miss on comps costs a provider call, whereas a cache miss on
quota costs revenue. Comps degrade to a fetch; they never fail closed.

The negative cache matters as much as the positive one
------------------------------------------------------
Obscure items are exactly the ones that return nothing, and they are also the
ones users scan most often in a thrift shop — unbranded jumpers, no-name
homeware. Without a negative cache, every scan of an unidentifiable item
re-queries every provider and returns nothing again, which is how a rate limit
gets exhausted by traffic that can never produce a comp.

Serialisation
-------------
Comps are stored as JSON rather than pickle. Pickle would be faster and less
code, but a cache is an untrusted deserialisation surface: anything with Redis
write access could achieve RCE. JSON cannot.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from comps.models import Comp, Condition, ItemIdentity, Marketplace

log = logging.getLogger("snapworth.comps.cache")

CACHE_VERSION = "v1"

# Sentinel stored for a confirmed-empty lookup, distinguishing "we asked and
# there was nothing" from "we have never asked".
_NEGATIVE = "__none__"


def positive_key(identity: ItemIdentity, window_days: int) -> str:
    return f"{identity.cache_key()}:w{window_days}"


def negative_key(identity: ItemIdentity, window_days: int) -> str:
    return f"comps:neg:{CACHE_VERSION}:{identity.cache_key()}:w{window_days}"


def _comp_to_dict(comp: Comp) -> dict:
    return {
        "provider": comp.provider.value,
        "external_id": comp.external_id,
        "title": comp.title,
        "price": str(comp.price),
        "sold_at": comp.sold_at.isoformat(),
        "currency_original": comp.currency_original,
        "price_original": str(comp.price_original) if comp.price_original is not None else None,
        "condition": comp.condition.value if comp.condition else None,
        "shipping": str(comp.shipping) if comp.shipping is not None else None,
        "url": comp.url,
        "seller_rating": comp.seller_rating,
        "seller_sales_count": comp.seller_sales_count,
    }


def _comp_from_dict(raw: dict) -> Comp | None:
    """Rebuild a comp, returning None for anything malformed.

    A single corrupt entry must not poison the whole cached set — this is
    deserialising data that may have been written by an older deploy with a
    different shape.
    """
    try:
        return Comp(
            provider=Marketplace(raw["provider"]),
            external_id=str(raw["external_id"]),
            title=str(raw["title"]),
            price=Decimal(raw["price"]),
            sold_at=datetime.fromisoformat(raw["sold_at"]),
            currency_original=raw.get("currency_original", "USD"),
            price_original=(
                Decimal(raw["price_original"]) if raw.get("price_original") else None),
            condition=Condition(raw["condition"]) if raw.get("condition") else None,
            shipping=Decimal(raw["shipping"]) if raw.get("shipping") else None,
            url=raw.get("url"),
            seller_rating=raw.get("seller_rating"),
            seller_sales_count=raw.get("seller_sales_count"),
        )
    except Exception as exc:
        log.debug("dropping malformed cached comp: %s", exc)
        return None


@dataclass
class CompsCache:
    """Read-through cache for provider results.

    Stores *raw* provider output, before matching and aggregation. That is the
    expensive part (network) and the part that does not change; scoring and
    statistics are cheap, deterministic and depend on the query's condition and
    the current time, so re-running them per request is both correct and free.
    """

    backend: object                      # cache.ResilientCache or compatible
    ttl_seconds: int = 86_400
    negative_ttl_seconds: int = 21_600

    async def get(self, identity: ItemIdentity, window_days: int) -> list[Comp] | None:
        """Return cached comps, `[]` for a cached-empty, or None for a miss."""
        try:
            negative = await self.backend.get(negative_key(identity, window_days))
            if negative == _NEGATIVE:
                return []
            raw = await self.backend.get(positive_key(identity, window_days))
        except Exception as exc:
            # Cache trouble degrades to a fetch. Never fails the scan.
            log.warning("comps cache read failed: %s", exc)
            return None

        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("comps cache entry was not valid JSON, ignoring")
            return None
        if payload.get("version") != CACHE_VERSION:
            return None

        comps = [c for c in (_comp_from_dict(d) for d in payload.get("comps", [])) if c]
        return comps or None

    async def put(self, identity: ItemIdentity, window_days: int,
                  comps: list[Comp]) -> None:
        """Store provider results, or a negative marker when empty."""
        try:
            if not comps:
                await self.backend.set(
                    negative_key(identity, window_days), _NEGATIVE,
                    self.negative_ttl_seconds)
                return
            payload = json.dumps({
                "version": CACHE_VERSION,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "comps": [_comp_to_dict(c) for c in comps],
            })
            await self.backend.set(
                positive_key(identity, window_days), payload, self.ttl_seconds)
        except Exception as exc:
            log.warning("comps cache write failed: %s", exc)

    async def invalidate(self, identity: ItemIdentity, window_days: int) -> None:
        for key in (positive_key(identity, window_days),
                    negative_key(identity, window_days)):
            try:
                await self.backend.delete(key)
            except Exception:
                pass


class NullCompsCache:
    """No-op cache. Used when no backend is wired, and in tests."""

    async def get(self, identity: ItemIdentity, window_days: int) -> list[Comp] | None:
        return None

    async def put(self, identity: ItemIdentity, window_days: int,
                  comps: list[Comp]) -> None:
        return None

    async def invalidate(self, identity: ItemIdentity, window_days: int) -> None:
        return None
