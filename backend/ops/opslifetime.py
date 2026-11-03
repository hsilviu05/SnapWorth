"""Totals since launch: `/lifetime`, and the block under the weekly report.

The day counters (`opsstats`) expire after 35 days, so no window the bot
reads reaches back to launch. This folds each complete day into running
totals (`opsstats:life:<name>`) that never expire, once per day, claimed by a
marker that outlives the day's counters so no day is folded twice.

Folding, rather than bumping a second counter beside every write, keeps the
request path untouched: it runs from the daily digest and before each read.
What expired before the first fold is gone, so the totals start at the oldest
day still on record, which `since` names.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ops import opsformat, opsrevenue, opsspend, opsstats

if TYPE_CHECKING:
    from core.cache import ResilientCache

log = logging.getLogger("snapworth.notify")

#: The day counters folded in: what the weekly report shows, plus tokens.
NAMES: tuple[str, ...] = (
    "scans_free", "scans_pro", "scans_failed", "active_users",
    opsstats.START_COUNTERS["trial"], opsstats.TRIAL_CONVERSIONS,
    opsstats.START_COUNTERS["paid"], "tok_in", "tok_out", "model_calls",
)

SINCE_KEY = "opsstats:life:since"

#: Longer than a day counter lives, so a day cannot be folded a second time
#: by a replica that still sees its counters.
ROLLED_TTL = opsstats.STATS_TTL + 60 * 60 * 24 * 10

#: Days the counters keep: everything older than this has already expired.
WINDOW_DAYS = opsstats.STATS_TTL // (60 * 60 * 24)

_cache: ResilientCache | None = None


def bind(cache: ResilientCache | None) -> None:
    global _cache
    _cache = cache


def life_key(name: str) -> str:
    return f"opsstats:life:{name}"


def rolled_key(day: str) -> str:
    return f"opsstats:life:rolled:{day}"


async def roll_up(now: datetime | None = None) -> int:
    """Fold every complete day still on record into the totals. Returns how
    many days this call folded. Never raises.

    Today is never folded: it is still being counted, and reads add it live.
    A day's values are read before its marker is claimed, and added after, so
    a failure between claim and add loses that day's counts rather than
    doubling them.
    """
    if _cache is None:
        return 0
    folded = 0
    try:
        for day in opsstats.days_ending_today(WINDOW_DAYS, now)[1:]:
            values = {name: await opsstats.read_stat(day, name) for name in NAMES}
            if not any(values.values()):
                continue
            if not await _cache.add(rolled_key(day), "1", ROLLED_TTL):
                continue
            for name, value in values.items():
                if value:
                    await _cache.incr(life_key(name), None, value)
            since = await _cache.get(SINCE_KEY)
            if not since or day < since:
                await _cache.set(SINCE_KEY, day)
            folded += 1
    except Exception as exc:
        log.warning("lifetime roll-up stopped: %s", type(exc).__name__)
    return folded


async def totals(now: datetime | None = None) -> tuple[dict[str, int], str | None]:
    """Every total, today's live counts included, and the first day counted."""
    await roll_up(now)
    today = opsstats.day(now)
    out: dict[str, int] = {}
    since: str | None = None
    if _cache is None:
        return {name: 0 for name in NAMES}, None
    for name in NAMES:
        try:
            folded = int(await _cache.get(life_key(name)) or 0)
        except Exception:
            folded = 0
        out[name] = folded + await opsstats.read_stat(today, name)
    try:
        since = await _cache.get(SINCE_KEY)
    except Exception:
        since = None
    return out, since or (today if any(out.values()) else None)


def _since_text(since: str | None) -> str:
    if not since:
        return "nothing counted yet"
    return "since " + datetime.strptime(since, "%Y%m%d").strftime("%d %b %Y")


async def lifetime_lines(now: datetime | None = None) -> list[str]:
    """The lifetime block: scans, users, subscriptions, spend and revenue."""
    now = now or datetime.now(timezone.utc)
    t, since = await totals(now)
    free, pro = t["scans_free"], t["scans_pro"]
    scans = free + pro
    trials = t[opsstats.START_COUNTERS["trial"]]
    conversions = t[opsstats.TRIAL_CONVERSIONS]
    direct = t[opsstats.START_COUNTERS["paid"]]
    spend = opsspend._cost_usd(t["tok_in"], t["tok_out"])
    gross, refunds = await opsrevenue.lifetime_money(now)

    lines = [
        f"🏁 <b>Lifetime</b> ({_since_text(since)})",
        f"Scans: {scans} ({free} free · {pro} Pro) · failed {t['scans_failed']}",
        f"Active user-days: {t['active_users']}",
        f"Trial starts: {trials} · paid: {conversions + direct} "
        f"({conversions} converted trial{'s' if conversions != 1 else ''} · {direct} direct)",
        f"Gemini spend: {opsformat.usd(spend)} · {t['model_calls']} calls"
        + (f" · {opsformat.usd_fine(spend / scans)}/scan" if scans else ""),
        f"Revenue: {opsformat.money_total(gross)}"
        + (f" · refunds {opsformat.money_total(refunds)}" if refunds else ""),
    ]
    if set(gross) <= {"USD"} and set(refunds) <= {"USD"}:
        net = (gross.get("USD", 0.0) - refunds.get("USD", 0.0)) * (1 - opsspend.APPLE_COMMISSION)
        lines.append(f"After {opsspend.APPLE_COMMISSION:.0%} Apple: ≈ {opsformat.usd(net)} · "
                     f"minus Gemini ≈ {opsformat.usd(net - spend)}")
    lines.append("Counts start at the oldest day still on record (35 days before this "
                 "was added); revenue goes back to Apple's history. Customer prices, "
                 "before VAT.")
    return lines


async def lifetime_text(now: datetime | None = None) -> str:
    return "\n".join(await lifetime_lines(now))
