"""Sell-through (#43): how fast an item moves, stated only when the sales support it."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cache import InMemoryCache  # noqa: E402
from comps import aggregate as agg  # noqa: E402
from comps.cache import CompsCache  # noqa: E402
from comps.models import CompsStatus  # noqa: E402
from tests.test_comps_engine import NOW, build_engine, comp, identity, many  # noqa: E402

MIN = agg.SELL_THROUGH_MIN_COMPS


def timed(n, *, wait_days=9, days_ago=5, now=NOW):
    """`n` independent sales, each listed `wait_days` before it sold."""
    out = []
    for i in range(n):
        c = comp(100 + i, external_id=str(i), days_ago=days_ago + i, seller_id=f"s{i}")
        c = replace(c, sold_at=now - timedelta(days=days_ago + i))
        out.append(replace(c, listed_at=c.sold_at - timedelta(days=wait_days + i % 3)))
    return out


def recent(n):
    """`timed`, relative to the real clock — the engine windows against it."""
    return timed(n, now=datetime.now(timezone.utc))


class TestDaysToSale:
    def test_is_the_gap_between_listing_and_sale(self):
        c = timed(1, wait_days=9)[0]
        assert c.days_to_sale == 9

    def test_is_unknown_without_a_listing_date(self):
        assert comp(100).days_to_sale is None

    def test_a_listing_after_the_sale_is_unknown_not_instant(self):
        c = comp(100)
        assert replace(c, listed_at=c.sold_at + timedelta(days=1)).days_to_sale is None

    def test_a_naive_listing_date_is_read_as_utc(self):
        c = comp(100)
        naive = replace(c, listed_at=(c.sold_at - timedelta(days=2)).replace(tzinfo=None))
        assert naive.days_to_sale == 2


class TestSellThrough:
    def test_absent_below_the_threshold(self):
        """Not zero and not "unknown": no claim at all."""
        assert agg.sell_through(timed(MIN - 1), window_days=90, now=NOW) is None

    def test_present_at_the_threshold(self):
        result = agg.sell_through(timed(MIN), window_days=90, now=NOW)
        assert result is not None
        assert result.sales_in_window == MIN
        assert result.timed_count == MIN
        assert 9 <= (result.median_days_to_sale or 0) <= 11

    def test_counts_only_sales_inside_the_window(self):
        recent = timed(MIN)
        old = [replace(c, external_id=f"old{i}", sold_at=NOW - timedelta(days=200))
               for i, c in enumerate(timed(5))]
        result = agg.sell_through(recent + old, window_days=90, now=NOW)
        assert result is not None and result.sales_in_window == MIN
        # Old sales cannot lift a thin window over the threshold either.
        assert agg.sell_through(recent[:MIN - 1] + old, window_days=90, now=NOW) is None

    def test_speed_needs_enough_listing_dates(self):
        """A count is still worth stating when the speed is not."""
        comps = timed(MIN - 1) + [comp(150, external_id="x", seller_id="x")] * 3
        result = agg.sell_through(comps, window_days=90, now=NOW)
        assert result is not None
        assert result.sales_in_window == MIN + 2
        assert result.median_days_to_sale is None
        assert result.timed_count == MIN - 1

    def test_weekly_rate(self):
        result = agg.sell_through(timed(12), window_days=84, now=NOW)
        assert result is not None and result.sales_per_week == 1.0


class TestEngine:
    def test_an_ok_lookup_carries_sell_through(self):
        result = asyncio.run(build_engine(recent(12)).lookup(identity()))
        assert result.status is CompsStatus.OK
        assert result.sell_through is not None
        assert result.sell_through.sales_in_window == len(result.comps)
        assert result.sell_through.median_days_to_sale is not None

    def test_too_few_sales_for_liquidity_still_prices(self):
        comps = many(n=agg.MIN_COMPS)
        result = asyncio.run(build_engine(comps).lookup(identity()))
        assert result.status is CompsStatus.OK and result.has_evidence
        assert result.sell_through is None

    def test_insufficient_comps_has_no_sell_through(self):
        result = asyncio.run(build_engine([]).lookup(identity()))
        assert result.status is CompsStatus.INSUFFICIENT_COMPS
        assert result.sell_through is None


class TestCacheRoundTrip:
    def test_seller_and_listing_date_survive_the_cache(self):
        """`seller_id` was dropped on the way into the cache.

        Dedupe reads it to tell a relist (same seller) from ten sellers moving
        the same item, so every cache hit fell back to title+price matching —
        the fungible-goods collapse `docs/COMPS-ENGINE.md` records fixing.
        """
        cache = CompsCache(backend=InMemoryCache())
        original = timed(3)
        asyncio.run(cache.put(identity(), 90, original))
        restored = asyncio.run(cache.get(identity(), 90))
        assert restored is not None
        assert [c.seller_id for c in restored] == [c.seller_id for c in original]
        assert [c.listed_at for c in restored] == [c.listed_at for c in original]
