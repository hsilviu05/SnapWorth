"""Gemini spend: the token tallies every model call adds, and `/costs`.

Split out of notify.py (#230). The tallies run whenever there is a cache, as
`opsstats.count_scan` does, because `/costs` divides them by scans counted the
same way: a deploy with the bot unset must not tally scans and no spend. So
nothing here depends on Telegram. The cache is bound once at startup by
`notify.configure`, which also passes the over-budget alert while the bot is
configured; notify routes `/costs` here and adds its menu to the reply.
"""

from __future__ import annotations

import html
import logging
import math
import os
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import opsformat
import opsindex
import opsstats
from background import spawn
from opsstats import STATS_TTL

if TYPE_CHECKING:
    from cache import ResilientCache

# notify's logger, so log filters written before the split still match.
log = logging.getLogger("snapworth.notify")

# What the model costs, per million tokens, so spend can be derived from the
# token counts every call already reports. Defaults are Gemini 2.5 Flash's
# published rates (thinking tokens bill as output); Google changes prices and
# GEMINI_MODEL can point elsewhere, so both are env-overridable.
GEMINI_PRICE_INPUT_PER_M = float(os.environ.get("GEMINI_PRICE_INPUT_PER_M", "0.30"))
GEMINI_PRICE_OUTPUT_PER_M = float(os.environ.get("GEMINI_PRICE_OUTPUT_PER_M", "2.50"))
# A daily spend ceiling that pages once when crossed. 0 disables it.
GEMINI_DAILY_BUDGET_USD = float(os.environ.get("GEMINI_DAILY_BUDGET_USD", "0"))
# Apple's cut of a subscription, for `/costs`' net revenue per paying month.
# 0.15 is the Small Business Program rate and the second-year rate; 0.30 is
# the standard first-year rate. Which one applies is an account fact the repo
# does not record, so it is the operator's to set.
APPLE_COMMISSION = float(os.environ.get("APPLE_COMMISSION", "0.15"))

_cache: ResilientCache | None = None

# Sends the once-a-day over-budget message, `async (spend, budget)`. notify's,
# and set only while the bot is configured.
_over_budget: Callable[[float, float], Awaitable[None]] | None = None


def bind(cache: ResilientCache | None,
         over_budget: Callable[[float, float], Awaitable[None]] | None = None) -> None:
    """Point the tallies and `/costs` at the app's cache. None leaves the
    tallies no-ops."""
    global _cache, _over_budget
    _cache = cache
    _over_budget = over_budget


def _cost_usd(tok_in: int, tok_out: int) -> float:
    return (tok_in / 1e6) * GEMINI_PRICE_INPUT_PER_M + (tok_out / 1e6) * GEMINI_PRICE_OUTPUT_PER_M


async def total_spend(days: list[str]) -> float:
    return _cost_usd(await opsstats.sum_stat(days, "tok_in"), await opsstats.sum_stat(days, "tok_out"))


async def budget_line() -> str:
    """Whether a day's Gemini spend can page the operator at all.

    `GEMINI_DAILY_BUDGET_USD` defaults to 0, which switches the alert off, and
    it was never set — while RUNBOOK §3 listed "Over budget" among the alerts
    that reach you. Pro is sold as unlimited scans, capped only per hour
    (`ratelimit.PRO_SCAN_RATE_MAX_REQUESTS`), so this alert is the one thing
    that would notice a heavy day. Its absence is said where the rest of the
    unsafe configuration is.
    """
    budget = GEMINI_DAILY_BUDGET_USD
    if budget <= 0:
        return ("Spend alert: OFF ⚠️ — GEMINI_DAILY_BUDGET_USD is not set, so no "
                "day's Gemini spend reaches you. Set it on Railway (RUNBOOK §12)")
    try:
        today = await total_spend([opsstats.day()])
    except Exception as exc:
        return (f"Spend alert: above {opsformat.usd(budget)}/day · today's spend unreadable "
                f"({html.escape(type(exc).__name__)})")
    return f"Spend alert: above {opsformat.usd(budget)}/day · today ≈ {opsformat.usd(today)}"


async def spend_line(days: list[str], scans: int) -> str:
    """The digest and /status spend line.

    `$/scan` is *users'* spend over user scans. It used to divide the whole
    bill by the user scan count, and the whole bill includes the operator's own
    usage — /post, /price, /caption, /hooks, the /checkup probe. At a handful of
    scans a day that made the figure substantially the operator's own token
    spend, reported as what a user costs. `/costs` already did this correctly,
    so the two surfaces disagreed and the digest was the one being read daily.

    `Gemini ≈` stays the true bill, because that is the number that has to
    match the invoice, and `· N mine` is appended whenever operator usage is
    non-zero so the subtraction is visible rather than silently applied.
    """
    spend = await total_spend(days)
    mine = await _operator_spend(days)
    parts = [f"Gemini ≈ {opsformat.usd(spend)}"]
    if scans:
        parts.append(f"{opsformat.usd_fine(max(spend - mine, 0.0) / scans)}/scan")
        avg_ms = await opsstats.sum_stat(days, "scan_ms")
        if avg_ms:
            parts.append(f"avg scan {avg_ms / scans / 1000:.1f}s")
    if mine > 0:
        parts.append(f"{opsformat.usd(mine)} mine")
    return " · ".join(parts)


#: Labels that are Pro by construction, whatever tier the caller passed:
#: `/listing` answers 402 to anyone else, and a tag photo is only read for Pro.
_PRO_LABELS = ("listing", "scan_with_tag")

# A user's scan, the call a thinking budget is for (#217). Retries and
# reformats are left out, so "thinking per scan call" means what it says.
_SCAN_LABELS = ("scan", "scan_with_tag")


def _usage_tier(label: str, tier: str | None) -> str | None:
    """Which tier a model call is charged to, or None for neither.

    The operator's own calls belong to no tier. Otherwise the label decides
    when it can, and the caller's tier when it cannot: a `scan` or its
    `reformat` retry is Pro or free according to who scanned. A call that
    names no tier is left out of both, rather than guessed into one."""
    if label in _OPERATOR_LABELS:
        return None
    if label in _PRO_LABELS:
        return "pro"
    if tier is None:
        return None
    return "pro" if tier == "pro" else "free"


async def _note_usage(label: str, usage: dict, tier: str | None = None) -> None:
    cache = _cache
    if cache is None:
        return
    try:
        day = opsstats.day()
        tok_in = int(usage.get("prompt_tokens") or 0)
        tok_out = int(usage.get("output_tokens") or 0) + int(usage.get("thoughts_tokens") or 0)
        if tok_in:
            await cache.incr(opsstats.stat_key(day, "tok_in"), STATS_TTL, tok_in)
        if tok_out:
            await cache.incr(opsstats.stat_key(day, "tok_out"), STATS_TTL, tok_out)
        await cache.incr(opsstats.stat_key(day, "model_calls"), STATS_TTL)
        await cache.incr(opsstats.stat_key(day, f"calls_{label}"), STATS_TTL)
        # Per-label tokens, so /costs can separate what users cost from what
        # the operator's own bot usage costs. `calls_{label}` alone could not:
        # it counts calls, and an ideas generation is not the size of a scan.
        if tok_in:
            await cache.incr(opsstats.stat_key(day, f"tok_in_{label}"), STATS_TTL, tok_in)
        if tok_out:
            await cache.incr(opsstats.stat_key(day, f"tok_out_{label}"), STATS_TTL, tok_out)
        # Thinking on scans, apart from the answer (#217). `tok_out` bills the
        # two together, which is right for spend and hides the one number a
        # thinking budget moves.
        thoughts = int(usage.get("thoughts_tokens") or 0)
        if label in _SCAN_LABELS and thoughts:
            await cache.incr(opsstats.stat_key(day, "scan_thoughts"), STATS_TTL, thoughts)
        # Per-tier tokens, so /costs can say what a subscriber costs. Labels
        # name the operation, and a `scan` is the same operation for both.
        charged = _usage_tier(label, tier)
        if charged is not None:
            if tok_in:
                await cache.incr(opsstats.stat_key(day, f"tok_in_tier_{charged}"),
                                  STATS_TTL, tok_in)
            if tok_out:
                await cache.incr(opsstats.stat_key(day, f"tok_out_tier_{charged}"),
                                  STATS_TTL, tok_out)

        budget = GEMINI_DAILY_BUDGET_USD
        announce = _over_budget
        if budget > 0 and announce is not None:
            spend = await total_spend([day])
            if spend > budget and await cache.add(f"opsseen:budget:{day}", "1", STATS_TTL):
                await announce(spend, budget)
    except Exception as exc:
        log.debug("usage note failed: %s", type(exc).__name__)


def model_usage(label: str, usage: dict | None, *, tier: str | None = None) -> None:
    """Tally one model call's tokens. Fire-and-forget.

    Runs whenever there is a cache, as `opsstats.count_scan` does. It used to return
    without the Telegram notifier too, so a deploy with the bot unset tallied
    scans and no spend, and the first `/costs` after turning the bot on
    divided a month of scans by the days since.

    `tier` is the caller's, for the calls whose label does not settle it:
    see `_usage_tier`."""
    if _cache is None:
        return
    spawn(_note_usage(label, dict(usage or {}), tier))


# Model calls the operator makes through the bot: /post ideas, the /checkup
# one-token probe, and a photo sent to the bot as a test scan (with its
# reformat retry, which was filed as a user's `reformat` until #219). They are
# billed like any other call and belong in the total — but not in "$/scan" or
# in either tier's spend, which are statements about users.
_OPERATOR_LABELS = ("ideas", "probe", "bot_scan", "bot_scan_with_tag", "bot_reformat")


async def _operator_spend(days: list[str]) -> float:
    tok_in = sum([await opsstats.sum_stat(days, f"tok_in_{label}") for label in _OPERATOR_LABELS])
    tok_out = sum([await opsstats.sum_stat(days, f"tok_out_{label}") for label in _OPERATOR_LABELS])
    return _cost_usd(tok_in, tok_out)


async def _tier_spend(days: list[str], tier: str) -> float:
    """Model spend charged to one tier (see `_usage_tier`)."""
    return _cost_usd(await opsstats.sum_stat(days, f"tok_in_tier_{tier}"),
                     await opsstats.sum_stat(days, f"tok_out_tier_{tier}"))


def _percentile(sorted_values: list[int], p: float) -> int:
    """Nearest-rank percentile of an ascending list; the list is non-empty."""
    return sorted_values[max(1, math.ceil(len(sorted_values) * p)) - 1]


def _paid_by_currency(doc: dict, now: float) -> dict[str, tuple[float, int]]:
    """{currency: (monthly revenue, paid subscriptions)} over live paid rows.

    MRR alone cannot be divided by the paid count `opsindex.subs_summary` returns: that
    count spans currencies, and a row with no price adds a subscriber and no
    revenue. Both halves come from the same priced rows here."""
    out: dict[str, tuple[float, int]] = {}
    for e in doc.values():
        if not isinstance(e, dict) or e.get("acq") != "paid" or not opsformat.sub_is_alive(e, now):
            continue
        price, cur = e.get("price"), e.get("currency") or "?"
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        monthly = price / 12 if "yearly" in opsformat.plan(e.get("product")) else price
        total, n = out.get(cur, (0.0, 0))
        out[cur] = (total + monthly, n + 1)
    return out


def _pro_span_days(row: dict, start: float, now: float) -> float:
    """Days of `row`'s current or last Pro span inside [start, now]."""
    since = row.get("pro_since")
    if not isinstance(since, (int, float)):
        return 0.0
    until = row.get("pro_until")
    end = float(until) if isinstance(until, (int, float)) else now
    return max(0.0, min(end, now) - max(float(since), start)) / 86400


async def _pro_block(month: list[str]) -> list[str]:
    """What a subscriber costs against what one pays, over `month`.

    Every figure carries its n: at launch there are a handful of subscribers,
    and a mean over three devices is a claim about three devices. The tail
    (p90, max, the heaviest three) is shown for the same reason — Pro is sold
    as unlimited, and the price question is decided by the heaviest users, not
    the average one.

    Per-device dollars are estimates: tokens are tallied per tier, not per
    device, so a device's spend is its Pro scans times the tier's all-in cost
    per Pro scan (listings and reformats included)."""
    now = time.time()
    start = now - len(month) * 86400
    users = await opsindex.read_index(opsindex.USERS_INDEX_KEY)
    subs = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
    lines = [f"<b>Pro, last {len(month)} days</b>"]

    paid_rows = [e for e in subs.values() if isinstance(e, dict)
                 and e.get("acq") == "paid" and opsformat.sub_is_alive(e, now)]
    paying = {d for e in paid_rows for d in opsindex.row_devices(e)}
    lines.append(f"Paying Pro devices: {len(paying)} "
                 f"(n={len(paid_rows)} paid subscriptions)")

    spend = await _tier_spend(month, "pro")
    scans = await opsstats.sum_stat(month, "scans_pro")
    pro_rows = {who: e for who, e in users.items()
                if isinstance(e, dict) and _pro_span_days(e, start, now) > 0}
    device_months = sum(_pro_span_days(e, start, now) for e in pro_rows.values()) / 30
    per_month = (f"{opsformat.usd_fine(spend / device_months)} per Pro device-month"
                 if device_months > 0 else "n/a per Pro device-month")
    per_scan = spend / scans if scans else None
    lines.append(
        f"Pro model spend: {opsformat.usd(spend)} (n={scans} Pro scans"
        + (f", {opsformat.usd_fine(per_scan)}/scan" if per_scan is not None else "")
        + f") · {per_month} (n={device_months:.1f} device-months, "
        f"{len(pro_rows)} devices)")

    by_currency = _paid_by_currency(subs, now)
    keep = 1 - APPLE_COMMISSION
    if by_currency:
        net = " + ".join(f"{opsformat.money(total / n * keep, cur)} (n={n})"
                         for cur, (total, n) in sorted(by_currency.items()))
    else:
        net = "n/a (n=0 priced paid plans)"
    lines.append(f"Net revenue per paying month: {net} "
                 f"after {APPLE_COMMISSION:.0%} Apple commission")

    oldest = month[-1]
    daily = sorted(int(n) for e in pro_rows.values()
                   for d, n in (e.get("pro_days") or {}).items()
                   if d >= oldest and isinstance(n, (int, float)) and n > 0)
    if daily:
        lines.append(f"Pro scans per device-day: p50 {_percentile(daily, 0.5)} · "
                     f"p90 {_percentile(daily, 0.9)} · max {daily[-1]} "
                     f"(n={len(daily)} device-days)")
    else:
        lines.append("Pro scans per device-day: n/a (n=0 device-days)")

    heaviest = []
    if per_scan is not None:
        for who, e in pro_rows.items():
            count = sum(int(n) for d, n in (e.get("pro_days") or {}).items()
                        if d >= oldest and isinstance(n, (int, float)))
            if count:
                days = max(1.0, _pro_span_days(e, start, now))
                heaviest.append((count * per_scan / days, who, count, days))
        heaviest.sort(reverse=True)
    if heaviest:
        lines.append(f"Heaviest by $/day (n={len(heaviest)} devices with Pro scans): " + " · ".join(
            f"{who[:6]} {opsformat.usd_fine(rate)}/day ({count} scans / {days:.1f}d)"
            for rate, who, count, days in heaviest[:3]))
    else:
        lines.append("Heaviest by $/day: n/a (n=0 devices with Pro scans)")
    return lines


async def _free_line(month: list[str]) -> str:
    """Cost per free device-day that scanned: what the free tier costs per
    person who used it, rather than a share of the bill split by scan count."""
    spend = await _tier_spend(month, "free")
    device_days = await opsstats.sum_stat(month, "free_device_days")
    rate = (f"≈ {opsformat.usd_fine(spend / device_days)} per active free device-day"
            if device_days else "≈ n/a per active free device-day")
    return (f"Free tier, {len(month)} days: {opsformat.usd(spend)} {rate} "
            f"(n={device_days} device-days with a scan)")


async def _thinking_line() -> str:
    """Thinking tokens per scan call, today and over 7 and 30 days, and the
    budget this process runs with (#217): the before and after a
    `GEMINI_THINKING_BUDGET` change is read against."""
    import aiconfig    # the live value, as this process parsed it at start
    budget = aiconfig.THINKING_BUDGET
    parts = []
    for label, n in (("today", 1), ("7d", 7), ("30d", 30)):
        days = opsstats.days_ending_today(n)
        calls = sum([await opsstats.sum_stat(days, f"calls_{name}") for name in _SCAN_LABELS])
        thoughts = await opsstats.sum_stat(days, "scan_thoughts")
        parts.append(f"{label} {thoughts // calls:,}" if calls else f"{label} —")
    return ("🧠 Thinking per scan call: " + " · ".join(parts)
            + f" · budget {'unset' if budget is None else budget}"
            " (GEMINI_THINKING_BUDGET)")


async def costs_text() -> str:
    lines = ["💸 <b>Gemini spend</b>"]
    for label, n in (("Today", 1), ("Last 7 days", 7), ("Last 30 days", 30)):
        days = opsstats.days_ending_today(n)
        tok_in = await opsstats.sum_stat(days, "tok_in")
        tok_out = await opsstats.sum_stat(days, "tok_out")
        calls = await opsstats.sum_stat(days, "model_calls")
        scans = await opsstats.sum_stat(days, "scans_free") + await opsstats.sum_stat(days, "scans_pro")
        spend = _cost_usd(tok_in, tok_out)
        mine = await _operator_spend(days)
        parts = [f"{label}: {opsformat.usd(spend)}", f"{calls} calls",
                 f"{opsformat.kilo(tok_in)} in / {opsformat.kilo(tok_out)} out"]
        if scans:
            # Users' spend, not total spend. This used to divide the whole
            # figure — operator test scans, /post and /checkup probes included
            # — by the user scan count, which at 1-4 scans a day made "$/scan"
            # substantially the operator's own usage.
            parts.append(f"{opsformat.usd_fine(max(spend - mine, 0.0) / scans)}/scan")
        if mine > 0:
            parts.append(f"{opsformat.usd(mine)} mine")
        lines.append(" · ".join(parts))

    lines.append(await _thinking_line())

    month = opsstats.days_ending_today(30)
    lines.extend(await _pro_block(month))
    lines.append(await _free_line(month))
    mine_month = await _operator_spend(month)
    if mine_month > 0:
        lines.append(f"My own bot usage, 30 days: ≈ {opsformat.usd(mine_month)} "
                     f"(/post, /checkup — excluded from $/scan and both tiers)")

    _, _, _, _, mrr = opsindex.subs_summary(await opsindex.read_index(opsindex.SUBS_INDEX_KEY))
    lines.append("vs MRR ≈ " + (" + ".join(opsformat.money(v, c) for c, v in sorted(mrr.items()))
                                 if mrr else "n/a") + " (paid plans)")
    budget = f" · budget {opsformat.usd(GEMINI_DAILY_BUDGET_USD)}/day" if GEMINI_DAILY_BUDGET_USD > 0 else ""
    lines.append(f"Prices: ${GEMINI_PRICE_INPUT_PER_M:.2f}/M in · "
                 f"${GEMINI_PRICE_OUTPUT_PER_M:.2f}/M out{budget}")
    return "\n".join(lines)
