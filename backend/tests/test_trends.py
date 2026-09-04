"""GET /trends — anonymous aggregates, and the floor that keeps them anonymous."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify  # noqa: E402
from cache import InMemoryCache, ResilientCache  # noqa: E402


@pytest.fixture
def cache():
    c = ResilientCache(None, InMemoryCache())
    notify.configure(c)
    yield c


async def seed(cache, days_ago: int, cats: dict, brands: dict, finds=(), scans: int = 0):
    day = notify._day(datetime.now(timezone.utc) - timedelta(days=days_ago))
    await cache.set(notify._stat_key(day, "top"),
                    json.dumps({"cats": cats, "brands": brands, "finds": list(finds)}), 600)
    if scans:
        await cache.set(notify._stat_key(day, "scans_free"), str(scans), 600)


def find(name, category, lo, hi):
    return {"n": name, "c": category, "lo": lo, "hi": hi, "t": "free"}


class TestFloor:
    @pytest.mark.asyncio
    async def test_rows_below_the_floor_are_withheld(self, cache):
        # clothing clears the floor; shoes (4) does not, and a lone brand never does.
        await seed(cache, 0, {"clothing": 9, "shoes": 4}, {"Nike": 6, "Ferrari": 1}, scans=13)
        payload = await notify.trends(is_pro=False)
        assert [r["name"] for r in payload["categories"]] == ["clothing"]
        assert [r["name"] for r in payload["brands"]] == ["Nike"]
        assert payload["scans"] == 13

    @pytest.mark.asyncio
    async def test_direction_only_against_a_week_that_also_cleared_the_floor(self, cache):
        await seed(cache, 0, {"clothing": 12}, {})
        await seed(cache, 8, {"clothing": 6}, {})       # last week, above the floor
        await seed(cache, 9, {"home": 2}, {})           # below it: no direction for home
        await seed(cache, 1, {"home": 8}, {})
        rows = {r["name"]: r for r in (await notify.trends(is_pro=False))["categories"]}
        assert rows["clothing"]["change_pct"] == 100
        assert "change_pct" not in rows["home"]


class TestTierSplit:
    @pytest.mark.asyncio
    async def test_free_gets_counts_only(self, cache):
        await seed(cache, 0, {"clothing": 9}, {"Nike": 6},
                   [find("Carhartt Detroit Jacket", "clothing", 60, 100)] * 3)
        payload = await notify.trends(is_pro=False)
        assert "notable_finds" not in payload
        assert all("average_estimate" not in r for r in payload["categories"])

    @pytest.mark.asyncio
    async def test_pro_gets_averages_and_notable_finds(self, cache):
        finds = [find("Le Creuset 5.5qt", "home", 120, 220),
                 find("KitchenAid Mixer", "home", 100, 180),
                 find("Pyrex set", "home", 40, 80)]
        await seed(cache, 0, {"home": 9}, {"Le Creuset": 6}, finds)
        payload = await notify.trends(is_pro=True)
        (home,) = payload["categories"]
        assert home["average_estimate"] == 123        # (170 + 140 + 60) / 3
        assert [f["name"] for f in payload["notable_finds"]] == \
            ["Le Creuset 5.5qt", "KitchenAid Mixer", "Pyrex set"]
        assert set(payload["notable_finds"][0]) == {"name", "category", "low", "high"}, \
            "a find is an item and a price — never a device, never a time"

    @pytest.mark.asyncio
    async def test_an_average_needs_three_finds(self, cache):
        await seed(cache, 0, {"home": 9}, {},
                   [find("Le Creuset", "home", 120, 220), find("Pyrex", "home", 40, 80)])
        (home,) = (await notify.trends(is_pro=True))["categories"]
        assert "average_estimate" not in home

    @pytest.mark.asyncio
    async def test_more_rows_for_pro(self, cache):
        cats = {name: 9 for name in
                ["clothing", "shoes", "home", "books", "toys", "sports", "electronics"]}
        await seed(cache, 0, cats, {})
        assert len((await notify.trends(is_pro=False))["categories"]) == notify.TRENDS_FREE_ROWS
        await cache.delete(f"{notify.TRENDS_CACHE_KEY}:pro")
        assert len((await notify.trends(is_pro=True))["categories"]) == notify.TRENDS_PRO_ROWS


class TestCaching:
    @pytest.mark.asyncio
    async def test_each_tier_is_cached_separately(self, cache):
        await seed(cache, 0, {"clothing": 9}, {})
        first = await notify.trends(is_pro=False)
        # A later scan does not change what the cache already answered.
        await seed(cache, 0, {"clothing": 99}, {})
        assert (await notify.trends(is_pro=False)) == first
        # Pro has its own entry, computed fresh from the new numbers.
        assert (await notify.trends(is_pro=True))["categories"][0]["count"] == 99

    @pytest.mark.asyncio
    async def test_no_data_is_an_empty_answer_not_an_error(self, cache):
        payload = await notify.trends(is_pro=True)
        assert payload["scans"] == 0
        assert payload["categories"] == [] and payload["brands"] == []
