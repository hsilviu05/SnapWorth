"""What people scan, tallied per day, and `/trends`, which serves it to the app.

Split out of notify.py (#230). The day documents (`opsstats:<day>:top`) were
written from the operator's Telegram module, and while they lived there
"Trending at the thrift" collected nothing unless the bot was configured. This
module needs the cache and nothing else, bound once at startup by
`notify.configure`; the operator's views (/finds, /trend, the digest's top
line) read the same documents from notify.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import auditlog
import categories
import opsstats
from confidence import brand_is_known

if TYPE_CHECKING:
    from cache import ResilientCache

# notify's logger, so log filters written before the split still match.
log = logging.getLogger("snapworth.notify")

# Brand tallies are keyed by whatever the model wrote, so the day's table is
# capped; categories are a closed set and need no cap.
TOP_BRANDS_CAP = 200

# The week's most valuable scans, kept alongside the day's category and brand
# tallies for /finds and as grounding for /post. Item and price only.
TOP_FINDS_CAP = 8

_cache: ResilientCache | None = None


def bind(cache: ResilientCache | None) -> None:
    """Point the tallies and `/trends` at the app's cache. None leaves
    `record_scan` a no-op and `trends()` empty."""
    global _cache
    _cache = cache


async def record_scan(*, tier: str, item_name: str, brand: str | None,
                      category: str, low: float, high: float,
                      subject: str | None = None, reread: bool = False) -> None:
    """The half of a scan that `/trends` reads: the count and the tallies.

    Runs whether or not Telegram is configured; `notify.scan_completed` calls
    it before anything that is the operator's alone, and handles its errors.
    """
    if _cache is None:
        return
    await opsstats.bump("scans_pro" if tier == "pro" else "scans_free")
    if tier != "pro" and subject:
        # Distinct free devices that scanned today: the denominator of
        # `/costs`' cost per active free device-day. Beside the spend
        # tally it divides, so it is written whenever that is.
        who = auditlog.pseudonymise(subject)
        if await _cache.add(f"opsseen:fd:{opsstats.day()}:{who}", "1", opsstats.STATS_TTL):
            await opsstats.bump("free_device_days")
    if not reread:
        await _tally_top(opsstats.day(), normalise_category(category), clean_brand(brand),
                         _find_record(item_name=item_name, brand=brand, category=category,
                                      low=low, high=high, tier=tier),
                         _trend_device(subject))


def normalise_category(category: str | None) -> str:
    # `/scan` already hands over a normalised category. Normalised again here
    # because the day's tallies key on it and must not grow a row per spelling.
    return categories.normalise(category)


# Web addresses, e-mail addresses and @handles. Brands and item names are text
# the model read off a user's photo, and `/trends` shows them to every install:
# a label printed with "shop at x.com" or "follow @x" is an advert, not a brand.
# The domain branch names its endings rather than matching any "a.b", so
# "J.Crew", "A.P.C." and "Mr. Coffee" survive.
_LINKISH = re.compile(
    r"(?:https?://|www\.)\S*"
    r"|\S+@\S+"
    r"|(?<![\w.])@\w+"
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|info|biz|io|co|me|ly|gg|tv|xyz|app|"
    r"shop|store|link|site|online|club|live|top|tk|ru|cn|us|uk|de|eu|ro)\b(?:/\S*)?",
    re.IGNORECASE)


def _without_links(text: str | None) -> str:
    """`text` with anything shaped like a URL or a handle removed, and its
    whitespace collapsed."""
    return " ".join(_LINKISH.sub(" ", text or "").split())


def clean_brand(brand: str | None) -> str | None:
    """A brand worth tallying, or None. Model output: trimmed, bounded, and
    stripped of links and handles."""
    value = _without_links(brand)[:40]
    if not brand_is_known(value):
        return None
    return value


async def _tally_top(day: str, category: str, brand: str | None,
                     find: dict | None = None, device: str | None = None) -> None:
    """Read-modify-write of the day's category and brand counts, and its
    handful of most valuable finds.

    One small JSON document rather than a key per brand, because the cache
    interface cannot enumerate keys and the report needs the whole table.
    A lost update between two replicas costs one count, which is fine for a
    tally that exists to say "clothing 5 · Nike ×3". A lost *read* is not:
    see `opsstats.read_doc_for_update`, which this shares with the
    subscription index.
    Losing this scan's count is the price of not resetting the day's.

    `device` is `_trend_device`'s keyed tag for whoever scanned it, recorded
    beside each category, brand and find (at most TRENDS_DEVICES_KEPT per
    entry) so `/trends` can count devices rather than scans — see
    `TRENDS_MIN_CATEGORY_DEVICES`. It stays as long as the document, opsstats.STATS_TTL.

    The write that gives a day its device maps also offers that day to
    TRENDS_TAGGED_SINCE_KEY, which keeps the first: see `_tagged_since`.
    """
    key = opsstats.stat_key(day, "top")
    doc = await opsstats.read_doc_for_update(key)
    if doc is None or _cache is None:
        return
    first_tagged_write = not isinstance(doc.get("cat_devices"), dict)
    cats = c if isinstance(c := doc.get("cats"), dict) else {}
    brands = b if isinstance(b := doc.get("brands"), dict) else {}
    finds = f if isinstance(f := doc.get("finds"), list) else []
    cat_devices = cd if isinstance(cd := doc.get("cat_devices"), dict) else {}
    brand_devices = bd if isinstance(bd := doc.get("brand_devices"), dict) else {}
    cats[category] = int(cats.get(category, 0)) + 1
    cat_devices[category] = _add_device(cat_devices.get(category), device)
    if brand is not None and (brand in brands or len(brands) < TOP_BRANDS_CAP):
        brands[brand] = int(brands.get(brand, 0)) + 1
        brand_devices[brand] = _add_device(brand_devices.get(brand), device)
    if find is not None:
        find = {**find, "d": _add_device(None, device)}
        finds = _merge_finds([*finds, find])[:TOP_FINDS_CAP]
    await _cache.set(key, json.dumps({"cats": cats, "brands": brands, "finds": finds,
                                      "cat_devices": cat_devices,
                                      "brand_devices": brand_devices}),
                     opsstats.STATS_TTL)
    if first_tagged_write:
        # A day's first write, or the first since an older build wrote the
        # day back without its maps. `add`, so only the first ever stands.
        try:
            await _cache.add(TRENDS_TAGGED_SINCE_KEY, day)
        except Exception as exc:
            log.debug("trends tagged-since note failed: %s", type(exc).__name__)


def _trend_device(subject: str | None) -> str | None:
    """A short tag meaning "a different device", for the `/trends` floor.

    Kept beside the categories, brands and finds a device scanned, for as
    long as the day document (opsstats.STATS_TTL, 35 days), and used for nothing but
    counting distinct devices. So it must not be a join key. It was a plain
    hash of the audit pseudonym, and the same cache holds every pseudonym in
    full — `/users`'s index, each `/subs` row — so anyone who could read the
    cache could recompute every tag and tie a device, and through its
    subscription or a support mail a customer, to the items it scanned.

    Now `auditlog.keyed_tag`: an HMAC under AUDIT_SALT, which lives in the
    environment and never in the cache. Someone holding the salt and a
    device's key id can still recompute its tag; nothing stored beside it
    can. None when the scan has no subject, which counts toward nothing."""
    if not subject:
        return None
    return auditlog.keyed_tag("trends", subject)


def _add_device(devices, device: str | None) -> list[str]:
    """`devices` with `device` added, holding at most TRENDS_DEVICES_KEPT.

    The cap is exact for the only question ever asked of the list — "did at
    least N different devices do this, this week?" — for every floor N, since
    it is the largest of them. If any one day's list is full, the week's union
    is at least the cap, so at least N; if none is, every list is complete and
    the union is the true count."""
    kept = [d for d in (devices or []) if isinstance(d, str)][:TRENDS_DEVICES_KEPT]
    if device and device not in kept and len(kept) < TRENDS_DEVICES_KEPT:
        kept.append(device)
    return kept


def _find_key(find: dict) -> tuple[str, str]:
    """What makes two finds the same item: the name, case and spacing
    ignored, within one category."""
    return (" ".join(str(find.get("n") or "").lower().split()),
            str(find.get("c") or "other"))


def _merge_finds(finds: list) -> list[dict]:
    """One entry per item, the most valuable reading kept, best first.

    A find is recorded per scan, so one jacket scanned four times was four of
    the day's eight slots, and the week's list — seven days appended — could
    carry it once per day as well. `/trends` then cut to five *before* anything
    removed the repeats, and the client's dedupe could only work on what was
    left: two or three notable finds instead of five, or the same item twice at
    two prices. Deduped here, on write and again across days, so the cut is
    taken over distinct items."""
    best: dict[tuple[str, str], dict] = {}
    for find in finds:
        if not isinstance(find, dict):
            continue
        key = _find_key(find)
        kept = best.get(key)
        if kept is None:
            best[key] = find
            continue
        # Whichever reading wins, the devices behind the item are all of them.
        devices = list(kept.get("d") or [])
        for device in find.get("d") or []:
            devices = _add_device(devices, device)
        winner = find if float(find.get("hi") or 0) > float(kept.get("hi") or 0) else kept
        best[key] = {**winner, "d": devices}
    return sorted(best.values(), key=lambda f: -float(f.get("hi") or 0))


def _find_record(*, item_name: str, brand: str | None, category: str,
                 low: float, high: float, tier: str) -> dict:
    """A scan as /finds and /trends keep it: the item and its price. The name
    is stripped of links and handles, as `clean_brand` does.

    Not quite all that is stored: `_tally_top` adds `d`, the keyed tags of the
    devices behind the item (see `_trend_device`), so a find can be held back
    until TRENDS_MIN_FIND_DEVICES have scanned it."""
    return {"n": _without_links(item_name)[:60] or "Unidentified item",
            "b": clean_brand(brand), "c": normalise_category(category),
            "lo": round(float(low)), "hi": round(float(high)),
            "t": "pro" if tier == "pro" else "free"}


# ── Trends, for the app (#96) ────────────────────────────────────────────────
#
# The same tallies the bot reads, shaped for users. Aggregates only, with a
# floor: a category or brand appears only once enough scans *and* enough
# different devices back it, and a notable find only once enough different
# devices have scanned that item. A find is still one item rather than a total,
# so it leaves the server as a brand, a category and a range: never the item
# name, no device, no photo, no time of day. Pro sees the averages and the
# finds; free sees the counts.
#
# The floor used to count scans alone, and at one to four real scans a day,
# five scans of one label — a spam URL, a slur — was that week's "Trending at
# the thrift" row on every install, and notable finds had no floor at all: one
# scan was enough. The text is read off a user's photo; nothing upstream of
# here promises it is fit to show anyone else.
#
# Days tallied before devices were recorded still count, by scans alone, for
# the fortnight they stay in the window. A day an older build wrote back after
# tags began has lost its devices rather than never had them, and is withheld
# — see `_floored`.

TRENDS_MIN_COUNT = 5          # below this a row says more about one user than a trend
# Different devices behind a row or a find across the week, whatever the scan
# count. Brands and finds are free text off a photo, so they need three.
# Categories are a closed set and cannot carry a URL or a slur; their floor
# serves only "one user's afternoon is not a trend", and two devices say that.
# The difference is felt at one to four scans a day, where three devices behind
# one category may take much longer than a week, and the app hides "Trending at
# the thrift" — which the paywall sells to Pro — while both lists are empty.
TRENDS_MIN_CATEGORY_DEVICES = 2
TRENDS_MIN_BRAND_DEVICES = 3
TRENDS_MIN_FIND_DEVICES = 3
# How many tags each list in a day document keeps: the largest floor, which
# `_add_device` shows is all any floor needs.
TRENDS_DEVICES_KEPT = max(TRENDS_MIN_CATEGORY_DEVICES, TRENDS_MIN_BRAND_DEVICES,
                          TRENDS_MIN_FIND_DEVICES)
TRENDS_FREE_ROWS = 3
TRENDS_PRO_ROWS = 6
TRENDS_FINDS = 5
TRENDS_CACHE_KEY = "opsstate:trends"
TRENDS_CACHE_TTL = 15 * 60
# The first day `_tally_top` gave a day document its device maps. Kept with no
# expiry: it is one date, and the code before tags never writes it, so no
# rollback can move or remove it. See `_tagged_since`.
TRENDS_TAGGED_SINCE_KEY = "opsstate:trends_tagged_since"


def _trend_rows(counts: list[tuple[str, int]], previous: dict[str, int],
                limit: int) -> list[dict]:
    """Rows above the floor, with last week's direction where it exists."""
    rows = []
    for name, count in counts:
        if count < TRENDS_MIN_COUNT:
            continue
        row: dict = {"name": name, "count": count}
        before = previous.get(name)
        if isinstance(before, int) and before >= TRENDS_MIN_COUNT:
            row["change_pct"] = round((count - before) / before * 100)
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _floored(docs: list[tuple[str, dict]], counts_field: str, devices_field: str,
             min_devices: int, clean: Callable[[str], str | None],
             tagged_since: str | None) -> dict[str, int]:
    """One table of `docs` — `(day, document)` for the days of one window —
    summed per name, from the scans the device floor lets count.

    A day tallied with device tags has `devices_field`, and its scans of a name
    count only once `min_devices` different devices stand behind that name
    across the window's tagged days. A day written before device tags existed
    has no such field. Withholding it emptied the card from deploy until the
    devices built up, so its scans count as they did then, by the scan floor
    alone; its names go through `clean` first, because the code that wrote
    them did not strip links.

    But a missing field means "before tags" only for a day earlier than
    `tagged_since`, the first day tagged code wrote. The code before tags
    writes the whole document back without the maps, and a rollback is a
    deploy — the runbook's first move — so a day it wrote on or after that
    day has lost its devices, not never had them. Counted by scans, one
    device's five scans of a brand were on every install for as long as that
    day stayed in the window after the redeploy. It is withheld instead; a
    tagged write landing on it again is judged by the devices that write
    recorded, which is cautious, since the earlier ones are gone.

    In a window holding both, then, old scans always count and new scans only
    with their devices. One device on its own, however often it scans, can
    neither lift old scans that fell short of TRENDS_MIN_COUNT over it nor add
    to a row they made by themselves: from new data, a brand needs three.
    Days before `tagged_since` leave the fortnight `trends()` reads two weeks
    after it, and from then on nothing counts by scans alone. The deploy day
    itself, written by both, is judged by the devices it recorded, which
    leaves its earlier scans with none: cautious, for one day.
    """
    legacy: dict[str, int] = {}
    tagged: dict[str, int] = {}
    devices: dict[str, list[str]] = {}
    for day, doc in docs:
        counts = doc.get(counts_field)
        counts = counts if isinstance(counts, dict) else {}
        table = doc.get(devices_field)
        if not isinstance(table, dict):
            if tagged_since is not None and day >= tagged_since:
                continue
            for name, n in counts.items():
                name = clean(name)
                if name is not None:
                    legacy[name] = legacy.get(name, 0) + int(n)
            continue
        for name, n in counts.items():
            tagged[name] = tagged.get(name, 0) + int(n)
        for name, tags in table.items():
            for tag in tags if isinstance(tags, list) else []:
                devices[name] = _add_device(devices.get(name), tag)
    counted = dict(legacy)
    for name, n in tagged.items():
        if len(devices.get(name) or []) >= min_devices:
            counted[name] = counted.get(name, 0) + n
    return counted


async def _top_docs(days: list[str]) -> list[tuple[str, dict]]:
    """Each day's top document beside its day; {} when absent or unreadable."""
    if _cache is None:
        return [(day, {}) for day in days]
    docs: list[tuple[str, dict]] = []
    for day in days:
        try:
            doc = json.loads(await _cache.get(opsstats.stat_key(day, "top")) or "{}")
        except Exception:
            doc = {}
        docs.append((day, doc if isinstance(doc, dict) else {}))
    return docs


async def _tagged_since(docs: list[tuple[str, dict]]) -> str | None:
    """The first day tagged code wrote, or None if it never has — the line
    `_floored` draws between a day from before tags and a day an older build
    wrote back after them.

    TRENDS_TAGGED_SINCE_KEY holds it, because the days themselves cannot:
    after a rollback longer than the window, no tagged day is left in it to
    say when tags began. The earliest tagged day in `docs` stands in when the
    key cannot be read or was lost, and a lost key costs no more than that."""
    try:
        recorded = await _cache.get(TRENDS_TAGGED_SINCE_KEY) if _cache is not None else None
    except Exception:
        recorded = None
    days = [day for day, doc in docs if isinstance(doc.get("cat_devices"), dict)]
    if recorded:
        days.append(str(recorded))
    return min(days) if days else None


async def _tallies(docs: list[tuple[str, dict]], tagged_since: str | None
                   ) -> tuple[dict[str, int], dict[str, int], list[dict], int]:
    """The days' category and brand counts, finds and scan total.

    The counts are only what `_floored` lets count; everything else is left
    out here, so no caller can forget the floor. Finds come back whole, and
    `trends()` holds each back until TRENDS_MIN_FIND_DEVICES have scanned it."""
    scans = 0
    for day, _ in docs:
        scans += await opsstats.read_stat(day, "scans_free") + await opsstats.read_stat(day, "scans_pro")
    finds = [f for _, doc in docs for f in doc.get("finds") or [] if isinstance(f, dict)]
    return (_floored(docs, "cats", "cat_devices", TRENDS_MIN_CATEGORY_DEVICES,
                     normalise_category, tagged_since),
            _floored(docs, "brands", "brand_devices", TRENDS_MIN_BRAND_DEVICES,
                     clean_brand, tagged_since),
            finds, scans)


async def trends(*, is_pro: bool, now: datetime | None = None) -> dict:
    """This week's categories and brands, against the week before.

    Cached for everyone (the numbers are identical per tier), so a burst of
    app launches costs one pass over fourteen day-documents rather than one
    per request.
    """
    if _cache is None:
        return {"days": 7, "scans": 0, "categories": [], "brands": []}
    tier = "pro" if is_pro else "free"
    try:
        cached = await _cache.get(f"{TRENDS_CACHE_KEY}:{tier}")
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    now = now or datetime.now(timezone.utc)
    # Both windows end yesterday. `_days_ending_today` starts at i=0, so the
    # current week used to be six whole days plus however much of today had
    # happened, compared against seven whole days — every category was measured
    # short against a full-length baseline and the arrow leaned ▼ all day,
    # recovering only around midnight UTC. `_weekly_text` already anchors this
    # way; trends did not. Today is excluded from both the ratio and the scan
    # count so the percentage and the number printed beside it cannot disagree.
    end = now - timedelta(days=1)
    this_week = [opsstats.day(end - timedelta(days=i)) for i in range(7)]
    last_week = [opsstats.day(end - timedelta(days=i)) for i in range(7, 14)]
    current, previous = await _top_docs(this_week), await _top_docs(last_week)
    since = await _tagged_since(current + previous)
    cats, brands, finds, scans = await _tallies(current, since)
    prev_cats, prev_brands, _, _ = await _tallies(previous, since)

    limit = TRENDS_PRO_ROWS if is_pro else TRENDS_FREE_ROWS
    payload: dict = {
        "days": 7,
        "scans": scans,
        "categories": _trend_rows(sorted(cats.items(), key=lambda kv: -kv[1]), prev_cats, limit),
        "brands": _trend_rows(sorted(brands.items(), key=lambda kv: -kv[1]), prev_brands, limit),
    }
    if is_pro:
        # One entry per item across the week, before anything is averaged or
        # cut to five — see `_merge_finds`, which also pools the devices
        # behind each item.
        finds = _merge_finds(finds)
        # Average estimate per category, from the day's best finds only —
        # which is what the tallies keep. Labelled as such by the client.
        by_category: dict[str, list[float]] = {}
        for f in finds:
            category = str(f.get("c") or "other")
            try:
                low, high = float(f.get("lo") or 0), float(f.get("hi") or 0)
            except (TypeError, ValueError):
                continue
            if high > 0:
                by_category.setdefault(category, []).append((low + high) / 2)
        for row in payload["categories"]:
            values = by_category.get(row["name"]) or []
            if len(values) >= 3:      # an average of one or two is not an average
                row["average_estimate"] = round(sum(values) / len(values))
        # The brand stands in for the item name. A find is one item, shown to
        # strangers, and the name is whatever the model wrote about someone's
        # photo — free text that can carry anything it read off a label.
        # `name` stays the field so shipped clients decode it unchanged. A
        # find with no brand has nothing left worth showing and is skipped.
        #
        # So is a repeat. Clients key a find on `name-low-high` and drop
        # duplicates (`Trends.distinctNotableFinds`), and with the brand as the
        # name, two scans of one brand at the same rounded range are one row to
        # them — sent twice, it would take a slot and show nothing.
        #
        # And so is a find fewer than TRENDS_MIN_FIND_DEVICES scanned, counted
        # per item by `_merge_finds` above. A find from a day tallied before
        # devices were recorded has no `d`, and stays withheld where its day's
        # rows now count by scans: a find never had a scan floor to fall back
        # on — one scan was enough.
        notable: list[dict] = []
        shown: set[tuple[str, int, int]] = set()
        for f in finds:
            if len(f.get("d") or []) < TRENDS_MIN_FIND_DEVICES:
                continue
            brand = clean_brand(str(f.get("b") or ""))
            if brand is None or float(f.get("hi") or 0) <= 0:
                continue
            lo, hi = round(float(f.get("lo") or 0)), round(float(f.get("hi") or 0))
            if (brand, lo, hi) in shown:
                continue
            shown.add((brand, lo, hi))
            notable.append({"name": brand, "category": str(f.get("c") or "other"),
                            "low": lo, "high": hi})
            if len(notable) >= TRENDS_FINDS:
                break
        payload["notable_finds"] = notable

    try:
        await _cache.set(f"{TRENDS_CACHE_KEY}:{tier}", json.dumps(payload), TRENDS_CACHE_TTL)
    except Exception as exc:
        log.debug("trends cache write failed: %s", type(exc).__name__)
    return payload
