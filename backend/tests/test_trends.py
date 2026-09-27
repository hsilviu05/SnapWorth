"""GET /trends — anonymous aggregates, and the floor that keeps them anonymous."""

from __future__ import annotations

import asyncio
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


# Enough different devices behind everything a fixture seeds, unless a test
# says otherwise — so the tests about the scan floor, the tiers and the window
# stay about those, and the device floor is tested on its own below.
DEVICES = ("dev-a", "dev-b", "dev-c")


# `trends()` windows both end YESTERDAY — today is excluded from the counts and
# from the ratio. It used to compare a partial today-plus-six against seven
# whole days, which leaned every category ▼ all day and recovered at midnight
# UTC. So fixtures start at days_ago=1; seeding day 0 puts data outside the
# window on purpose, not by accident.
#
# `devices=None` writes a day as the code before device tags did — no
# `cat_devices`, no `brand_devices`, no `d` on a find.
async def seed(cache, days_ago: int, cats: dict, brands: dict, finds=(), scans: int = 0,
               devices=DEVICES):
    day = notify._day(datetime.now(timezone.utc) - timedelta(days=days_ago))
    doc: dict = {"cats": cats, "brands": brands,
                 "finds": [f if devices is None else {**f, "d": list(devices)}
                           for f in finds]}
    if devices is not None:
        doc["cat_devices"] = {c: list(devices) for c in cats}
        doc["brand_devices"] = {b: list(devices) for b in brands}
    await cache.set(notify._stat_key(day, "top"), json.dumps(doc), 600)
    if scans:
        await cache.set(notify._stat_key(day, "scans_free"), str(scans), 600)


def find(name, category, lo, hi):
    return {"n": name, "c": category, "lo": lo, "hi": hi, "t": "free"}


class TestFloor:
    @pytest.mark.asyncio
    async def test_rows_below_the_floor_are_withheld(self, cache):
        # clothing clears the floor; shoes (4) does not, and a lone brand never does.
        await seed(cache, 1, {"clothing": 9, "shoes": 4}, {"Nike": 6, "Ferrari": 1}, scans=13)
        payload = await notify.trends(is_pro=False)
        assert [r["name"] for r in payload["categories"]] == ["clothing"]
        assert [r["name"] for r in payload["brands"]] == ["Nike"]
        assert payload["scans"] == 13

    @pytest.mark.asyncio
    async def test_direction_only_against_a_week_that_also_cleared_the_floor(self, cache):
        await seed(cache, 1, {"clothing": 12}, {})
        await seed(cache, 2, {"home": 8}, {})
        await seed(cache, 8, {"clothing": 6}, {})       # last week, above the floor
        await seed(cache, 9, {"home": 2}, {})           # below it: no direction for home
        rows = {r["name"]: r for r in (await notify.trends(is_pro=False))["categories"]}
        assert rows["clothing"]["change_pct"] == 100
        assert "change_pct" not in rows["home"]


class TestDeviceFloor:
    """Five scans used to be the whole floor, and at one to four real scans a
    day five scans of one label was a week's trend on every install. A row
    now also needs different devices behind it — TRENDS_MIN_CATEGORY_DEVICES
    for a category, TRENDS_MIN_BRAND_DEVICES for a brand — and a notable find
    needs TRENDS_MIN_FIND_DEVICES."""

    @pytest.mark.asyncio
    async def test_many_scans_from_one_device_are_not_a_trend(self, cache):
        await seed(cache, 1, {"clothing": 9}, {"spam.example free money": 9},
                   [find("Visit my shop", "clothing", 900, 1000)], devices=["dev-a"])
        payload = await notify.trends(is_pro=True)
        assert payload["categories"] == [] and payload["brands"] == []
        assert payload["notable_finds"] == []

    @pytest.mark.asyncio
    async def test_a_category_needs_two_devices_and_a_brand_three(self, cache):
        """Categories are a closed set and cannot carry a spam URL or a slur;
        a brand is free text read off a photo, and can."""
        await seed(cache, 1, {"clothing": 9}, {"Nike": 9}, devices=["dev-a", "dev-b"])
        payload = await notify.trends(is_pro=False)
        assert [(r["name"], r["count"]) for r in payload["categories"]] == [("clothing", 9)]
        assert payload["brands"] == []

        await seed(cache, 2, {"clothing": 1}, {"Nike": 1}, devices=["dev-c"])
        await cache.delete(f"{notify.TRENDS_CACHE_KEY}:free")
        payload = await notify.trends(is_pro=False)
        assert [(r["name"], r["count"]) for r in payload["brands"]] == [("Nike", 10)]

    @pytest.mark.asyncio
    async def test_one_device_scanning_a_brand_every_day_never_trends(self, cache):
        # Ten scans a day for five days is fifty scans and one device. A second
        # device puts clothing on the card, so it is the brand held back, not
        # the week.
        for days_ago in range(1, 6):
            await seed(cache, days_ago, {"clothing": 10}, {"Carhartt": 10}, devices=["dev-a"])
        await seed(cache, 6, {"clothing": 1}, {}, devices=["dev-b"])
        payload = await notify.trends(is_pro=False)
        assert [(r["name"], r["count"]) for r in payload["categories"]] == [("clothing", 51)]
        assert payload["brands"] == []

    @pytest.mark.asyncio
    async def test_devices_add_up_across_the_week(self, cache):
        # One device a day for three days is three devices; the same device on
        # three days is still one.
        for days_ago, device in ((1, "dev-a"), (2, "dev-b"), (3, "dev-c")):
            await seed(cache, days_ago, {"clothing": 3}, {"Nike": 2}, devices=[device])
        for days_ago in (4, 5, 6):
            await seed(cache, days_ago, {"shoes": 3}, {}, devices=["dev-z"])
        payload = await notify.trends(is_pro=False)
        assert [(r["name"], r["count"]) for r in payload["categories"]] == [("clothing", 9)]
        assert [r["name"] for r in payload["brands"]] == ["Nike"]

    @pytest.mark.asyncio
    async def test_a_notable_find_needs_three_devices(self, cache):
        # The jacket: three devices across the week. The shirt: two, which is
        # a category's floor and not a find's. The watch: one device, three
        # days running, and the most valuable thing scanned.
        for days_ago, device in ((1, "dev-a"), (2, "dev-b")):
            await seed(cache, days_ago, {"clothing": 3}, {},
                       [find("Carhartt Detroit Jacket", "clothing", 60, 100),
                        find("Pendleton Board Shirt", "clothing", 80, 150)], devices=[device])
        await seed(cache, 3, {"clothing": 3}, {},
                   [find("Carhartt Detroit Jacket", "clothing", 60, 100)], devices=["dev-c"])
        for days_ago in (4, 5, 6):
            await seed(cache, days_ago, {"accessories": 1}, {},
                       [find("Rolex Submariner", "accessories", 5000, 9000)], devices=["dev-z"])
        notable = (await notify.trends(is_pro=True))["notable_finds"]
        assert [f["name"] for f in notable] == ["Carhartt Detroit Jacket"]

    @pytest.mark.asyncio
    async def test_last_weeks_direction_needs_last_weeks_devices(self, cache):
        await seed(cache, 1, {"clothing": 12}, {})
        await seed(cache, 8, {"clothing": 6}, {}, devices=["dev-a"])
        (row,) = (await notify.trends(is_pro=False))["categories"]
        assert "change_pct" not in row

    @pytest.mark.asyncio
    async def test_a_quiet_week_can_show_categories_and_no_brands(self, cache):
        """What the floor means at one to four scans a day: two devices share
        a category long before three share any one brand, so the week shows
        categories alone — the card still shows, since the app hides it only
        when both lists are empty — and a brand one or two devices scanned,
        however often, shows nowhere."""
        await seed(cache, 1, {"clothing": 2}, {}, devices=["dev-a"])
        await seed(cache, 2, {"clothing": 3}, {"Carhartt": 6}, devices=["dev-a"])
        await seed(cache, 4, {"clothing": 2}, {"Carhartt": 1}, devices=["dev-b"])
        payload = await notify.trends(is_pro=False)
        assert [(r["name"], r["count"]) for r in payload["categories"]] == [("clothing", 7)]
        assert payload["brands"] == []

    @pytest.mark.asyncio
    async def test_scan_completed_counts_devices_not_scans(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        c = ResilientCache(None, InMemoryCache())
        notify.configure(c)
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)

        async def scans(subjects):
            for subject in subjects:
                notify.scan_completed(
                    tier="free", item_name="Carhartt Detroit Jacket", brand="Carhartt",
                    category="clothing", low=60, high=100, confidence="High",
                    subject=subject)
            await asyncio.gather(*list(notify._tasks), return_exceptions=True)
            await c.delete(f"{notify.TRENDS_CACHE_KEY}:pro")
            return await notify.trends(is_pro=True, now=tomorrow)

        try:
            one = await scans(["one-device"] * 6)
            assert one["categories"] == [] and one["brands"] == []
            assert one["notable_finds"] == []
            two = await scans(["second-device"])
            assert [(r["name"], r["count"]) for r in two["categories"]] == [("clothing", 7)]
            assert two["brands"] == [] and two["notable_finds"] == []
            three = await scans(["third-device"])
            assert [(r["name"], r["count"]) for r in three["brands"]] == [("Carhartt", 8)]
            assert [f["name"] for f in three["notable_finds"]] == ["Carhartt Detroit Jacket"]
        finally:
            await notify.aclose()


class TestDaysRecordedBeforeDevices:
    """Day documents written before device tags existed carry none. Withheld
    outright, they emptied "Trending at the thrift" — which the paywall sells
    to Pro — from deploy until enough devices had built up under the new
    floor, which at one to four scans a day could take well over a week. So
    their scans count as they were written to, by the five-scan floor alone,
    until they leave the window; and nothing a single device scans since is
    lifted over the floor by them."""

    @pytest.mark.asyncio
    async def test_an_old_week_is_judged_by_five_scans(self, cache):
        await seed(cache, 1, {"clothing": 40, "shoes": 4}, {"Nike": 30, "Ferrari": 4},
                   [find("Le Creuset", "home", 100, 200)], devices=None)
        payload = await notify.trends(is_pro=True)
        assert [(r["name"], r["count"]) for r in payload["categories"]] == [("clothing", 40)]
        assert [(r["name"], r["count"]) for r in payload["brands"]] == [("Nike", 30)]
        # A find never had a scan floor to fall back on — one scan used to be
        # enough — so without devices it is still withheld.
        assert payload["notable_finds"] == []

    @pytest.mark.asyncio
    async def test_old_brands_are_stripped_of_links_as_new_ones_are(self, cache):
        # The code that wrote these did not strip links and handles.
        await seed(cache, 1, {}, {"https://spam.example/x": 9, "@somehandle": 9,
                                  "Nike www.cheap-nikes.example": 3, "Nike": 3}, devices=None)
        brands = (await notify.trends(is_pro=False))["brands"]
        assert [(r["name"], r["count"]) for r in brands] == [("Nike", 6)]

    @pytest.mark.asyncio
    async def test_one_device_cannot_lift_old_scans_over_the_floor(self, cache):
        # Four old scans of each, one short of the floor; then one device,
        # sixty times.
        await seed(cache, 6, {"clothing": 4}, {"Carhartt": 4}, devices=None)
        for days_ago in range(1, 5):
            await seed(cache, days_ago, {"clothing": 15}, {"Carhartt": 15}, devices=["dev-a"])
        payload = await notify.trends(is_pro=False)
        assert payload["categories"] == [] and payload["brands"] == []

    @pytest.mark.asyncio
    async def test_new_scans_without_enough_devices_do_not_grow_an_old_row(self, cache):
        # Five old scans are a row by themselves. One device's twenty new ones
        # neither make it nor move it up the list.
        await seed(cache, 6, {"clothing": 5}, {"Nike": 5}, devices=None)
        await seed(cache, 1, {"clothing": 20}, {"Nike": 20}, devices=["dev-a"])
        payload = await notify.trends(is_pro=False)
        assert [(r["name"], r["count"]) for r in payload["categories"]] == [("clothing", 5)]
        assert [(r["name"], r["count"]) for r in payload["brands"]] == [("Nike", 5)]

    @pytest.mark.asyncio
    async def test_new_scans_add_to_old_ones_once_their_devices_clear_the_floor(self, cache):
        # Two old scans and three new ones from two devices: enough for a
        # category, not for a brand — until a third device scans it.
        await seed(cache, 6, {"clothing": 2}, {"Nike": 2}, devices=None)
        await seed(cache, 1, {"clothing": 3}, {"Nike": 3}, devices=["dev-a", "dev-b"])
        payload = await notify.trends(is_pro=False)
        assert [(r["name"], r["count"]) for r in payload["categories"]] == [("clothing", 5)]
        assert payload["brands"] == []

        await seed(cache, 2, {"clothing": 1}, {"Nike": 1}, devices=["dev-c"])
        await cache.delete(f"{notify.TRENDS_CACHE_KEY}:free")
        payload = await notify.trends(is_pro=False)
        assert [(r["name"], r["count"]) for r in payload["brands"]] == [("Nike", 6)]

    @pytest.mark.asyncio
    async def test_direction_against_an_old_week(self, cache):
        await seed(cache, 1, {"clothing": 12}, {})
        await seed(cache, 8, {"clothing": 6}, {}, devices=None)
        (row,) = (await notify.trends(is_pro=False))["categories"]
        assert row["change_pct"] == 100


class TestTierSplit:
    @pytest.mark.asyncio
    async def test_free_gets_counts_only(self, cache):
        await seed(cache, 1, {"clothing": 9}, {"Nike": 6},
                   [find("Carhartt Detroit Jacket", "clothing", 60, 100)] * 3)
        payload = await notify.trends(is_pro=False)
        assert "notable_finds" not in payload
        assert all("average_estimate" not in r for r in payload["categories"])

    @pytest.mark.asyncio
    async def test_pro_gets_averages_and_notable_finds(self, cache):
        finds = [find("Le Creuset 5.5qt", "home", 120, 220),
                 find("KitchenAid Mixer", "home", 100, 180),
                 find("Pyrex set", "home", 40, 80)]
        await seed(cache, 1, {"home": 9}, {"Le Creuset": 6}, finds)
        payload = await notify.trends(is_pro=True)
        (home,) = payload["categories"]
        assert home["average_estimate"] == 123        # (170 + 140 + 60) / 3
        assert [f["name"] for f in payload["notable_finds"]] == \
            ["Le Creuset 5.5qt", "KitchenAid Mixer", "Pyrex set"]
        assert set(payload["notable_finds"][0]) == {"name", "category", "low", "high"}, \
            "a find is an item and a price — never a device, never a time"

    @pytest.mark.asyncio
    async def test_an_average_needs_three_finds(self, cache):
        await seed(cache, 1, {"home": 9}, {},
                   [find("Le Creuset", "home", 120, 220), find("Pyrex", "home", 40, 80)])
        (home,) = (await notify.trends(is_pro=True))["categories"]
        assert "average_estimate" not in home

    @pytest.mark.asyncio
    async def test_more_rows_for_pro(self, cache):
        cats = {name: 9 for name in
                ["clothing", "shoes", "home", "books", "toys", "sports", "electronics"]}
        await seed(cache, 1, cats, {})
        assert len((await notify.trends(is_pro=False))["categories"]) == notify.TRENDS_FREE_ROWS
        await cache.delete(f"{notify.TRENDS_CACHE_KEY}:pro")
        assert len((await notify.trends(is_pro=True))["categories"]) == notify.TRENDS_PRO_ROWS


class TestNotableFindsAreDistinct:
    """The top five were cut before any repeat was removed, so the client's
    dedupe only ever saw what survived the cut."""

    @pytest.mark.asyncio
    async def test_one_item_scanned_four_times_takes_one_slot(self, cache):
        # The same jacket on four days, at four readings, and on one day twice
        # with different spacing and case — plus four genuinely different items.
        await seed(cache, 1, {"clothing": 9}, {},
                   [find("Carhartt Detroit Jacket", "clothing", 60, 100),
                    find("carhartt  detroit JACKET", "clothing", 70, 140)])
        await seed(cache, 2, {"clothing": 9}, {}, [find("Carhartt Detroit Jacket", "clothing", 50, 90)])
        await seed(cache, 3, {"clothing": 9}, {}, [find("Carhartt Detroit Jacket", "clothing", 60, 120)])
        await seed(cache, 4, {"home": 9}, {},
                   [find("Le Creuset 5.5qt", "home", 120, 130),
                    find("KitchenAid Mixer", "home", 100, 125),
                    find("Pyrex set", "home", 40, 80),
                    find("Dansk Kobenstyle pot", "home", 30, 60)])

        notable = (await notify.trends(is_pro=True))["notable_finds"]

        assert [f["name"] for f in notable] == [
            "carhartt  detroit JACKET", "Le Creuset 5.5qt", "KitchenAid Mixer",
            "Pyrex set", "Dansk Kobenstyle pot"]
        # The most valuable reading of the repeated item is the one kept.
        assert (notable[0]["low"], notable[0]["high"]) == (70, 140)

    @pytest.mark.asyncio
    async def test_an_average_is_over_items_not_rescans(self, cache):
        """Three scans of one pot are one data point, not an average."""
        await seed(cache, 1, {"home": 9}, {}, [find("Le Creuset", "home", 120, 220)] * 3)
        (home,) = (await notify.trends(is_pro=True))["categories"]
        assert "average_estimate" not in home


class TestWrittenWithoutTelegram:
    """The tallies `trends()` reads have one writer, `scan_completed`. It used
    to return early whenever the Telegram variables were unset, so the card
    in the app depended on the operator's bot — and emptied itself, silently,
    a week after the bot was switched off. The other tests here seed the day
    documents directly, which is why none of them could notice."""

    @pytest.mark.asyncio
    async def test_scans_reach_trends_with_the_bot_unconfigured(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        c = ResilientCache(None, InMemoryCache())
        notify.configure(c)
        try:
            assert not notify.enabled()
            for i in range(6):
                notify.scan_completed(
                    tier="free", item_name="Carhartt Detroit Jacket", brand="Carhartt",
                    category="clothing", low=60, high=100, confidence="High",
                    subject=f"device-{i}")
            await asyncio.gather(*list(notify._tasks), return_exceptions=True)

            # Today is outside the window, so read it from tomorrow.
            tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
            payload = await notify.trends(is_pro=False, now=tomorrow)

            assert payload["scans"] == 6
            assert [(r["name"], r["count"]) for r in payload["categories"]] == [("clothing", 6)]
            assert [r["name"] for r in payload["brands"]] == ["Carhartt"]
        finally:
            await notify.aclose()


class TestCaching:
    @pytest.mark.asyncio
    async def test_each_tier_is_cached_separately(self, cache):
        await seed(cache, 1, {"clothing": 9}, {})
        first = await notify.trends(is_pro=False)
        # A later scan does not change what the cache already answered.
        await seed(cache, 1, {"clothing": 99}, {})
        assert (await notify.trends(is_pro=False)) == first
        # Pro has its own entry, computed fresh from the new numbers.
        assert (await notify.trends(is_pro=True))["categories"][0]["count"] == 99

    @pytest.mark.asyncio
    async def test_no_data_is_an_empty_answer_not_an_error(self, cache):
        payload = await notify.trends(is_pro=True)
        assert payload["scans"] == 0
        assert payload["categories"] == [] and payload["brands"] == []
