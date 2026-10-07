"""Operator alerts to a private Telegram chat.

A single-operator service has no on-call rotation and no pager: production
telling *someone* what just happened means telling one phone. Telegram is the
cheapest reliable way to do that — the Bot API is free, needs no SDK, and a
message to a private chat is push-delivered.

Everything here is OFF unless both ``TELEGRAM_BOT_TOKEN`` and
``TELEGRAM_CHAT_ID`` are set — except the scan count and the category, brand
and finds tallies, which `/trends` serves to the app, and the subscription
index with its trial and paid counters (#218), all of which therefore run
whenever there is a cache — and every path is best-effort by construction:
an alert *about* production must never be able to degrade production. No user
request ever waits on Telegram — sends run as background tasks — and the one
awaited entry point (`entitlement_recorded`) swallows its own failures.

What gets sent:

* **New Pro subscription** — the first sighting of an ``originalTransactionId``.
  Renewals and re-syncs share that id, so they never re-fire.
* **Subscription ended** — a signed transaction that verified but grants
  nothing (refunded, revoked or expired), throttled per subject per day.
* **AI provider transitions** — degraded / recovered, from the same
  `_ModelHealth` state /health reports, throttled so a flapping upstream is
  one message per half hour rather than one per failure.
* **A daily digest** of scan and subscription counters kept in the shared
  cache, so multiple replicas count together and exactly one of them sends.
* **A deploy ping** the first time a commit boots — which also makes every
  release a live test of the notifier itself.
* **A sharing signal** when the subscription device cap evicts a device that
  was recently active, once per subscription per day.
* **A live scan feed** — one line per valuation, item and price only, never
  who scanned it and never the photo. `/feed off` silences it.
* **A weekly report** with Monday's digest: the seven days just ended against
  the seven before, with the direction of each number.

And it listens: `/status`, `/subs`, `/users`, `/costs`, `/social`, `/finds`,
`/post`, `/digest`, `/week`, `/feed` from the operator's chat, with inline
buttons under every reply so nothing has to be typed. `/costs` prices every
model call's token usage; `/social` reads the app's own TikTok account
(social.py). `/subs` is every subscription the server has seen — plan, how it
was obtained (paid, offer code, trial), renewal date, and an MRR line from
Apple's own transaction prices. `/users` is devices seen: 7- and 30-day
actives and the most active, by the audit log's pseudonyms, because there are
no accounts. `/finds` is the week's most valuable scans; `/post` hands those
to the model (ideas.py) and comes back with three TikTok post ideas grounded
in what people actually scanned. Anyone else who finds the bot gets silence.

Two things about surviving a deploy. Only one replica may poll Telegram, and
the lock that decides which is released on shutdown — otherwise the new build
sits silent for the lock's TTL after every release, which read as "the bot
ignores me until I press Refresh". And the poll offset is kept in the cache,
so the successor continues where the predecessor stopped instead of
re-answering the last batch of commands.

The bot token is a credential. It appears in request URLs, so failures are
logged by exception class name only, and observability.py redacts the token
pattern as a backstop.
"""

# `_cache` and `_notifier` are module state set once by `configure`. Every
# public entry point returns early while either is None, and the ~40 private
# helpers below them run only past that guard — which pyright cannot see
# across a call, so each `_cache.get` read as a possible None access: 78 of
# this file's errors, none of them reachable. Scoped to this file and this one
# rule; every other check, including Optional subscripts and arguments, stays
# on here and everywhere else.
# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

import asyncio
import dataclasses
import html
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

import auditlog
import background
import categories
import chatlog
import checkup
import ideas
import opsformat
import opsindex
import opsspend
import opsstats
import opssupport
import telegram
import trends
from opsstats import STATS_TTL

if TYPE_CHECKING:
    # For the annotation only. The value arrives through `configure`, from the
    # one `ScanQuota` main builds, so the bot asks the quota what the welcome
    # is instead of working it out again.
    from quota import WelcomeSetting

log = logging.getLogger("snapworth.notify")


# The live scan feed: one message per successful scan, item and price only.
# Persisted in the cache so the toggle survives deploys. On by default — the
# operator asked for it — and one command away from quiet.
FEED_KEY = "opsfeed:enabled"

CATEGORY_EMOJI = {c.name: c.emoji for c in categories.CATEGORIES}

# The weekly report goes out with Monday's digest, covering the seven days
# that just ended against the seven before.
WEEKLY_REPORT_WEEKDAY = 0

# First-sighting record for an originalTransactionId. Matches the proof and
# device-binding horizon: past it the subscription itself is the bound.
SUB_SEEN_TTL = 60 * 60 * 24 * 400

# The day counters a subscription's first sighting and its first paid period
# feed (#218). `new_subs` is every first sighting, one per
# originalTransactionId, and is exactly `trial_starts + paid_direct +
# offer_starts`; `trial_conversions` is not a new subscription and is not in
# it. See `_count_new_subscription`.
START_COUNTERS = {"trial": "trial_starts", "paid": "paid_direct"}
OFFER_STARTS = "offer_starts"
TRIAL_CONVERSIONS = "trial_conversions"

# Every place the app can show a paywall: `PaywallTrigger`'s raw values in
# ios/SnapWorth/Services/Analytics.swift, copied exactly. `thrift_flip` is not
# here: it retired with #128, before any build sent a trigger. The set is
# closed because each value becomes a counter key; anything else a client
# sends is ignored, never stored.
PAYWALL_TRIGGERS: tuple[str, ...] = (
    "onboarding", "scan_limit", "upgrade_button", "settings", "ledger_history",
    "ledger_export", "snap_sell", "portfolio_trend", "valuation_detail",
    "trends", "add_tag", "haul",
)

# `/paywall`'s window. Inside STATS_TTL, so every day in it is still readable.
PAYWALL_WINDOW_DAYS = 28

# A lapsed install re-POSTs its expired transaction on every cold launch; one
# "subscription ended" note per subject per day is signal, more is noise.
DOWNGRADE_THROTTLE_TTL = 60 * 60 * 24

# Minimum gap between repeats of the same operational alert. A provider outage
# fails every scan; the first message is the alert, the rest would be a siren.
ALERT_MIN_INTERVAL_SECONDS = 30 * 60.0

DEFAULT_DIGEST_UTC_HOUR = 6

# A subscription first purchased within this window is a new customer. Older
# than that and the app is merely re-syncing a subscription this notifier has
# not announced before — which every existing subscriber does exactly once
# after the notifier deploys, and which is not a sale.
NEW_SUBSCRIPTION_WINDOW_SECONDS = 24 * 3600

# "Online" does not exist for this app: a phone talks to the backend for the
# seconds a scan takes and is otherwise silent. What can be counted honestly
# is distinct devices seen within a clock-aligned window. Fifteen minutes is
# short enough to mean "right now" and long enough to catch a scan session.
ACTIVE_WINDOW_SECONDS = 15 * 60


# Only one replica may poll getUpdates — Telegram rejects concurrent pollers
# and would hand each replica a random subset of messages. The lock is a
# cache NX write that the holder renews; a dead holder loses it within TTL.
POLL_LOCK_KEY = "opslock:tgpoll"
POLL_LOCK_TTL = 90
# How often a replica without the lock checks whether it has become free.
# Short, because this is exactly the gap between a deploy landing and the bot
# answering again: a cache read every fifteen seconds is nothing.
POLL_LOCK_RETRY_SECONDS = 15
# Ceiling on the failed-poll backoff, and the elapsed time above which an
# empty poll is a real long-poll timeout rather than a transport failure.
# See `_command_loop`.
POLL_BACKOFF_MAX_SECONDS = 60
POLL_LONG_ENOUGH_SECONDS = 10
# Where the poller left off, so a successor replica confirms what its
# predecessor already handled rather than being handed it again.
POLL_OFFSET_KEY = "opsstate:tgoffset"

# The deploy ping runs in the first seconds of a container's life, when the
# network is at its least reliable. It is also the one message whose absence
# is read as "the bot is broken", so it retries, with these pauses between
# attempts, before giving up.
DEPLOY_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0, 10.0)

# Quiet-hours watch. /health cannot see the outage where nobody can scan — the
# process is up, Redis answers, and the App Store build is broken — but a US
# app with zero successful scans for six hours of US daytime can. Checked every
# quarter hour; one note per day.
LAST_SCAN_KEY = "opsstate:lastscan"
QUIET_AFTER_SECONDS = 6 * 3600
QUIET_HOURS_UTC = frozenset(list(range(13, 24)) + [0, 1, 2, 3])   # ~9am–11pm Eastern
WATCH_INTERVAL_SECONDS = 15 * 60
# A day at or above this multiple of the trailing week's daily average earns a
# 🔥 line in the digest — with a floor, so 3 scans against 0.5 is not a spike.
SPIKE_FACTOR = 3.0
SPIKE_MIN_SCANS = 10

TREND_DAYS = 30

# The free-scan experiment, A-6 in docs/AUDIT-2026-09.md. `FREE_SCANS_FIRST_DAY`
# was armed 2026-09-07, but until 1.3.6 shipped a spent allowance was filed as a
# scan failure (I-23) and the client's own count could be wrong three separate
# ways (I-3, I-4, I-5) — so the window opens on approval day, not arming day.
EXPERIMENT_START_DAY = os.environ.get("EXPERIMENT_START_DAY", "20260910")
EXPERIMENT_END_DAY = os.environ.get("EXPERIMENT_END_DAY", "20260924")
# `limit_hits` only began counting at 18:29 UTC on this day, so that column
# covers about five and a half hours of it while every other column is a whole
# day. Marked in the table rather than dropped: the row is real, and the mark is
# what stops it being read as a full day's figure.
EXPERIMENT_PARTIAL_DAY = os.environ.get("EXPERIMENT_PARTIAL_DAY", "20260910")
SPARK = "▁▂▃▄▅▆▇█"

# Commands that need typed input, reachable from a button: the button sends a
# question with Telegram's reply box already open, the operator's reply comes
# back quoting that question, and the quote says which command it was for.
ASKS: dict[str, tuple[str, str]] = {
    # command: (question shown, placeholder in the reply box)
    "caption": ("✍️ /caption — what did you film? One line is enough.",
                "me scanning a $4 Patagonia fleece"),
    "hooks": ("✍️ /hooks — what is the video about?", "vintage Levi's"),
    "reply": ("✍️ /reply — paste the comment or review.", "paste it here"),
    "price": ("✍️ /price — describe the item: brand, model, size, condition.",
              "Carhartt Detroit jacket, brown duck, L, worn"),
    "trend": ("✍️ /trend — which brand or category?", "carhartt, or shoes"),
    "user": ("✍️ /user — the id from /users or /subs.", "a1b2c3"),
}
_ASK_QUOTE = re.compile(r"^✍️ /(\w+) —")

# What happened to the last deploy ping, so /status can answer "did it go
# out?" without anyone reading Railway logs.
LAST_DEPLOY_KEY = "opsstate:lastdeploy"

# When a signed App Store Server Notification last verified, for /checkup.
# `/apple/notifications` is the only route that withdraws a refund, and
# nothing else says when Apple last reached it.
LAST_APPSTORE_NOTIFICATION_KEY = "opsstate:lastasn"

COMMANDS: tuple[tuple[str, str], ...] = (
    ("status", "Active users, scans today, provider health"),
    ("subs", "Every subscription seen: plan, how obtained, renews"),
    ("sub", "/sub <id> — ask Apple for one subscriber's live status"),
    ("users", "Devices seen, 7-day and 30-day actives, most active"),
    ("costs", "Gemini spend: today, 7 and 30 days, per scan, vs MRR"),
    ("experiment", "Free-scan experiment: limit hits vs subscriptions, whole window; "
                   "/experiment export for a CSV to keep"),
    ("lever", "Arm or disarm the free-scan allowance without a redeploy"),
    ("paywall", "28 days: trial starts and direct purchases per paywall, trial → paid"),
    ("minbuild", "Tell app builds below a number to update, without a redeploy"),
    ("referrals", "Referrals on or off, and the oldest build shown them"),
    ("social", "TikTok: followers, likes and the latest videos"),
    ("finds", "Best finds this week: the most valuable scans"),
    ("post", "Three TikTok post ideas from what people scanned; add a topic"),
    ("calendar", "Seven days of posts planned from the week's data"),
    ("caption", "/caption <what you filmed> — hook, caption, hashtags"),
    ("hooks", "/hooks <topic> — ten opening lines"),
    ("reply", "/reply <paste a comment or review> — three replies"),
    ("price", "/price <item> — a text-only estimate, no photo"),
    ("trend", "/trend <brand or category> — 30 days of scans"),
    ("user", "/user <id> — one device's story, for support"),
    ("checkup", "Redis, Gemini, DeviceCheck, App Store, TLS expiry — one screen"),
    ("clear", "Delete the last two days of this chat — asks first; /history keeps the bot's side"),
    ("history", "/history [n] — what the bot said before the last clears"),
    ("feed", "Live scan feed: on, off, or show"),
    ("digest", "Yesterday's digest, now"),
    ("week", "Last 7 days against the 7 before"),
    ("help", "List commands"),
)

# Rows a /subs or /users table shows.
TABLE_ROWS = 20



# ── Module state, wired by `configure` from the app lifespan ─────────────────
_notifier: telegram.TelegramNotifier | None = None
_cache = None                                   # ResilientCache once configured
_digest_task: asyncio.Task | None = None
_command_task: asyncio.Task | None = None
_watch_task: asyncio.Task | None = None
_tasks = background.tasks          # every module's fire-and-forget work
_spawn = background.spawn

# Supplies the live process facts /status reports (commit, cache backend,
# auth enforcement, model health). Injected by main so this module never
# imports it.
_status_provider: Callable[[], dict] | None = None

# TikTok reader (social.Social), when configured.
_social = None

# Turns a prompt into model text, for /post. Injected by main so this module
# reuses the app's model, retry policy, metrics and cost tallies rather than
# growing a second Gemini client. `async (prompt, max_tokens) -> str`.
_generator: Callable[..., Awaitable[str]] | None = None

# Runs the real scan pipeline on a photo the operator sends the bot — the
# same code /scan runs after auth and quota, injected by main. `async (bytes,
# declared_type) -> dict` with the response's fields plus "elapsed".
_scanner: Callable[..., Awaitable[dict]] | None = None

# Asks Apple whether the DeviceCheck credentials actually sign, injected by
# main. `async () -> (ok, detail)`, where `ok` is None when Apple could not be
# asked (see `devicecheck.DeviceCheckClient.verify`); None when the app did
# not wire one.
_device_check_probe: Callable[[], Awaitable[tuple[bool | None, str]]] | None = None

# What the first-day welcome is, from the `ScanQuota` that grants it: its
# `describe_welcome`, injected by main. None when the app did not wire one,
# and then the bot says so rather than guess — see `_welcome_setting`.
_describe_welcome: Callable[[], Awaitable[WelcomeSetting]] | None = None

# Identifies this replica as the poll-lock holder.
_poll_token = secrets.token_hex(8)

# In-process alert throttling. Per-replica on purpose: an alert is about *this*
# process's view, and a duplicate from a second replica during an incident is
# an acceptable cost for not paying a cache round-trip on the failure path.
_alert_last_sent: dict[str, float] = {}
_alert_awaiting_recovery: set[str] = set()


def enabled() -> bool:
    return _notifier is not None


def configure(cache, notifier: telegram.TelegramNotifier | None = None,
              status_provider: Callable[[], dict] | None = None,
              social=None, generator: Callable[..., Awaitable[str]] | None = None,
              scanner: Callable[..., Awaitable[dict]] | None = None,
              device_check_probe: Callable[[], Awaitable[tuple[bool | None, str]]] | None = None,
              welcome: Callable[[], Awaitable[WelcomeSetting]] | None = None) -> None:
    """Wire the notifier from the environment. Called once at startup.

    With the env vars unset this leaves everything disabled and every public
    function a no-op — the feature costs nothing until it is turned on — apart
    from `opsstats.count_scan` and `scan_completed`'s tallies, which `/trends`
    reads, and the subscription row and counters `entitlement_recorded` and
    `subscription_event` write.
    """
    global _notifier, _cache, _status_provider, _social, _generator, _scanner
    global _device_check_probe, _describe_welcome
    _cache = cache
    opsstats.bind(cache)
    opsindex.bind(cache)
    opssupport.bind(cache)
    opsspend.bind(cache)
    trends.bind(cache)
    _status_provider = status_provider
    _social = social
    _generator = generator
    _scanner = scanner
    _device_check_probe = device_check_probe
    _describe_welcome = welcome

    if notifier is not None:
        _notifier = notifier
        _notifier.on_sent = _remember_message
        opsspend.bind(cache, _announce_over_budget)
    else:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not (token and chat_id):
            _notifier = None
            log.info("telegram alerts disabled — TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID unset")
            return
        _notifier = telegram.TelegramNotifier(token, chat_id)
        _notifier.on_sent = _remember_message
        opsspend.bind(cache, _announce_over_budget)

    _start_digest()
    _start_command_loop()
    _start_watch()
    # Take the default-scope menu down before publishing the chat-scoped one:
    # a chat scope does not replace a default scope, so without this the list
    # already live stays live for everyone.
    _spawn(_notifier.clear_default_commands())
    _spawn(_notifier.set_commands(COMMANDS))
    log.info("telegram alerts enabled", extra={"digest_utc_hour": _digest_hour()})


async def aclose() -> None:
    """Tear down background work. Alerts in flight at shutdown are dropped."""
    global _notifier, _digest_task, _command_task, _watch_task, _cache_settle_task
    if _digest_task is not None:
        _digest_task.cancel()
        _digest_task = None
    if _command_task is not None:
        _command_task.cancel()
        _command_task = None
    if _watch_task is not None:
        _watch_task.cancel()
        _watch_task = None
    for task in list(_tasks):
        task.cancel()
    _tasks.clear()
    _cache_settle_task = None
    _alert_last_sent.clear()
    _alert_awaiting_recovery.clear()
    await _release_poll_lock()
    if _notifier is not None:
        notifier, _notifier = _notifier, None
        await notifier.aclose()


# ── Daily counters ───────────────────────────────────────────────────────────

def count_limit_hit() -> None:
    """Tally one free user refused because the day's allowance was spent.

    The other half of the funnel. The client reports `free_scan_limit_hit` to
    TelemetryDeck, and `auth.enforce`'s refusal wrote an audit event and
    nothing countable — so the only instrument on the measurement that the
    FREE_SCANS_FIRST_DAY experiment turns on was the client's, with no way to
    cross-check it from the server.

    Deliberately not a scan failure: nothing reached the model, nothing was
    billed, and the user was told exactly what happened. Counting it beside
    `scans_failed` would make a working paywall look like an outage.
    """
    if _notifier is None:
        return
    _spawn(opsstats.bump("limit_hits"))


# The ways a scan that reached the model can still fail the user. They are
# counted apart because they call for different responses: "provider" is the
# AI service being down and nothing to do with the photo, "no price" usually
# is the photo, and "unreadable" means the model answered but not in JSON we
# could use even after the reformat retry. A bare "3 failed" cannot tell an
# operator which of those happened, which is the whole point of the line.
# "timed out" is the app's deadline passing before the model answered — a slow
# upload or a slow reply, and not by itself the provider being down.
#
# Note what is NOT here: an attestation refusal never reaches the model, so it
# 401s long before this counter and is not a scan failure in this sense.
SCAN_FAILURE_LABELS = {
    "provider": "provider",
    "unreadable": "unreadable",
    "no_price": "no price",
    "deadline": "timed out",
    "other": "other",
}


def count_scan_failure(kind: str = "other") -> None:
    """Tally one scan that reached the model and still failed the user.

    `scans_failed` stays the running total, so the digest, the weekly trend and
    every day already recorded stay continuous; the per-kind counter is the new
    detail beside it."""
    if _notifier is None:
        return
    _spawn(opsstats.bump("scans_failed"))
    _spawn(opsstats.bump(f"scans_failed_{kind if kind in SCAN_FAILURE_LABELS else 'other'}"))


async def _failure_breakdown(day: str) -> str:
    """"2 no price · 1 provider", commonest first; "" when nothing is tagged.

    Days recorded before the per-kind counters existed have a total but no
    parts, and read correctly as a plain "3 failed" rather than a wrong zero."""
    counts = []
    for kind, label in SCAN_FAILURE_LABELS.items():
        found = await opsstats.read_stat(day, f"scans_failed_{kind}")
        if found:
            counts.append((found, label))
    counts.sort(key=lambda c: (-c[0], c[1]))
    return " · ".join(f"{found} {label}" for found, label in counts)


# One 🚫 per device per day: the pause itself stops the traffic, and every
# further attempt from the same device says nothing new.
SAFETY_PAUSE_THROTTLE_TTL = 60 * 60 * 24


async def _announce_safety_pause(who: str, count: int) -> None:
    try:
        if not await _cache.add(f"opsseen:safety:{who}", "1", SAFETY_PAUSE_THROTTLE_TTL):
            return
    except Exception:
        return
    await _notifier.send(
        "🚫 <b>Device paused after repeated blocked photos</b>\n"
        f"<code>{html.escape(who[:8])}</code> sent {count} photos today that the safety "
        "filter refused. Its scans are refused for 24 hours; nothing was stored.",
        [[_device_button(who)], [("\U0001FA7A Checkup", "checkup")]])


def safety_blocked(subject: str, count: int, *, paused: bool) -> None:
    """The model's safety filter refused a photo. Tallied for the digest; the
    operator hears about it only when a device crosses the pause threshold."""
    if _notifier is None or _cache is None:
        return
    _spawn(opsstats.bump("scans_blocked"))
    if paused:
        _spawn(_announce_safety_pause(auditlog.pseudonymise(subject), count))


# ── Referrals (#97) ──────────────────────────────────────────────────────────

# The server's half of the referral funnel, in order. The app's events fire on
# taps — a code accepted, a redeem page opened — and an offer-code redemption
# never passes through the app's purchase flow, so only the server sees what
# Apple actually did. Worded as verbs after the number, so "1 claimed" and
# "12 claimed" both read.
REFERRAL_STEPS = (
    ("claimed", "claimed"),                    # a friend was handed an Apple code
    ("redeemed", "redeemed at Apple"),         # a friend-offer purchase arrived
    ("rewarded", "rewarded"),                  # a week was parked for a referrer
    ("paid", "paid after the free week"),      # that subscription was then paid for
)


def count_referral(step: str) -> None:
    """Tally one step of the referral funnel for the digest. Fire-and-forget."""
    if _notifier is None or step not in dict(REFERRAL_STEPS):
        return
    _spawn(opsstats.bump(f"referral_{step}"))


async def _referral_digest_line(day: str) -> str:
    """"Referrals: 3 claimed · 2 redeemed at Apple · …", or "" on a quiet day."""
    counts = [(await opsstats.read_stat(day, f"referral_{step}"), label)
              for step, label in REFERRAL_STEPS]
    if not any(n for n, _ in counts):
        return ""
    return "Referrals: " + " · ".join(f"{n} {label}" for n, label in counts)


def referral_pool_low(pool: str, remaining: int) -> None:
    """A referral code pool is down to `remaining` codes. Fire-and-forget.

    An empty pool used to be a log line and nothing else, while every claim
    answered "Invites are paused" — or, for the reward pool, a referrer's week
    went unissued. Said once per pool per UTC day at "low", and once more if it
    reaches empty, because a new batch is an App Store Connect chore the
    operator has to do by hand.
    """
    if _notifier is None or _cache is None:
        return
    _spawn(_announce_referral_pool(pool, remaining))


async def _announce_referral_pool(pool: str, remaining: int) -> None:
    state = "empty" if remaining <= 0 else "low"
    try:
        if not await _cache.add(f"opsseen:refpool:{pool}:{state}:{opsstats.day()}", "1",
                                STATS_TTL):
            return
    except Exception:
        return
    name = html.escape(pool)
    if pool == "friend":
        effect = ("Every invite claim now answers “Invites are paused”." if state == "empty"
                  else "When it runs out, every invite claim answers “Invites are paused”.")
    else:
        effect = ("A friend who redeems now earns their referrer nothing until it is "
                  "refilled; that friend's next sync retries." if state == "empty"
                  else "When it runs out, referrers stop receiving the weeks they earn.")
    headline = (f"🎟 <b>Referral {name} pool is empty</b>" if state == "empty"
                else f"🎟 <b>Referral {name} pool is low</b> — {remaining} left")
    await _notifier.send(
        f"{headline}\n{effect}\nGenerate a new batch of one-time codes for the "
        f"{name} offer in App Store Connect and load it with "
        "<code>backend/tools/load_referral_codes.py</code> (RUNBOOK §18).")


async def _referral_line() -> str:
    """The checkup's referral line: on or off, and what is left in each pool."""
    import referral            # not at the top: referral imports auth, which imports this
    state = await referral.describe()
    live = await referral.switched_on()
    try:
        # Required, or a Redis outage reads as (0, 0) from process memory and
        # is reported below as "no referral codes loaded" about intact pools.
        levels = {pool: await referral.pool_level(pool, _cache, required=True)
                  for pool in referral.POOLS}
    except Exception as exc:
        return f"Referrals: {state} · pools unreadable ({html.escape(type(exc).__name__)})"
    if not any(size for size, _ in levels.values()):
        line = f"Referrals: {state}" + (" · no codes loaded" if live else "")
        return line + ("\n⚠️ no referral codes loaded — every invite is refused"
                       if live else "")
    line = f"Referrals: {state} · " + " · ".join(
        f"{pool} codes {left} of {size} left" for pool, (size, left) in levels.items())
    if live:
        for pool, (_, left) in levels.items():
            if left <= referral.POOL_LOW_AT:
                line += (f"\n⚠️ {pool} pool at {left} — load a new batch before it runs out"
                         if left else f"\n⚠️ {pool} pool is empty")
    return line


# ── Subscription events ──────────────────────────────────────────────────────

async def appstore_test_notification(environment: str) -> str:
    """Apple's "Request a Test Notification" reached us. Say so, out loud.

    This is the only mechanism Apple provides for proving the integration end
    to end, and it is worth very little if the answer is a 200 nobody sees.
    Pushing it means the operator can request a test and watch their phone —
    the whole loop, without reading a log.
    """
    if _notifier is None:
        return "no notifier configured"
    if environment == "Sandbox":
        # The Sandbox route acts on refunds and revokes and nothing else, so
        # promising renewals here would describe a feed that does not exist.
        what = ("Sandbox refunds and revokes will now withdraw a tester's or "
                "reviewer's Pro. Nothing from Sandbox reaches /subs or "
                "the revenue figures.")
    else:
        what = ("Renewals, expiries and refunds will now arrive without "
                "waiting for anyone to open the app.")
    ok = await _notifier.send(
        "\u2705 <b>App Store Server Notifications are connected</b>\n"
        f"Apple delivered a test notification ({html.escape(environment)}). "
        + what,
        _SUBS_BUTTONS)
    return "sent" if ok else "send failed"


async def _note_appstore_notification(environment: str, notification_type: str | None) -> None:
    if _cache is None:
        return
    try:
        await _cache.set(LAST_APPSTORE_NOTIFICATION_KEY,
                         json.dumps([int(time.time()), str(environment)[:20],
                                     str(notification_type or "?")[:40]]),
                         opsindex.INDEX_TTL)
    except Exception as exc:
        log.debug("notification arrival note failed: %s", type(exc).__name__)


def appstore_notification_verified(environment: str, notification_type: str | None) -> None:
    """A signed App Store Server Notification verified. Fire-and-forget.

    Recorded for `/checkup`, whatever the notification turns out to be — a
    redelivery and a test are Apple reaching us too. If Apple's notifications
    stop, refunded subscribers keep Pro until their term ends, and without this
    no line on /checkup would change."""
    if _notifier is None or _cache is None:
        return
    _spawn(_note_appstore_notification(environment, notification_type))


async def subscription_event(note, *, reinstated=None) -> None:
    """Record one App Store Server Notification. Awaited, but never raises.

    This is the half of the picture the client cannot give us. `/auth/entitlement`
    only fires when the app runs, which made two things invisible:

      * a trial converting to paid — the row kept the trial's expiry, went past
        it, and read as churn. The customer least likely to relaunch the app is
        exactly the one who just started paying.
      * a subscriber whose device never synced at all.

    Apple sends these whether or not anyone opens the app, so the row is now
    written by whoever finds out first.

    Nothing here grants access. The index and the alerts are an operator view;
    entitlement stays verified per request against the transaction the client
    presents.

    `reinstated` is the `entitlements.Reinstatement` that
    `EntitlementService.reinstate` answered for a REFUND_REVERSED — whether
    the access path lifted a refund block on this term, held none, or kept one
    that still denies it — and None for every other type.

    Gated on the cache, not on Telegram: the row and the trial and paid
    counters are written either way (#218), and only the alert waits for the
    bot.
    """
    if _cache is None:
        return
    try:
        ent = note.entitlement
        otid = ent.original_transaction_id
        if not otid or not note.is_indexed:
            return
        if opsindex.is_bounded(ent):
            # `/apple/notifications` refuses Sandbox and the Sandbox route
            # never calls this, so nothing should reach here. If something
            # does, it is a tester's renewal and not money.
            return
        if note.is_paid_period and _notifier is not None:
            # Still behind the bot, as it was: `referral.note_paid_period`
            # spends its once-per-transaction key even when `count_referral`
            # then counts nothing, so running it with Telegram off would lose
            # the friend's payment for good rather than wait for the bot.
            # A friend who took a referral week and then paid. Apple says so
            # whether or not they open the app again, which the sync cannot.
            import referral    # not at the top: referral imports auth, which imports this
            await referral.note_paid_period(ent)

        before = await opsindex.index_subscription(None, ent, note.auto_renew,
                                           current=note.is_refund_reversal)
        # None: the index could not be read, so there is no previous row to
        # judge a paid period against. It is then neither a conversion nor a
        # new payer — an ordinary renewal would otherwise be announced, and
        # counted, as "New paying subscriber".
        known = before is not None
        before = before or {}
        was = str(before.get("acq") or "") if before else ""
        now_acq = opsindex.acquisition(ent)

        product = html.escape(ent.product_id or "unknown product")
        environment = html.escape(ent.environment)
        detail = f"{product} ({environment})"
        price = getattr(ent, "price", None)
        if isinstance(price, (int, float)) and price > 0:
            detail += f" · {opsformat.money(price, getattr(ent, 'currency', None))}"

        lines: list[str] | None = None

        if note.is_paid_period and _started_as(before) == "trial":
            # Whichever half sees the first paid period counts it, once. The
            # device may have synced the paid transaction before Apple's
            # notification arrived, so `was` can already read "paid" here.
            await _count_trial_conversion(otid)

        if note.is_paid_period and was and was != "paid":
            # The headline event. We knew this subscription as a trial or a
            # comp; Apple has just charged for it. `was` is what makes this a
            # conversion rather than an ordinary renewal — the notification
            # itself cannot tell those apart, because they are identical.
            label = "Trial converted" if was == "trial" else f"{was.capitalize()} converted"
            lines = [f"🎉 <b>{label} — this is real money</b>", detail]
        elif note.is_paid_period and known and not before:
            # A payer no device ever synced. Before Apple told us directly,
            # this subscription did not exist as far as the bot was concerned.
            lines = ["🎉 <b>New paying subscriber</b> (Apple reported it first)", detail]
            await _count_new_subscription(otid, "paid")
        elif note.is_paid_period and not known:
            # Money, with nothing to say which kind. Staying silent lost the
            # conversion alert for good: the device's next sync rewrites the
            # row as paid without a word, and the subscription was already
            # seen as a trial. Not counted, because a renewal must not be.
            lines = ["💵 <b>Paid period</b> (subscription index unreadable: "
                     "a renewal, a conversion or a new payer)", detail,
                     "Not counted in today's paid or trial conversions."]
        elif note.is_refund:
            lines = ["↩️ <b>Refund</b>", detail]
        elif note.is_revoke:
            lines = ["🚫 <b>Subscription revoked</b>", detail]
        elif note.is_refund_reversal:
            # Apple took back a refund it had granted, so this term is paid
            # for again. The handler has already acted on the access path;
            # this says what it found there.
            from entitlements import Reinstatement
            lines = ["↪️ <b>Refund reversed by Apple</b>", detail]
            if reinstated is Reinstatement.LIFTED:
                lines.append("Refund block lifted: Pro comes back at the "
                             "app's next sync, if not sooner.")
            elif reinstated is Reinstatement.NOT_BLOCKED:
                lines.append("The server held no refund block on this term.")
            elif reinstated is Reinstatement.STILL_BLOCKED:
                # Kept because it names a different term, but that term ends
                # later, so the block still denies this one. Saying "no
                # block" here gave the operator no reason to look.
                lines.append(
                    "⚠️ A refund block for a different term was kept, and it "
                    f"still denies this one. <code>/sub {html.escape(otid)}</code> "
                    "shows it next to Apple's live status and can lift it.")
        elif note.is_expiry:
            lines = ["📉 <b>Subscription ended</b>", f"{detail} · was {was or now_acq}"]
        elif note.is_cancellation:
            # Not a loss yet — they keep it until the period ends. It is the
            # earliest warning of one that Apple gives.
            when = f" · runs until {opsformat.date(ent.expires_at)}" if ent.expires_at else ""
            lines = ["⚠️ <b>Auto-renew turned off</b>", f"{detail}{when}"]
        elif note.is_billing_failure:
            lines = ["💳 <b>Renewal payment failed</b>",
                     f"{detail} · Apple is retrying"]

        if lines is None or _notifier is None:
            return
        if ent.expires_at and not note.is_loss:
            # `note.auto_renew` is what this notification's own renewal info
            # says; `before` is what we last knew. Preferring the notification
            # matters on exactly the alert where it changed — a
            # DID_CHANGE_RENEWAL_STATUS whose headline is already "auto-renew
            # turned off" must not be followed by a line saying it renews.
            auto_renew = (note.auto_renew if note.auto_renew is not None
                          else before.get("auto_renew"))
            lines.append(opsformat.renewal_phrase(ent.expires_at, auto_renew))
        if not await _notifier.send("\n".join(lines), _SUBS_BUTTONS):
            log.warning("subscription notification alert failed to send")
    except Exception:
        # An operator ping must never fail Apple's delivery: a non-2xx makes
        # Apple retry the same notification for hours.
        log.exception("subscription notification handling failed")


def _started_as(row: dict | None) -> str | None:
    """How the subscription in `row` began: `opsindex.acquisition`'s word for its
    first sighting.

    A row written before `started_as` existed has only `acq`, the latest
    transaction's. That is still how it started as long as it has not been
    overwritten by a later kind, which is exactly the case this is asked
    about: a trial row about to see its first paid period.
    """
    if not row:
        return None
    return row.get("started_as") or row.get("acq") or None


async def _count_new_subscription(otid: str, acq: str) -> None:
    """Count one new subscription, once, whichever half found out first.

    There are two ways a subscription first becomes known: the client posts its
    signed transaction to `/auth/entitlement`, or Apple posts a notification to
    `/apple/notifications`. Only the first incremented the day's count, so a
    payer whose device never synced before Apple told us — the case
    `subscription_event` alerts on by name, "New paying subscriber (Apple
    reported it first)" — never appeared in it.

    `acq` is `opsindex.acquisition`'s word for the first transaction, and picks the
    counter: a free trial is `trial_starts`, a purchase with no offer
    `paid_direct`, and an intro, promo or offer-code start `offer_starts`.
    `new_subs` is still written, as their sum: one per customer, the meaning
    it always had in practice, so the days before the split and kept exports
    of them still compare. A trial *converting* is not a new subscription and
    is counted by `_count_trial_conversion` instead. This used to claim it
    counted conversions too; it never did in the real order, because the
    device syncs the trial first and spends the guard below (#218).

    `opsseen:subcount:{otid}` is the guard, and it is deliberately the *same*
    key both callers use: whichever path learns of a subscription first counts
    it, and the other finds the key already set and counts nothing.

    Never raises. A counter is not worth failing an alert or a scan over.
    """
    if not otid or _cache is None:
        return
    counter = START_COUNTERS.get(acq, OFFER_STARTS)
    try:
        if await _cache.add(f"opsseen:subcount:{otid}", "1", SUB_SEEN_TTL):
            day = opsstats.day()
            await _cache.incr(opsstats.stat_key(day, "new_subs"), STATS_TTL)
            await _cache.incr(opsstats.stat_key(day, counter), STATS_TTL)
    except Exception as exc:                      # pragma: no cover - defensive
        log.warning("%s counter failed for %s: %s", counter, otid, exc)


async def _count_paywall_trigger(otid: str, started_as: str | None,
                                 trigger: str | None) -> None:
    """Count the paywall a new subscription was bought from, once. Never raises.

    `trigger` is what `/auth/entitlement` accepted: one of `PAYWALL_TRIGGERS`,
    or None, which counts nothing and spends nothing. The first sync that
    carries a valid one counts it, under a guard of its own rather than
    `_count_new_subscription`'s, because the sync carrying it is often not the
    first sighting: dismissing the purchase sheet re-activates the app, whose
    foreground sync sends no trigger and can reach the server first, and
    Apple can report a direct purchase before either. On the shared guard
    that purchase's trigger would always be lost.

    `started_as` picks `trial_starts:<trigger>` or `paid_direct:<trigger>`: the
    row's, when an earlier sync wrote one, else the transaction's own. An
    offer-code or intro start counts under neither. Only the count is kept;
    the trigger never reaches the row, which would link it to Purchase
    History (#218, Notes).
    """
    counter = START_COUNTERS.get(started_as or "")
    if not otid or _cache is None or trigger not in PAYWALL_TRIGGERS or counter is None:
        return
    try:
        if await _cache.add(f"opsseen:subtrigger:{otid}", "1", SUB_SEEN_TTL):
            await _cache.incr(opsstats.stat_key(opsstats.day(), f"{counter}:{trigger}"), STATS_TTL)
    except Exception as exc:                      # pragma: no cover - defensive
        log.warning("paywall trigger counter failed for %s: %s", otid, exc)


async def _count_trial_conversion(otid: str) -> None:
    """Count a trial's first paid period, once. Never raises.

    Called by whichever half sees it: Apple's DID_RENEW, or the device syncing
    the paid transaction first. Its own guard, not `_count_new_subscription`'s,
    which the trial's start has already spent."""
    if not otid or _cache is None:
        return
    try:
        if await _cache.add(f"opsseen:subconv:{otid}", "1", SUB_SEEN_TTL):
            await _cache.incr(opsstats.stat_key(opsstats.day(), TRIAL_CONVERSIONS), STATS_TTL)
    except Exception as exc:                      # pragma: no cover - defensive
        log.warning("trial_conversions counter failed for %s: %s", otid, exc)


async def entitlement_recorded(subject: str, ent, *,
                               paywall_trigger: str | None = None) -> None:
    """Note a verified StoreKit transaction. Awaited, but never raises.

    Called from /auth/entitlement, which fires at cold launch, purchase,
    restore and Transaction.updates — so almost every call is a re-sync of a
    subscription already seen. The cache keeps this quiet: a Pro result only
    alerts on the first sighting of its originalTransactionId (renewals and
    re-syncs share it), and a not-Pro result — a refund, revocation or expiry
    the client just proved — alerts at most once per subject per day.

    The row and the trial and paid counters are written whenever there is a
    cache. They returned early with Telegram unset, as `/trends`' tallies did
    before #190, so switching the bot off would have stopped the count the
    trial experiment is read on without a word (#218). Everything that is a
    message still waits for the bot.

    `paywall_trigger` is what `/auth/entitlement` accepted from the app: one
    of `PAYWALL_TRIGGERS`, or None.
    """
    if _cache is None:
        return
    if _notifier is not None:
        _spawn(opsindex.note_sync(subject, "pro" if ent.tier == "pro" else "free"))
    if opsindex.is_bounded(ent):
        # App Review or a TestFlight tester, honoured on bounded terms. Pro
        # for that device and nothing more: no row, no count, no "New Pro",
        # and no "Subscription ended" when their transaction lapses.
        return
    try:
        if ent.tier == "pro":
            otid = ent.original_transaction_id
            if not otid:
                return
            # The previous row is the only source of auto-renew here: the
            # client presents a signed transaction, which has no such field.
            # None when the index could not be read: then there is no row to
            # say this was a trial, and no conversion is counted.
            previous = await opsindex.index_subscription(subject, ent)
            before = previous or {}
            purchased = getattr(ent, "original_purchase_at", None)
            # Unknown purchase date reads as new: Apple always supplies it, so
            # its absence is a test fixture, not a customer.
            is_new = (purchased is None
                      or time.time() - purchased < NEW_SUBSCRIPTION_WINDOW_SECONDS)
            acq = opsindex.acquisition(ent)
            if is_new:
                # Counted before, and apart from, the alert's guard below.
                #
                # That guard is handed back when the alert fails to send, so a
                # counter behind it would count one sale again at every
                # re-sync inside the 24-hour window; and the sync carrying the
                # trigger is often not the first (see
                # `_count_paywall_trigger`). Each counter has its own
                # once-per-transaction key. The trigger is only counted for a
                # new subscription, as the totals are, so the per-trigger
                # rows never add up to more than they do.
                await _count_new_subscription(otid, acq)
                await _count_paywall_trigger(
                    otid, _started_as(previous) or acq, paywall_trigger)
            if acq == "paid" and _started_as(previous) == "trial":
                # The device synced the first paid period before Apple's
                # notification arrived, or instead of it.
                await _count_trial_conversion(otid)
            if _notifier is None:
                return
            if not await _cache.add(f"opsseen:sub:{otid}", "1", SUB_SEEN_TTL):
                return
            if is_new:
                headline = "🎉 <b>New Pro subscription</b>"
            else:
                headline = ("👋 <b>Existing Pro subscriber checked in</b> "
                            "(first time this bot has seen them)")
            product = html.escape(ent.product_id or "unknown product")
            environment = html.escape(ent.environment)
            lines = [headline, f"{product} ({environment}) · {acq}"]
            if purchased is not None:
                lines.append(f"first purchased {opsformat.date(purchased)}")
            if ent.expires_at:
                lines.append(opsformat.renewal_phrase(
                    ent.expires_at, before.get("auto_renew")))
            # The guard above was consumed *before* this send and its result
            # was discarded, so a Telegram failure burned a 400-day marker
            # and the alert for that sale was never seen. `_announce_deploy`
            # already hands its guard back on failure; this now does too.
            #
            # The sale itself was never lost — `opsindex.index_subscription` has
            # already run and `/subs` lists them — but the one push that says
            # "someone just paid you" was, silently.
            if not await _notifier.send("\n".join(lines), _SUBS_BUTTONS):
                log.warning("subscription alert failed to send, releasing the guard")
                try:
                    await _cache.delete(f"opsseen:sub:{otid}")
                except Exception:
                    pass
        else:
            if _notifier is None:
                return
            if not await _cache.add(
                    f"opsseen:down:{subject}", "1", DOWNGRADE_THROTTLE_TTL):
                return
            pseudonym = auditlog.pseudonymise(subject)
            who = html.escape(pseudonym)
            await _notifier.send(
                "⚠️ <b>Subscription ended</b>\n"
                f"Subject <code>{who}</code> presented a transaction that "
                "verified as not-Pro — refunded, revoked or expired.")
    except Exception as exc:
        log.warning("subscription alert failed: %s", type(exc).__name__)


def entitlement_rejected(subject: str, reason: str) -> None:
    """/auth/entitlement refused a signed transaction. Fire-and-forget.

    Not an alert — a malformed or Sandbox transaction is routine noise — but
    recorded on the device, so that when a customer writes "I paid and the
    app says free", `/user` can say the purchase reached the server and why
    it was turned away."""
    if _notifier is None or _cache is None or not subject:
        return
    _spawn(opsindex.note_sync(subject, "rejected", reason))


# ── Subscription sharing signal ──────────────────────────────────────────────

# An evicted device seen this recently was still in use: that is concurrent
# sharing, not a phone that was replaced months ago and finally aged out.
SHARING_RECENT_SECONDS = 7 * 24 * 3600

# One sharing note per subscription per day. Sharing shows up as steady churn,
# and every eviction after the first says nothing new.
SHARING_THROTTLE_TTL = 60 * 60 * 24


async def _announce_over_cap(otid: str, product_id: str | None,
                             idle_seconds: int, max_devices: int) -> None:
    try:
        if not await _cache.add(f"opsseen:cap:{otid}", "1", SHARING_THROTTLE_TTL):
            return
    except Exception as exc:
        log.debug("sharing alert guard failed, skipping: %s", type(exc).__name__)
        return
    product = html.escape(product_id or "unknown product")
    hours = max(1, idle_seconds // 3600)
    await _notifier.send(
        "🔁 <b>Subscription over the device cap</b>\n"
        f"{product}: more than {max_devices} devices active. Evicted one last "
        f"seen {hours}h ago — likely sharing, not a replaced phone.",
        _SUBS_BUTTONS)


def subscription_over_cap(original_transaction_id: str, product_id: str | None,
                          *, idle_seconds: int, max_devices: int) -> None:
    """The device cap evicted a device that was recently in use.

    Called from the entitlement binding path, so it must cost nothing there:
    the cache guard and the send both run in the background. Long-idle
    evictions are not reported — that is a replaced device, which is what the
    idle prune exists for, not sharing.
    """
    if _notifier is None or _cache is None or not original_transaction_id:
        return
    if idle_seconds >= SHARING_RECENT_SECONDS:
        return
    _spawn(_announce_over_cap(
        original_transaction_id, product_id, idle_seconds, max_devices))


# ── Deploy ping ──────────────────────────────────────────────────────────────

_MERGE_SUBJECT = re.compile(r"^Merge pull request #(\d+) from \S+\s*$")
DEPLOY_BODY_CHARS = 600


def _deploy_text(commit: str, cache_backend: str, auth_enforcing: bool,
                 info: dict | None) -> str:
    """The deploy message: what went live, then where it is running.

    A merge commit's subject is boilerplate ("Merge pull request #80 from …")
    and its body is the PR title, so the message leads with "#80 <title>" and
    links the PR. A direct commit leads with its own subject and carries the
    first paragraph of its body. Either way the SHA, cache backend and auth
    posture follow — those are the facts worth checking on every deploy.
    """
    info = info or {}
    message = str(info.get("message") or "").strip()
    subject, _, body = message.partition("\n")
    body = body.strip()
    repo = str(info.get("repository") or "")

    lines = ["🚀 <b>Backend deployed</b>"]
    merge = _MERGE_SUBJECT.match(subject)
    if merge:
        number = merge.group(1)
        title = body.split("\n", 1)[0].strip() or subject
        label = f"#{number} {html.escape(title)}"
        if repo:
            label = f'<a href="https://github.com/{html.escape(repo)}/pull/{number}">#{number}</a> {html.escape(title)}'
        lines.append(f"<b>{label}</b>" if not repo else label)
    elif subject:
        lines.append(f"<b>{html.escape(subject)}</b>")
        paragraph = body.split("\n\n", 1)[0].strip()
        if paragraph:
            if len(paragraph) > DEPLOY_BODY_CHARS:
                paragraph = paragraph[:DEPLOY_BODY_CHARS].rstrip() + "…"
            lines.append(html.escape(" ".join(paragraph.split())))

    facts = []
    files = info.get("files")
    if isinstance(files, int) and files > 0:
        facts.append(f"{files} file{'s' if files != 1 else ''}")
    facts.append(f"commit <code>{html.escape(commit)}</code>")
    facts.append(f"cache {html.escape(cache_backend)}")
    facts.append("auth enforcing" if auth_enforcing else "auth NOT enforcing")
    lines.append(" · ".join(facts))
    return "\n".join(lines)


async def _announce_deploy(commit: str, cache_backend: str, auth_enforcing: bool,
                           info: dict | None) -> None:
    guard = f"opsseen:deploy:{commit}"
    try:
        # A rollback is a deploy. The guard is keyed on the commit and lives
        # 35 days, so rolling back to a build deployed inside that window used
        # to be completely silent — no ping, and /status went on describing
        # the commit that had just been rolled *out* of. Clearing the guard
        # when the recorded deploy is a different commit makes any transition
        # announce itself, in either direction.
        if str((await _last_deploy_record() or {}).get("commit") or "") not in ("", commit):
            await _cache.delete(guard)
        # Otherwise: one ping per commit, however many replicas boot it or
        # however often Railway restarts the container. A build that
        # crash-loops has other symptoms; a stream of identical "deployed"
        # messages would only bury them.
        if not await _cache.add(guard, "1", STATS_TTL):
            return
    except Exception as exc:
        # The cache is not up yet — which, seconds into a boot, it may well
        # not be. A duplicate ping is a shrug; a missing one is "did the
        # deploy land?" asked over and over. Send anyway.
        log.warning("deploy ping guard failed, sending anyway: %s", type(exc).__name__)
    text = _deploy_text(commit, cache_backend, auth_enforcing, info)
    notifier = _notifier
    # Built once, outside the loop: this send is retried up to four times and
    # the keyboard is identical on every attempt.
    buttons = _DEPLOY_BUTTONS
    for attempt, delay in enumerate((0.0, *DEPLOY_RETRY_DELAYS)):
        if delay:
            await asyncio.sleep(delay)
        if notifier is None or await notifier.send(text, buttons):
            await _record_deploy(commit, sent=True, attempts=attempt + 1)
            return
        log.warning("deploy ping attempt %d failed", attempt + 1)
    await _record_deploy(commit, sent=False, attempts=len(DEPLOY_RETRY_DELAYS) + 1)
    # Every attempt failed: give the guard back so the next boot of this same
    # commit — a Railway restart, say — gets to try again.
    try:
        await _cache.delete(guard)
    except Exception:
        pass


async def _record_deploy(commit: str, *, sent: bool, attempts: int) -> None:
    try:
        await _cache.set(LAST_DEPLOY_KEY, json.dumps(
            {"commit": commit, "sent": sent, "attempts": attempts, "at": int(time.time())}),
            STATS_TTL)
    except Exception as exc:
        log.debug("deploy record failed: %s", type(exc).__name__)


async def _last_deploy_record() -> dict | None:
    try:
        raw = await _cache.get(LAST_DEPLOY_KEY)
        rec = json.loads(raw) if raw else None
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


async def _deploy_line(current_commit: str | None) -> str:
    """One /status line: did this build's deploy ping go out, and when."""
    rec = await _last_deploy_record()
    if not rec:
        return "Deploy ping: no record for this build — it predates the record, or the ping never ran"
    when = datetime.fromtimestamp(int(rec.get("at") or 0), timezone.utc).strftime("%d %b %H:%M")
    same = current_commit and str(rec.get("commit")) == str(current_commit)
    build = "this build" if same else f"build <code>{html.escape(str(rec.get('commit') or '?'))}</code>"
    if rec.get("sent"):
        tries = int(rec.get("attempts") or 1)
        retry = f" after {tries} attempts" if tries > 1 else ""
        return f"Deploy ping: sent {when} UTC for {build}{retry}"
    return f"Deploy ping: FAILED {when} UTC for {build} — Telegram unreachable at boot"


def deployed(commit: str, *, cache_backend: str, auth_enforcing: bool,
             info: dict | None = None) -> None:
    """Announce that a new build is serving traffic.

    Answers "did the deploy land?" without a curl to /health, and doubles as a
    live check of the notifier itself on every release: if this message does
    not arrive, nothing else from this module will either. `info` is CI's
    BUILD_INFO — the commit message and change size — when it rode along.
    """
    if _notifier is None or _cache is None:
        return
    _spawn(_announce_deploy(commit, cache_backend, auth_enforcing, info))


# ── Operational alerts ───────────────────────────────────────────────────────

def _alert(key: str, text: str, buttons: opsformat.Buttons | None = None) -> None:
    if _notifier is None:
        return
    now = time.monotonic()
    last = _alert_last_sent.get(key)
    if last is not None and now - last < ALERT_MIN_INTERVAL_SECONDS:
        return
    _alert_last_sent[key] = now
    _alert_awaiting_recovery.add(key)
    # opsformat.Buttons are passed in rather than built here: these two are sync and hand
    # the send to `_spawn`, so anything awaited would have to move inside the
    # coroutine. Every keyboard an alert wants is static, so there is nothing
    # to await.
    _spawn(_notifier.send(text, buttons))


def _recovered(key: str, text: str, buttons: opsformat.Buttons | None = None) -> None:
    """Send the all-clear — only if the matching alert actually went out."""
    if _notifier is None or key not in _alert_awaiting_recovery:
        return
    _alert_awaiting_recovery.discard(key)
    # Clear the throttle so a relapse alerts immediately rather than being
    # mistaken for a repeat of the incident that just ended.
    _alert_last_sent.pop(key, None)
    _spawn(_notifier.send(text, buttons))


def model_unhealthy(kind: str | None) -> None:
    """The AI provider stopped answering — the same state /health reports."""
    reason = html.escape(kind or "unknown")
    extra = ""
    if kind == "quota_exhausted":
        extra = "\nThis one will not self-heal: top up the provider's billing."
    _alert("model",
           f"🔴 <b>AI provider degraded</b>\nScans are failing ({reason})."
           f"{extra}", _HEALTH_BUTTONS)


def model_recovered() -> None:
    _recovered("model", "🟢 <b>AI provider recovered</b> — scans are succeeding again.",
               _HEALTH_BUTTONS)


# How long Redis must stay down (or back up) before it is announced. The cache
# reports every transition, and one timeout under load is a transition; so is
# every request against a Redis that answers reads but refuses writes, which
# flips down and up per call. A state that does not hold for this long says
# nothing, rather than a siren of down/up pairs. The write-refusing case is
# left to /health/ready and the outside uptime check that reads it
# (RUNBOOK §3).
CACHE_ALERT_SETTLE_SECONDS = 60.0
_cache_state_generation = 0
# The one settle still waiting. Each transition cancels it before starting the
# next: left asleep, superseded settles piled up at transitions/s × 60 — about
# 12,000 live tasks at 100 req/s against a write-refusing Redis, during the
# very memory-pressure incident being announced.
_cache_settle_task: asyncio.Task | None = None


def cache_state_changed(degraded: bool) -> None:
    """`ResilientCache.on_change`, wired by main: Redis stopped or started
    answering. Schedules the announcement; never sends from inside the call."""
    global _cache_state_generation, _cache_settle_task
    if _notifier is None:
        return
    _cache_state_generation += 1
    if _cache_settle_task is not None:
        _cache_settle_task.cancel()       # a no-op once it has run
    _cache_settle_task = _spawn(_settle_cache_state(_cache_state_generation, degraded))


async def _settle_cache_state(generation: int, degraded: bool) -> None:
    await asyncio.sleep(CACHE_ALERT_SETTLE_SECONDS)
    if generation != _cache_state_generation:
        return                            # it changed again inside the window
    if degraded:
        # Before this, a Redis outage announced nothing: the cache logged and
        # fell back, free scans and token mints failed closed with a 503, no
        # model call was made so the model alert never fired, and the quiet
        # check read its timestamp from the empty fallback and stayed silent.
        _alert("cache",
               "🔴 <b>Redis unreachable</b>\nQuota and entitlement checks fail "
               "closed: free scans and token mints return 503 until it answers. "
               "RUNBOOK §5.4.", _HEALTH_BUTTONS)
    else:
        _recovered("cache", "🟢 <b>Redis recovered</b> — cache calls are succeeding again.",
                   _HEALTH_BUTTONS)


# ── Daily digest ─────────────────────────────────────────────────────────────

def _digest_hour() -> int:
    try:
        hour = int(os.environ.get(
            "TELEGRAM_DIGEST_UTC_HOUR", str(DEFAULT_DIGEST_UTC_HOUR)))
    except ValueError:
        return DEFAULT_DIGEST_UTC_HOUR
    return min(23, max(0, hour))


def _seconds_until_next(hour: int, now: datetime) -> float:
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _sub_counts(days: list[str]) -> tuple[int, int, int]:
    """(trial starts, trial conversions, direct purchases), summed over `days`."""
    trials = conversions = direct = 0
    for day in days:
        trials += await opsstats.read_stat(day, START_COUNTERS["trial"])
        conversions += await opsstats.read_stat(day, TRIAL_CONVERSIONS)
        direct += await opsstats.read_stat(day, START_COUNTERS["paid"])
    return trials, conversions, direct


def _subs_label(trials: int, conversions: int, direct: int) -> str:
    """"Trial starts: 2 · paid: 1 (1 converted trial · 0 direct)".

    Never "new subscriptions" (#218). That figure was trial starts plus
    direct purchases, and a trial converting — the one number a trial
    experiment is judged on — was in neither half of it. "Paid" is money:
    trials that converted and purchases with no trial."""
    paid = conversions + direct
    text = f"Trial starts: {trials} · paid: {paid}"
    if paid:
        text += f" ({conversions} converted trial{'s' if conversions != 1 else ''} · {direct} direct)"
    return text


async def send_digest(now: datetime | None = None) -> bool:
    """Send yesterday's digest. Returns whether this replica sent it.

    The cross-replica guard is a cache `add`: whichever process wins the NX
    write sends, the rest stand down. Sent even on an all-zero day — a quiet
    report and a broken notifier look identical otherwise.
    """
    if _notifier is None or _cache is None:
        return False
    now = now or datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    day = opsstats.day(yesterday)
    try:
        if not await _cache.add(f"opsstats:digestsent:{day}", "1", STATS_TTL):
            return False
    except Exception as exc:
        log.warning("digest guard failed, skipping: %s", type(exc).__name__)
        return False

    return await _notifier.send(await _digest_text(yesterday), await _buttons())


async def _digest_text(when: datetime) -> str:
    day = opsstats.day(when)
    free = await opsstats.read_stat(day, "scans_free")
    pro = await opsstats.read_stat(day, "scans_pro")
    failed = await opsstats.read_stat(day, "scans_failed")
    why = await _failure_breakdown(day) if failed else ""
    blocked = await opsstats.read_stat(day, "scans_blocked")
    trials, conversions, direct = await _sub_counts([day])
    users = await opsstats.read_stat(day, "active_users")
    limits = await opsstats.read_stat(day, "limit_hits")
    lines = [
        f"📊 <b>SnapWorth — {when.strftime('%Y-%m-%d')}</b>",
        f"Active users: {users}",
        f"Scans: {free + pro} ok ({free} free · {pro} Pro) · {failed} failed"
        + (f" ({why})" if why else "")
        + (f" · {blocked} blocked by the safety filter" if blocked else ""),
        # The line the free-scan experiment is read on. Against the day's
        # trial starts and direct purchases — the two ways to act on a paywall
        # — it is the first server-side answer to "does hitting the limit
        # move anyone". Omitted entirely on a day with none, so a quiet day
        # stays quiet.
        *([f"Free limit reached: {limits}"
           + (f" · {trials} trial start{'s' if trials != 1 else ''}"
              f" · {direct} paid directly" if trials or direct
              else " · no trial starts or purchases")]
          if limits else []),
        _subs_label(trials, conversions, direct),
        await _subscribers_line(),
        await opsspend.spend_line([day], free + pro),
    ]
    referrals = await _referral_digest_line(day)
    if referrals:
        lines.append(referrals)
    top = await _top_text(day)
    if top:
        lines.append(top)
    spike = await _spike_line(when, free + pro)
    if spike:
        lines.append(spike)
    social = await _social_line()
    if social:
        lines.append(social)
    return "\n".join(lines)


async def _digest_loop() -> None:
    # Catch up before sleeping. This loop used to sleep first unconditionally,
    # so a process that restarted across the digest hour — a deploy, a Railway
    # restart — skipped that day entirely, and a restart on a Monday took the
    # weekly report with it. Nothing reported the gap; the digest simply never
    # arrived.
    #
    # Safe to attempt: `send_digest` and `send_weekly` are both guarded by a
    # per-day cache `add`, which is what already stops two replicas
    # double-sending, so a catch-up that is not needed is a no-op.
    await _catch_up_digest()

    while True:
        await asyncio.sleep(
            _seconds_until_next(_digest_hour(), datetime.now(timezone.utc)))
        try:
            await send_digest()
            now = datetime.now(timezone.utc)
            if now.weekday() == WEEKLY_REPORT_WEEKDAY:
                await send_weekly(now)
        except Exception as exc:          # the loop must outlive any one send
            log.warning("digest send failed: %s", type(exc).__name__)


# Set the first time the digest loop runs against a given cache. Its presence
# is what distinguishes "this process restarted" from "this cache has never
# seen the loop", which is the difference between a digest that was missed and
# one that was never due.
DIGEST_LOOP_SEEN_KEY = "opsstate:digestloopseen"


async def _catch_up_digest() -> None:
    """Send today's digest if its hour has already passed and it never went."""
    now = datetime.now(timezone.utc)
    if now.hour < _digest_hour():
        return                            # not due yet today; nothing missed
    try:
        # A fresh cache means a fresh deployment, not a restart: there is no
        # missed digest to send and no data to send it from. Only a cache that
        # has already seen this loop can tell us the process went away.
        if await _cache.add(DIGEST_LOOP_SEEN_KEY, "1", STATS_TTL):
            return
    except Exception:
        return
    try:
        if await send_digest(now):
            log.info("sent a digest missed while this process was down")
        if now.weekday() == WEEKLY_REPORT_WEEKDAY and await send_weekly(now):
            log.info("sent a weekly report missed while this process was down")
    except Exception as exc:
        log.warning("digest catch-up failed: %s", type(exc).__name__)


def _start_digest() -> None:
    global _digest_task
    if _digest_task is not None:
        _digest_task.cancel()
        _digest_task = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # configure() outside a loop (tests, tooling): alerts still work from
        # any later loop via _spawn; only the scheduled digest needs one now.
        return
    _digest_task = loop.create_task(_digest_loop())


# ── Activity ─────────────────────────────────────────────────────────────────

def _window(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // ACTIVE_WINDOW_SECONDS)


def _window_start(window: int) -> datetime:
    return datetime.fromtimestamp(window * ACTIVE_WINDOW_SECONDS, timezone.utc)


async def _note_activity(subject: str, tier: str = "free") -> None:
    try:
        # The pseudonym, never the raw subject: these keys outlive the request
        # and the audit log already decided what identity is allowed to persist.
        who = auditlog.pseudonymise(subject)
        window = _window()
        if await _cache.add(f"opsseen:w:{window}:{who}", "1", 2 * ACTIVE_WINDOW_SECONDS):
            await _cache.incr(f"opsact:w:{window}", 2 * ACTIVE_WINDOW_SECONDS)
            # Once per device per window, not per request, so the index
            # write stays rare on a busy device.
            await opsindex.index_user(who, tier=tier)
        day = opsstats.day()
        if await _cache.add(f"opsseen:d:{day}:{who}", "1", STATS_TTL):
            await _cache.incr(opsstats.stat_key(day, "active_users"), STATS_TTL)
    except Exception as exc:
        log.debug("activity note failed: %s", type(exc).__name__)


def saw_user(subject: str, tier: str = "free") -> None:
    """Count this device as active now and today. Fire-and-forget.

    Called on every authenticated request. Two cache writes per *new* device
    per window, both in the background — the request never waits.
    """
    if _notifier is None or _cache is None or not subject:
        return
    _spawn(_note_activity(subject, tier))


async def _read_int(key: str) -> int:
    try:
        return int(await _cache.get(key) or 0)
    except Exception:
        return 0


# ── Commands ─────────────────────────────────────────────────────────────────

def _help_text() -> str:
    lines = ["🤖 <b>SnapWorth bot</b>"]
    lines += [f"/{c} — {html.escape(d)}" for c, d in COMMANDS]
    return "\n".join(lines)


def _replica_label(info: dict) -> str:
    """` · replica <id>` for the build line, or nothing outside Railway.

    What /status and Checkup describe from memory — provider health, the
    alert throttle — is this replica's alone (RUNBOOK §11), so with two the
    answer is only readable next to which one gave it. Eight characters tell
    replicas apart.
    """
    replica = str(info.get("replica") or "").strip()
    return f" · replica <code>{html.escape(replica[:8])}</code>" if replica else ""


async def _status_text() -> str:
    now = datetime.now(timezone.utc)
    day = opsstats.day(now)
    window = _window()
    active_now = await _read_int(f"opsact:w:{window}")
    active_today = await opsstats.read_stat(day, "active_users")
    free = await opsstats.read_stat(day, "scans_free")
    pro = await opsstats.read_stat(day, "scans_pro")
    failed = await opsstats.read_stat(day, "scans_failed")
    why = await _failure_breakdown(day) if failed else ""
    blocked = await opsstats.read_stat(day, "scans_blocked")
    trials, conversions, direct = await _sub_counts([day])
    limits = await opsstats.read_stat(day, "limit_hits")

    lines = [
        "📡 <b>SnapWorth status</b>",
        f"Active users: {active_now} since {_window_start(window):%H:%M} UTC "
        f"· {active_today} today",
        f"Scans today: {free + pro} ok ({free} free · {pro} Pro) · {failed} failed"
        + (f" ({why})" if why else "")
        + (f" · {blocked} blocked" if blocked else ""),
        *([f"Free limit reached: {limits} today"] if limits else []),
        _subs_label(trials, conversions, direct) + " today",
        await _subscribers_line(),
        await opsspend.spend_line([day], free + pro),
    ]
    top = await _top_text(day)
    if top:
        lines.append(top)
    if _status_provider is not None:
        try:
            info = _status_provider()
        except Exception as exc:
            log.warning("status provider failed: %s", type(exc).__name__)
            info = {}
        if info:
            if info.get("model_healthy", True):
                model = "healthy"
            else:
                model = f"degraded ({html.escape(str(info.get('model_failure_kind') or 'unknown'))})"
            auth = "enforcing" if info.get("auth_enforcing") else "NOT enforcing"
            lines.append(f"AI provider: {model}")
            lines.append(
                f"Build <code>{html.escape(str(info.get('commit', '?')))}</code>"
                f"{_replica_label(info)} · "
                f"cache {html.escape(str(info.get('cache', '?')))} · auth {auth}")
            lines.append(await _deploy_line(str(info.get("commit") or "")))
    return "\n".join(lines)


# ── Keyboards for the messages nobody asked for ─────────────────────────────
#
# Nine send sites — every unsolicited push — went out with no keyboard at all.
# That is exactly the class of message read on a lock screen, and the class
# where the next step is obvious: a device to look up, a health screen to open,
# a bill to check. `_announce_safety_pause` even ended with the literal text
# "/user a1b2c3 for its history", asking the operator to retype six characters
# the message had just printed.
#
# `_handle_update` turns callback data into "/" + data, so every button here is
# an existing command and none of this needs new dispatch.


def _device_button(pseudonym: str) -> tuple[str, str]:
    """A tappable device id.

    Takes the *unescaped* pseudonym: button labels are plain text, and an
    HTML-escaped one would put "&amp;" on the key and in the command.
    """
    short = pseudonym[:6]
    return (f"\U0001F464 {short}", f"user {short}")


_HEALTH_BUTTONS: opsformat.Buttons = [[("\U0001FA7A Checkup", "checkup"),
                             ("\U0001F4B8 Costs", "costs")]]
_SUBS_BUTTONS: opsformat.Buttons = [[("\U0001F4B3 Subs", "subs"),
                           ("\U0001F465 Users", "users")]]
_COSTS_BUTTONS: opsformat.Buttons = [[("\U0001F4B8 Costs", "costs")]]
_DEPLOY_BUTTONS: opsformat.Buttons = [[("\U0001F4E1 Status", "status"),
                             ("\U0001FA7A Checkup", "checkup")]]
_FEED_BUTTONS: opsformat.Buttons = [[("\U0001F3C6 Finds", "finds"),
                           ("\U0001F515 Feed off", "feed off")]]


async def _buttons() -> opsformat.Buttons:
    feed = "🔕 Feed off" if await _feed_enabled() else "🔔 Feed on"
    return [[("🔄 Refresh", "status"), ("📊 Digest", "digest"), ("📈 Week", "week")],
            [("💳 Subs", "subs"), ("👥 Users", "users"), ("💸 Costs", "costs")],
            [("📣 Social", "social"), ("🏆 Finds", "finds"), ("📝 Post ideas", "post")],
            [("🗓 Calendar", "calendar"), ("🩺 Checkup", "checkup"), (feed, "feed toggle")],
            [("✍️ Caption", "ask caption"), ("🪝 Hooks", "ask hooks"), ("💬 Reply", "ask reply")],
            [("💵 Price", "ask price"), ("📈 Trend", "ask trend"), ("👤 User", "ask user")],
            [("🧹 Clear chat", "clear"), ("🗂 History", "history")]]


async def handle_command(text: str) -> str | None:
    """Reply text for one operator message, or None to stay silent."""
    reply = await handle_command_with_buttons(text)
    return reply[0] if reply else None


async def handle_command_with_buttons(text: str) -> tuple[str, opsformat.Buttons] | None:
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    parts = text.split()
    command = parts[0].lower().split("@", 1)[0]
    argument = parts[1].lower() if len(parts) > 1 else ""
    # Everything after the command, as typed — /post takes a free-text topic.
    rest = " ".join(parts[1:])
    if command == "/status":
        return await _status_text(), await _buttons()
    if command == "/digest":
        return (await _digest_text(datetime.now(timezone.utc) - timedelta(days=1)),
                await _buttons())
    if command == "/week":
        return await _weekly_text(datetime.now(timezone.utc)), await _buttons()
    if command == "/feed":
        return await _feed_command(argument), await _buttons()
    if command == "/subs":
        return await _subs_text(), await _buttons()
    if command == "/sub":
        text, offers, menu = await opssupport.sub_command(rest)
        return text, offers + (await _buttons() if menu else [])
    if command == "/users":
        return await _users_text(), await _buttons()
    if command == "/costs":
        return await opsspend.costs_text(), await _buttons()
    if command == "/experiment":
        if argument == "export":
            return await _experiment_export(), [[("🧪 Experiment", "experiment")]]
        current = (await _levers()).get("free_scans_first_day")
        return (await _experiment_text(),
                _lever_buttons(current) + [[("💾 Export CSV", "experiment export")]]
                + await _buttons())
    if command == "/lever":
        return await _lever_command(argument, rest)
    if command == "/paywall":
        return await _paywall_text(), await _buttons()
    if command == "/minbuild":
        return await _minbuild_command(argument, rest)
    if command == "/referrals":
        import referral        # not at the top: referral imports auth, which imports this
        return await referral.bot_command(argument, rest)
    if command == "/social":
        return await _social_text(), await _buttons()
    if command == "/finds":
        return await _finds_text(), await _buttons()
    if command == "/post":
        return await _post_text(rest), await _buttons()
    if command == "/calendar":
        return await _calendar_text(), await _buttons()
    if command == "/caption":
        return await _brief_text("caption", rest), await _buttons()
    if command == "/hooks":
        return await _brief_text("hooks", rest), await _buttons()
    if command == "/reply":
        return await _brief_text("reply", rest), await _buttons()
    if command == "/price":
        return await _brief_text("price", rest), await _buttons()
    if command == "/trend":
        return await _trend_text(rest), await _buttons()
    if command == "/user":
        return await opssupport.user_text(rest), await _buttons()
    if command == "/checkup":
        return await checkup.text(_checkup_wiring()), await _buttons()
    if command == "/history":
        return await _history_text(argument), await _buttons()
    return _help_text(), await _buttons()


# ── Poll loop ────────────────────────────────────────────────────────────────

async def _hold_poll_lock() -> bool:
    """True when this replica may poll. Cache trouble errs on polling:
    a duplicated reply beats a bot that never answers."""
    try:
        if await _cache.add(POLL_LOCK_KEY, _poll_token, POLL_LOCK_TTL):
            return True
        if await _cache.get(POLL_LOCK_KEY) == _poll_token:
            await _cache.set(POLL_LOCK_KEY, _poll_token, POLL_LOCK_TTL)
            return True
        return False
    except Exception:
        return True


async def _release_poll_lock() -> None:
    """Hand the poll lock back if this replica holds it.

    Called on shutdown. Without it the lock outlived the process for its full
    TTL, and the replacement replica — the one that just deployed — could not
    poll until it expired: a minute and a half of a bot that reads every
    button press and answers none of them, after every single release.
    """
    if _cache is None:
        return
    try:
        if await _cache.get(POLL_LOCK_KEY) == _poll_token:
            await _cache.delete(POLL_LOCK_KEY)
    except Exception as exc:
        log.debug("poll lock release failed: %s", type(exc).__name__)


async def _read_offset() -> int | None:
    try:
        raw = await _cache.get(POLL_OFFSET_KEY)
        return int(raw) if raw else None
    except Exception:
        return None


async def _remember_offset(offset: int | None) -> None:
    if offset is None:
        return
    try:
        await _cache.set(POLL_OFFSET_KEY, str(offset), opsindex.INDEX_TTL)
    except Exception as exc:
        log.debug("poll offset save failed: %s", type(exc).__name__)


async def poll_once(offset: int | None) -> tuple[int | None, int]:
    """One getUpdates round. Returns (next offset, messages handled).

    Only the operator's chat is answered. Anyone else who finds the bot gets
    nothing back — not even an error — so there is nothing to probe.

    The returned offset is also written to the cache. Telegram only treats an
    update as confirmed when a *later* poll carries the offset past it, so a
    replica that dies right after answering leaves its last batch unconfirmed
    — and a successor starting from nothing would be handed those commands
    again and answer them twice. Starting from the stored offset instead
    confirms them.
    """
    handled = 0
    before = offset
    for update in await _notifier.get_updates(offset):
        offset = int(update.get("update_id", 0)) + 1
        try:
            handled += await _handle_update(update)
        except Exception as exc:
            # The offset has already advanced past this update, and it stays
            # advanced. Previously the whole batch ran unguarded and
            # `_command_loop` caught at the batch level, so one raising update
            # — `"/ask "` with a trailing space was enough — meant the offset
            # was never stored, the same batch came back every 5s, and the bot
            # answered nothing until Telegram expired the update ~24h later or
            # someone bumped the offset by hand. One bad message must not be
            # able to silence the bot.
            log.warning("dropping update %s: %s", update.get("update_id"),
                        type(exc).__name__)
    if offset != before:
        await _remember_offset(offset)
    return offset, handled


async def _handle_update(update: dict) -> int:
    """Act on one update. Returns 1 if it was handled, 0 if ignored.

    Lifted out of `poll_once` so a single update can be wrapped in its own
    try/except: the offset must advance past a message that raises, or that
    one message silences the bot until Telegram expires it.
    """
    callback = update.get("callback_query")
    if callback:
        # A button press. It carries the message it was attached to, and
        # that message's chat is the one that must match.
        message = callback.get("message") or {}
        text = "/" + str(callback.get("data") or "")
    else:
        message = update.get("message") or {}
        text = message.get("text") or ""
        if str((message.get("chat") or {}).get("id", "")) == _notifier.chat_id \
                and message.get("message_id") is not None:
            await _remember_message(int(message["message_id"]))
        # A reply to one of the bot's own questions is the argument for
        # the command the question named.
        quoted = (message.get("reply_to_message") or {}).get("text") or ""
        asked = _ASK_QUOTE.match(quoted)
        if asked and text and not text.startswith("/"):
            text = f"/{asked.group(1)} {text}"

    chat_id = str((message.get("chat") or {}).get("id", ""))
    if chat_id != _notifier.chat_id:
        return 0
    if callback:
        await _notifier.answer_callback(str(callback.get("id", "")))
    if not callback and message.get("photo"):
        await _test_scan(message["photo"])
        return 1
    if text.startswith("/ask "):
        # `split(None, 1)[1]` raised IndexError on "/ask " with nothing after
        # it — the concrete case behind T-1. Handled here as well as by the
        # caller's guard, so it is a no-op rather than a dropped update.
        parts = text.split(None, 1)
        command = parts[1].strip().lower() if len(parts) > 1 else ""
        if command in ASKS:
            question, placeholder = ASKS[command]
            await _notifier.send(question, ask=placeholder)
            return 1
        return 0
    words = text.split()
    if words and words[0].split("@", 1)[0].lower() == "/clear":
        # Two taps, as /lever does: the button sits one away from 🗂 History
        # on every keyboard, and what it deletes cannot be brought back.
        if len(words) > 1 and words[1].lower() == "yes":
            await _clear_chat()
        else:
            await _notifier.send(*await _clear_prompt())
        return 1
    reply = await handle_command_with_buttons(text)
    if reply:
        await _notifier.send(reply[0], reply[1])
        return 1
    return 0



async def _command_loop() -> None:
    offset: int | None = await _read_offset()
    idle = 0
    while True:
        try:
            if not await _hold_poll_lock():
                await asyncio.sleep(POLL_LOCK_RETRY_SECONDS)
                continue
            before = offset
            started = time.monotonic()
            offset, _ = await poll_once(offset)
            elapsed = time.monotonic() - started
            if offset != before:
                idle = 0                  # real work; back to full speed
            elif elapsed >= POLL_LONG_ENOUGH_SECONDS:
                # A genuine long-poll timeout. It already spent 25s waiting,
                # so there is nothing to back off from and the next poll
                # should go straight out.
                idle = 0
            else:
                # Returned at once with nothing: a transport failure, not a
                # quiet chat. This used to sleep a flat 2s, so a Telegram
                # outage or a revoked token was retried ~43k times a day with
                # a WARNING each — WARNING is never sampled, by design, so the
                # flood buried exactly the signal it was reporting. Backing
                # off costs up to a minute of latency on the first command
                # after an outage ends, and only after an outage.
                idle += 1
                await asyncio.sleep(min(2 * idle, POLL_BACKOFF_MAX_SECONDS))
        except asyncio.CancelledError:
            raise
        except Exception as exc:           # the loop must outlive any one poll
            idle += 1
            log.warning("command loop error: %s", type(exc).__name__)
            await asyncio.sleep(min(5 * idle, POLL_BACKOFF_MAX_SECONDS))


def _start_command_loop() -> None:
    global _command_task
    if _command_task is not None:
        _command_task.cancel()
        _command_task = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _command_task = loop.create_task(_command_loop())


# ── Live scan feed and what people scan ──────────────────────────────────────

# ── The free-scan lever ─────────────────────────────────────────────────────
#
# `/experiment` could report that the lever was not armed and do nothing about
# it. The measurement half of the experiment lives here — `limit_hits`, the
# window, the partial-day handling — and the control half was a Railway
# variable and a redeploy, from a phone.
#
# `quota.ScanQuota` reads `free_scan_lever` on every free scan and falls back
# to the environment when it returns None or raises, so an unreadable lever can
# neither fail a scan nor grant an allowance nobody configured. The value is
# clamped there too: this is a button that spends money.
#
# What a value resolves to — the cap, the daily floor, the environment's say —
# is the quota's to answer, and `_welcome_setting` asks it. This module used to
# keep its own copy of each rule. The missing floor is the copy that made
# screens untrue — the lever's confirmation (fixed in 6cae388) and
# /experiment's lever line — while the default and the cap still matched,
# each one quota edit away from not matching.

LEVERS_KEY = "opsstate:levers"
LEVER_CHANGES_CAP = 40
DEFAULT_ARMED_FIRST_DAY = 3


async def _welcome_setting() -> WelcomeSetting | None:
    """The welcome as the quota resolves it, or None when it cannot be asked.

    None when main did not wire `describe_welcome` into this process. The bot
    then says it does not know and will not arm: answering from its own copy
    of the rules is what printed `FREE_SCANS_FIRST_DAY=1` — no welcome at a
    daily limit of 1 — as though the lever were armed.
    """
    if _describe_welcome is None:
        return None
    try:
        return await _describe_welcome()
    except Exception as exc:                    # pragma: no cover - defensive
        log.warning("welcome setting unreadable: %s", type(exc).__name__)
        return None


def _welcome_summary(setting: WelcomeSetting | None) -> tuple[bool, str, str]:
    """(armed, head, why) — whether a new user gets a first-day welcome now.

    Plain text, so the CSV export can carry it; `_welcome_html` marks it up.
    It reports what the quota grants and then what was asked for, because the
    two differ exactly when the operator most needs to know it: a value at or
    below the daily limit is asked for and grants nothing.
    """
    if setting is None:
        return False, "welcome unknown", "the quota is not wired into the bot in this process"
    env = f"FREE_SCANS_FIRST_DAY={setting.environment}"
    chat = setting.override is not None
    if setting.scans:
        why = (f"{setting.scans} first-day scan{'s' if setting.scans != 1 else ''}, "
               + ("set from chat" if chat else f"from {env}"))
        if setting.configured > setting.cap:
            why += f" (asked for {setting.configured}, capped at {setting.cap})"
    elif setting.configured <= 0:
        why = "disarmed from chat" if chat else f"{env}, or unset"
    else:
        asked = f"the lever's {setting.configured}, set from chat," if chat else env
        if setting.configured > setting.daily:
            # Above the daily limit as asked, so the cap is what took it away:
            # clamped to a cap no higher than the daily limit. Saying the
            # value is "not above the daily limit" would be false.
            why = (f"{asked} is capped at {setting.cap}, which is not above the "
                   f"daily limit of {setting.daily}, so no first-day welcome")
        else:
            why = (f"{asked} is not above the daily limit of {setting.daily}, "
                   "so no first-day welcome")
    if chat and setting.environment != setting.override:
        # Worth printing: the environment is what ↩️ Use env hands back to.
        why += f" · env {env}"
    return bool(setting.scans), "lever armed" if setting.scans else "lever not armed", why


def _welcome_html(setting: WelcomeSetting | None) -> str:
    armed, head, why = _welcome_summary(setting)
    return f"{head if armed else f'<b>{head}</b>'} — {html.escape(why)}"


async def _levers(*, required: bool = False) -> dict:
    """The levers document. {} when unreadable, unless `required`, which
    raises instead: see `_set_free_scan_lever`."""
    try:
        raw = await _cache.get(LEVERS_KEY, required=required)
    except Exception:
        if required:
            raise
        return {}
    try:
        doc = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def _lever_value(doc: dict) -> int | None:
    """The welcome allowance a levers document holds, or None for none."""
    value = doc.get("free_scans_first_day")
    return int(value) if isinstance(value, (int, float)) else None


async def free_scan_lever() -> int | None:
    """The operator's welcome allowance, or None to use the environment.

    Injected into `ScanQuota` from main.py — quota must not import this module.
    Raises nothing: `_levers` swallows, and a missing key reads as None.
    """
    return _lever_value(await _levers())


async def _set_free_scan_lever(value: int | None) -> dict | None:
    """Set or clear the lever, and record the day it changed.

    The record is the point. A measurement window whose lever moved mid-flight
    and does not say so is worse than no window at all — the numbers look
    continuous and are not.

    None, with nothing written, when the document could not be read. Read as
    {} it was written back as just this change, and the record of every
    earlier one was gone.
    """
    try:
        doc = await _levers(required=True)
    except Exception as exc:
        log.warning("levers unreadable, not changing them: %s", type(exc).__name__)
        return None
    before = doc.get("free_scans_first_day")
    if value is None:
        doc.pop("free_scans_first_day", None)
    else:
        doc["free_scans_first_day"] = int(value)
    changes = [c for c in (doc.get("changes") or []) if isinstance(c, list) and len(c) == 3]
    changes.append([opsstats.day(), before, value])
    doc["changes"] = changes[-LEVER_CHANGES_CAP:]
    await _cache.set(LEVERS_KEY, json.dumps(doc))
    return doc


def _lever_label(value: int | None) -> str:
    if value is None:
        return "environment default"
    return f"{value} first-day scan{'s' if value != 1 else ''}"


_LEVER_UNREADABLE = ("🎚 Nothing changed: the lever's stored state could not be "
                     "read, and writing over it would lose its change history. "
                     "Try again in a minute.")

_LEVER_UNWIRED = ("🎚 Nothing changed: the bot cannot ask the quota in this process "
                  "what an allowance would grant, and will not arm one blind.")


async def _lever_command(argument: str, rest: str) -> tuple[str, opsformat.Buttons]:
    """`/lever`, `/lever arm [n]`, `/lever disarm`, and their confirmations;
    `/lever plan …` is the paywall's default plan (`_plan_command`).

    Two taps, never one. The first names what is about to change and what it
    currently is; the second does it. A single-tap lever on a phone, in a chat
    that also contains the word "Disarm" one row away, is how an experiment
    gets restarted by accident halfway through.
    """
    parts = (rest or "").split()
    action = parts[0].lower() if parts else ""
    if action == "plan":
        return await _plan_command(parts)
    # Anywhere after the action, not at a fixed index: the arm button carries
    # the value it is confirming ("lever arm 3 yes"), so checking parts[1]
    # silently re-showed the confirmation instead of acting on it.
    confirmed = any(token.lower() == "yes" for token in parts[1:])
    current = (await _levers()).get("free_scans_first_day")

    setting = await _welcome_setting()

    if action == "arm":
        if setting is None:
            return _LEVER_UNWIRED, _lever_buttons(current)
        wanted = DEFAULT_ARMED_FIRST_DAY
        for token in parts[1:]:
            if token.isdigit():
                wanted = int(token)
        # The quota clamps to its cap *and then* discards anything at or below
        # the daily limit. This used to mirror only the clamp, so arming with 0
        # or 1 replied "Lever armed — 1 first-day scan" and `/lever` went on
        # rendering that override, from a stored document with no TTL. Asked
        # rather than restated now, and refused where the operator can see it
        # rather than clamped silently.
        wanted = min(wanted, setting.cap)
        if not setting.allowance(wanted):
            daily = setting.daily
            smallest = setting.smallest
            fix = (f"Arm <b>{smallest}</b> or more, or use 🔕 Disarm for no "
                   "welcome at all." if smallest is not None else
                   f"The cap is <b>{setting.cap}</b>, so no first-day allowance "
                   "can be larger than that — there is no welcome to arm.")
            return (f"🧪 <b>That is not a welcome.</b>\n"
                    f"Every user already gets <b>{daily}</b> free scan"
                    f"{'s' if daily != 1 else ''} a day, and a first-day "
                    f"allowance is only an allowance above that — the quota "
                    f"discards <b>{wanted}</b> and grants nothing.\n{fix}",
                    _lever_buttons(current))
        if not confirmed:
            return (f"🧪 <b>Arm the free-scan lever?</b>\n"
                    f"New users would get <b>{wanted}</b> scan{'s' if wanted != 1 else ''} "
                    f"on their first day. Currently {_welcome_html(setting)}.\n"
                    f"This spends money: every extra scan is a model call.",
                    [[("✅ Yes, arm it", f"lever arm {wanted} yes"),
                      ("Cancel", "experiment")]])
        if await _set_free_scan_lever(wanted) is None:
            return _LEVER_UNREADABLE, _lever_buttons(current)
        return (f"🧪 Lever armed — <b>{_lever_label(wanted)}</b>.",
                [[("🧪 Experiment", "experiment")]])

    if action == "disarm":
        if not confirmed:
            return ("🔕 <b>Disarm the free-scan lever?</b>\n"
                    f"New users would fall back to the daily limit. Currently "
                    f"{_welcome_html(setting)}.\n"
                    "The window in /experiment keeps running; only the allowance stops.",
                    [[("✅ Yes, disarm it", "lever disarm yes"),
                      ("Cancel", "experiment")]])
        if await _set_free_scan_lever(0) is None:
            return _LEVER_UNREADABLE, _lever_buttons(current)
        return ("🔕 Lever disarmed — new users get the daily limit.",
                [[("🧪 Experiment", "experiment")]])

    if action == "default":
        if not confirmed:
            if setting is None:
                then = "FREE_SCANS_FIRST_DAY would decide again"
            else:
                # What handing back would actually grant, since the variable's
                # value alone does not say: 1 reads like a welcome and is none.
                after = setting.allowance(setting.environment)
                then = (f"FREE_SCANS_FIRST_DAY={setting.environment} would decide "
                        "again — " + (f"<b>{_lever_label(after)}</b>" if after
                                      else "<b>no first-day welcome</b>"))
            return ("↩️ <b>Hand the lever back to the environment?</b>\n"
                    f"{then}. Currently {_welcome_html(setting)}.",
                    [[("✅ Yes", "lever default yes"), ("Cancel", "experiment")]])
        if await _set_free_scan_lever(None) is None:
            return _LEVER_UNREADABLE, _lever_buttons(current)
        return ("↩️ Lever cleared — the environment decides again.",
                [[("🧪 Experiment", "experiment")]])

    lines = [f"🎚 <b>Free-scan lever</b>\nNow: {_welcome_html(setting)}",
             f"Override: <b>{_lever_label(current)}</b>"]
    if setting is not None:
        lines.append(f"Environment: <code>FREE_SCANS_FIRST_DAY={setting.environment}</code>"
                     f" · daily limit {setting.daily}")
    return "\n".join(lines), _lever_buttons(current)


# ── The paywall's default plan (#220) ──────────────────────────────────────
#
# Which plan the paywall preselects, sent to 1.5.2+ in the token response as
# `paywall_default_plan`. Unset means yearly, exactly as every build before
# the field behaves, so the lever exists to run the monthly-default arm
# without a release, and to end it the same way. Kept in the levers document
# beside the free-scan lever, with its own change record: `changes` is the
# free-scan lever's history, which /experiment's export reads, and a plan
# move written there would read as a free-scan change.

PaywallPlan = Literal["yearly", "monthly"]
PAYWALL_PLANS: tuple[PaywallPlan, ...] = ("yearly", "monthly")


def _plan_value(doc: dict) -> PaywallPlan | None:
    value = doc.get("paywall_default_plan")
    for plan in PAYWALL_PLANS:
        if value == plan:
            return plan
    return None


async def paywall_default_plan() -> PaywallPlan | None:
    """The plan the operator set, or None for the app's own default (yearly).

    Read on every token mint. Raises nothing: an unreadable levers document is
    None, which is what every user saw before the lever existed."""
    return _plan_value(await _levers())


async def _set_paywall_default_plan(value: PaywallPlan | None) -> dict | None:
    """Set or clear the plan lever, recording the day. None, with nothing
    written, when the document could not be read — as `_set_free_scan_lever`."""
    try:
        doc = await _levers(required=True)
    except Exception as exc:
        log.warning("levers unreadable, not changing the plan: %s", type(exc).__name__)
        return None
    before = _plan_value(doc)
    if value is None:
        doc.pop("paywall_default_plan", None)
    else:
        doc["paywall_default_plan"] = value
    changes = [c for c in (doc.get("plan_changes") or [])
               if isinstance(c, list) and len(c) == 3]
    changes.append([opsstats.day(), before, value])
    doc["plan_changes"] = changes[-LEVER_CHANGES_CAP:]
    await _cache.set(LEVERS_KEY, json.dumps(doc))
    return doc


def _plan_label(value: str | None) -> str:
    return f"{value}" if value else "yearly (app default)"


async def _plan_command(parts: list[str]) -> tuple[str, opsformat.Buttons]:
    """`/lever plan`, `/lever plan yearly|monthly|default [yes]`.

    Two taps, like the free-scan lever. It reaches each device at its next
    token mint, within the hour, and only builds that read the field: 1.5.2
    and later."""
    wanted_raw = parts[1].lower() if len(parts) > 1 else ""
    confirmed = any(token.lower() == "yes" for token in parts[2:])
    doc = await _levers()
    current = _plan_value(doc)
    history = [c for c in (doc.get("plan_changes") or []) if isinstance(c, list) and len(c) == 3]
    buttons: opsformat.Buttons = [[("📅 Yearly", "lever plan yearly"),
                         ("🗓 Monthly", "lever plan monthly"),
                         ("↩️ App default", "lever plan default")]]

    if wanted_raw not in (*PAYWALL_PLANS, "default"):
        lines = ["💳 <b>Paywall default plan</b>",
                 f"Now: <b>{_plan_label(current)}</b>",
                 "Preselected on the paywall by builds 1.5.2 and later, from "
                 "their next token (within an hour). Older builds always "
                 "preselect yearly."]
        if history:
            day, before, after = history[-1]
            lines.append(f"Last change: {day[:4]}-{day[4:6]}-{day[6:]}, "
                         f"{_plan_label(before)} → {_plan_label(after)}")
        return "\n".join(lines), buttons

    # None for "default", the plan itself otherwise (checked just above).
    wanted = _plan_value({"paywall_default_plan": wanted_raw})
    if wanted == current:
        return f"💳 Nothing changed: the default is already <b>{_plan_label(current)}</b>.", buttons
    if not confirmed:
        return (f"💳 <b>Change the paywall's default plan?</b>\n"
                f"<b>{_plan_label(current)}</b> → <b>{_plan_label(wanted)}</b>.\n"
                "This is an experiment arm (#220): change it only at an arm "
                "boundary, and log the date in the growth log.",
                [[("✅ Yes, change it", f"lever plan {wanted_raw} yes"),
                  ("Cancel", "lever plan")]])
    if await _set_paywall_default_plan(wanted) is None:
        return _LEVER_UNREADABLE, buttons
    return (f"💳 Paywall default plan — <b>{_plan_label(wanted)}</b>, "
            "from each 1.5.2+ device's next token.", buttons)


def _lever_buttons(current: int | None) -> opsformat.Buttons:
    row = [("🧪 Arm", "lever arm")]
    if current is not None:
        row.append(("↩️ Use env", "lever default"))
    row.append(("🔕 Disarm", "lever disarm"))
    return [row]


# ── The oldest build still served ───────────────────────────────────────────
#
# A bad client release could not be told to update: the server did not know
# which build was calling, and had no switch to act on it if it had.
# `main._refuse_outdated_build` reads this on /scan, /listing and /trends and
# refuses a build below it with `UPDATE_REQUIRED_DETAIL` and the code
# `update_required` — a 426 when the build said so in `X-SnapWorth-Build`,
# a 422 when it was read from the User-Agent. Only /scan and
# /listing show that text; the app fetches /trends with `try?`, so a refusal
# there shows nothing. /auth is never gated, so an old build can still sign in
# and record a purchase.
#
# Off until set, and fails open: an unreadable value serves everyone, because
# a switch that locks out every user when Redis blinks is worse than none.

MIN_BUILD_KEY = "opsstate:minbuild"
MIN_BUILD_MAX = 100_000

#: What a refused build is told. Here rather than in main.py so the bot's
#: confirmation can quote it word for word.
UPDATE_REQUIRED_DETAIL = (
    "This version of SnapWorth is no longer supported. "
    "Update SnapWorth from the App Store to keep using it.")


async def minimum_build() -> int | None:
    """The oldest build /scan, /listing and /trends still serve, or None.

    Raises nothing: anything unreadable is None, which serves every build.
    """
    cache = _cache
    if cache is None:
        return None
    try:
        raw = await cache.get(MIN_BUILD_KEY)
        value = int(raw) if raw else None
    except Exception:
        return None
    return value if value is not None and 0 < value <= MIN_BUILD_MAX else None


async def _minbuild_command(argument: str, rest: str) -> tuple[str, opsformat.Buttons]:
    """`/minbuild`, `/minbuild <n>`, `/minbuild off`, and their confirmations.

    Two taps, like `/lever`. The confirmation quotes what refused users are
    told, because it sends them to the App Store: set past the build that is
    actually live there, it tells them to install an update that does not
    exist.
    """
    parts = (rest or "").split()
    confirmed = any(token.lower() == "yes" for token in parts[1:])
    current = await minimum_build()
    back = [[("📵 Minimum build", "minbuild")]]
    cache = _cache
    if cache is None:  # the bot is wired by `configure`, which sets it first
        return "📵 No store is configured, so there is no minimum build.", back

    if argument == "off":
        if current is None:
            return "📵 No minimum build is set — every build is served.", back
        if not confirmed:
            return (f"📵 <b>Serve every build again?</b>\n"
                    f"Builds below <b>{current}</b> are refused now.",
                    [[("✅ Yes, serve all", "minbuild off yes"),
                      ("Cancel", "minbuild")]])
        await cache.delete(MIN_BUILD_KEY)
        log.warning("minimum build cleared from chat", extra={"previous": current})
        return "📵 Minimum build cleared — every build is served.", back

    if argument.isdigit():
        wanted = int(argument)
        if not 0 < wanted <= MIN_BUILD_MAX:
            return f"📵 <b>{wanted}</b> is not a build number.", back
        if not confirmed:
            return (f"📵 <b>Refuse builds below {wanted}?</b>\n"
                    f"On /scan and /listing they would be told: "
                    f"<i>{html.escape(UPDATE_REQUIRED_DETAIL)}</i>\n"
                    f"/trends is refused too, but the app drops that error "
                    f"silently and its Trending card just disappears.\n"
                    f"Only do this once build <b>{wanted}</b> is live on the "
                    f"App Store. Builds 7 and older cannot show this text and "
                    f"will see \"Something went wrong\". A build that sends "
                    f"<code>X-SnapWorth-Build</code> is refused with a 426 and "
                    f"shows the app's own update message in the app's "
                    f"language; only the Scan tab's alert adds an App Store "
                    f"button. Sign-in and purchases "
                    f"stay open, and a request that does not say its build is "
                    f"always served. The access log's <code>build</code> field "
                    f"shows who is still on an older one, and "
                    f"<code>snapworth_outdated_build_refused_total</code> "
                    f"counts refusals.\n"
                    f"Currently: <b>{current if current is not None else 'none'}</b>.",
                    [[(f"✅ Yes, require {wanted}", f"minbuild {wanted} yes"),
                      ("Cancel", "minbuild")]])
        await cache.set(MIN_BUILD_KEY, str(wanted))
        log.warning("minimum build set from chat",
                    extra={"minimum": wanted, "previous": current})
        return f"📵 Minimum build set to <b>{wanted}</b>.", back

    if current is None:
        return ("📵 <b>Minimum build</b>: none — every build is served.\n"
                "<code>/minbuild &lt;n&gt;</code> refuses builds below n on "
                "/scan, /listing and /trends. /scan and /listing tell them to "
                "update; on /trends the Trending card just disappears.",
                await _buttons())
    return (f"📵 <b>Minimum build</b>: <b>{current}</b>\n"
            f"Builds below it are told to update on /scan and /listing, and "
            f"lose the Trending card, since the app drops a /trends error "
            f"silently. Sign-in and purchases stay open.",
            [[("↩️ Serve every build", "minbuild off")]] + await _buttons())


async def _feed_enabled() -> bool:
    try:
        raw = await _cache.get(FEED_KEY)
    except Exception:
        return False
    return raw != "0"


async def _set_feed(enabled: bool) -> None:
    await _cache.set(FEED_KEY, "1" if enabled else "0")


async def _feed_command(argument: str) -> str:
    if argument in {"on", "off"}:
        await _set_feed(argument == "on")
    elif argument == "toggle":
        await _set_feed(not await _feed_enabled())
    state = "on" if await _feed_enabled() else "off"
    return (f"🔔 Live scan feed is <b>{state}</b>." if state == "on"
            else "🔕 Live scan feed is <b>off</b>. /feed on to resume.")


def _feed_text(*, item_name: str, category: str, low: float, high: float,
               confidence: str, tier: str) -> str:
    emoji = CATEGORY_EMOJI[trends.normalise_category(category)]
    name = html.escape(" ".join((item_name or "").split())[:80] or "Unidentified item")
    band = html.escape((confidence or "").strip().lower() or "unknown")
    who = "Pro" if tier == "pro" else "free"
    return (f"{emoji} <b>{name}</b>\n"
            f"{trends.normalise_category(category)} · ${low:,.0f}–{high:,.0f} · "
            f"{band} confidence · {who}")


async def _top_text(day: str, limit: int = 3) -> str:
    try:
        doc = json.loads(await _cache.get(opsstats.stat_key(day, "top")) or "{}")
    except Exception:
        return ""
    cats = sorted((doc.get("cats") or {}).items(), key=lambda kv: -kv[1])[:limit]
    brands = sorted((doc.get("brands") or {}).items(), key=lambda kv: -kv[1])[:limit]
    if not cats:
        return ""
    text = "Top: " + " · ".join(f"{html.escape(c)} {n}" for c, n in cats)
    if brands:
        text += " — " + ", ".join(f"{html.escape(b)} ×{n}" for b, n in brands)
    return text


async def _note_scan(*, tier: str, item_name: str, brand: str | None,
                     category: str, low: float, high: float, confidence: str,
                     subject: str | None = None, elapsed_ms: int | None = None,
                     reread: bool = False) -> None:
    try:
        # The count and the tallies feed `/trends` as well as the bot, so they
        # run with or without Telegram. Everything after them is the
        # operator's alone.
        await trends.record_scan(tier=tier, item_name=item_name, brand=brand,
                                 category=category, low=low, high=high,
                                 subject=subject, reread=reread)
        if _notifier is None:
            return
        await _cache.set(LAST_SCAN_KEY, str(int(time.time())), STATS_TTL)
        if elapsed_ms:
            await _cache.incr(opsstats.stat_key(opsstats.day(), "scan_ms"), STATS_TTL,
                              int(elapsed_ms))
        if subject:
            await opsindex.index_user(auditlog.pseudonymise(subject), tier=tier, scanned=True)
        if await _feed_enabled():
            await _notifier.send(_feed_text(
                item_name=item_name, category=category, low=low, high=high,
                confidence=confidence, tier=tier), _FEED_BUTTONS)
    except Exception as exc:
        log.debug("scan feed failed: %s", type(exc).__name__)


def scan_completed(*, tier: str, item_name: str, brand: str | None, category: str,
                   low: float, high: float, confidence: str,
                   subject: str | None = None, elapsed_ms: int | None = None,
                   reread: bool = False) -> None:
    """A scan produced a valuation. Counts it, tallies what it was, and — when
    the feed is on — tells the operator. Fire-and-forget; never the photo.

    What the tallies keep is the item, its price, and `trends._trend_device`'s
    keyed tag for the device, for STATS_TTL, only so `/trends` can count
    distinct devices. The tag joins to nothing else the cache holds without AUDIT_SALT.
    The feed message is item and price alone.

    Gated on the cache alone. The tallies are what `/trends` serves to the
    app, and this was their only writer: gated on Telegram as well, unsetting
    the bot's variables would have emptied "Trending at the thrift" for every
    user over the following week, with no error anywhere — the card hides
    itself when it has nothing to show.

    `reread` is a second look at an item already scanned — the result
    screen's "add a tag photo". It is a model call, so it is counted, but the
    item was tallied the first time and is not tallied again."""
    if _cache is None:
        return
    _spawn(_note_scan(tier=tier, item_name=item_name, brand=brand, category=category,
                      low=low, high=high, confidence=confidence, subject=subject,
                      elapsed_ms=elapsed_ms, reread=reread))


# ── Weekly report ────────────────────────────────────────────────────────────

def _trend(current: int, previous: int) -> str:
    if previous == 0:
        return "new" if current else "—"
    change = (current - previous) * 100 // previous
    if change > 0:
        return f"▲ {change}%"
    if change < 0:
        return f"▼ {-change}%"
    return "＝"


async def _weekly_text(now: datetime) -> str:
    """The seven days ending yesterday, against the seven before."""
    end = (now - timedelta(days=1)).date()
    this_week = [opsstats.day(datetime.combine(end - timedelta(days=i), datetime.min.time(),
                                       tzinfo=timezone.utc)) for i in range(7)]
    last_week = [opsstats.day(datetime.combine(end - timedelta(days=i), datetime.min.time(),
                                       tzinfo=timezone.utc)) for i in range(7, 14)]

    async def pair(name: str) -> tuple[int, int]:
        return await opsstats.sum_stat(this_week, name), await opsstats.sum_stat(last_week, name)

    free_now, free_prev = await pair("scans_free")
    pro_now, pro_prev = await pair("scans_pro")
    failed_now, failed_prev = await pair("scans_failed")
    users_now, users_prev = await pair("active_users")
    (trials_now, conv_now, direct_now), (trials_prev, conv_prev, direct_prev) = (
        await _sub_counts(this_week), await _sub_counts(last_week))
    paid_now, paid_prev = conv_now + direct_now, conv_prev + direct_prev
    scans_now, scans_prev = free_now + pro_now, free_prev + pro_prev
    spend_now = await opsspend.total_spend(this_week)
    spend_prev = await opsspend.total_spend(last_week)

    start = end - timedelta(days=6)
    return "\n".join([
        f"📈 <b>Week {start.strftime('%d %b')} – {end.strftime('%d %b')}</b>",
        f"Scans: {scans_now} ({free_now} free · {pro_now} Pro) {_trend(scans_now, scans_prev)}",
        f"Failed: {failed_now} {_trend(failed_now, failed_prev)}",
        f"Active user-days: {users_now} {_trend(users_now, users_prev)}",
        f"Trial starts: {trials_now} {_trend(trials_now, trials_prev)}",
        f"Paid: {paid_now} ({conv_now} converted trial{'s' if conv_now != 1 else ''} · "
        f"{direct_now} direct) {_trend(paid_now, paid_prev)}",
        f"Gemini spend: {opsformat.usd(spend_now)} {_trend(round(spend_now * 100), round(spend_prev * 100))}",
        f"vs {scans_prev} scans · {users_prev} user-days · {trials_prev} trial starts · "
        f"{paid_prev} paid · "
        f"{opsformat.usd(spend_prev)} the week before",
    ])


async def send_weekly(now: datetime | None = None) -> bool:
    """Send the weekly report once, however many replicas reach Monday."""
    if _notifier is None or _cache is None:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        if not await _cache.add(f"opsstats:weeklysent:{opsstats.day(now)}", "1", STATS_TTL):
            return False
    except Exception as exc:
        log.warning("weekly guard failed, skipping: %s", type(exc).__name__)
        return False
    return await _notifier.send(await _weekly_text(now), await _buttons())


# ── Operator tables: subscriptions and devices ───────────────────────────────

#: `opsindex.acquisition`'s words, shortened to fit a table column.
#:
#: The `/subs` `via` field is eleven wide and two of the five labels are
#: exactly eleven characters — "promo offer" and "intro offer" — so
#: `str.__format__` added no padding and the date ran straight into the label:
#: `promo offer12 Sep`. Every token here is at most five.
_VIA_SHORT = {
    "offer code":  "code",
    "promo offer": "promo",
    "intro offer": "intro",
    "trial":       "trial",
    "paid":        "paid",
}


def _via(acq: str | None) -> str:
    """The table form of an acquisition label."""
    if not acq:
        return "?"
    return _VIA_SHORT.get(acq, acq[:5])


#: The `/subs` auto-renew column, one character wide.
#:
#: Three states, and the third is the reason this is not a boolean: `?` means
#: nobody has told us. Rows predating `signedRenewalInfo` being read are all
#: `?`, and so is any subscription only ever seen via `/auth/entitlement`.
#: Printing those as "off" would invent a cancellation; printing them as "on"
#: would hide a real one.
#:
#: One character because the header line is already 42 columns and Telegram
#: wraps a <pre> block on a phone at around 46. `↻` and `✕` are single
#: code points and monospace in Telegram's block font.
_AUTO_RENEW_MARKS = {True: "↻", False: "✕", None: "?"}


def _renew_mark(entry: dict) -> str:
    """The auto-renew cell for one row."""
    return _AUTO_RENEW_MARKS[entry.get("auto_renew") if isinstance(
        entry.get("auto_renew"), bool) else None]


async def _subscribers_line() -> str:
    active, paid, comped, _, _ = opsindex.subs_summary(await opsindex.read_index(opsindex.SUBS_INDEX_KEY))
    return f"Subscribers: {active} active · {paid} paid · {comped} comped/trial"


async def _subs_text() -> str:
    doc = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
    active, paid, comped, expired, mrr = opsindex.subs_summary(doc)
    lines = [f"💳 <b>Subscriptions</b> — {active} active · {paid} paid · "
             f"{comped} comped/trial · {expired} expired"]
    if mrr:
        lines.append("MRR ≈ " + " + ".join(opsformat.money(v, c) for c, v in sorted(mrr.items()))
                     + " (paid plans, from transaction prices)")
    else:
        lines.append("MRR ≈ n/a (no priced paid plan seen yet)")
    if not doc:
        lines.append("No subscription has synced since the bot started watching.")
        return "\n".join(lines)

    now = time.time()
    def _alive(e: dict) -> bool:
        return opsformat.sub_is_alive(e, now)

    rows = sorted(doc.values(), key=lambda e: (not _alive(e), float(e.get("expires") or 0)))
    # Literal spaces between every column, not field widths alone — the same
    # reason `_experiment_text` documents for its own table: a value exactly as
    # wide as its field gets no padding and runs into its neighbour.
    header = (f"{'plan':<8} {'via':<5} {'since':<7} "
              f"{'renews':<7} {'↻':<2} {'seen':<7} {'id':<6}")
    body = [header]
    for e in rows[:TABLE_ROWS]:
        renews = opsformat.short_date(int(e["expires"])) if e.get("expires") else "never"
        if e.get("revoked") is not None:
            renews = "refund"
        elif not _alive(e):
            renews = "ended"
        body.append(
            f"{opsformat.plan(e.get('product')):<8} {_via(e.get('acq')):<5} "
            f"{(opsformat.short_date(int(e['first'])) if e.get('first') else '?'):<7} "
            f"{renews:<7} {_renew_mark(e):<2} "
            f"{(opsformat.short_date(int(e['seen'])) if e.get('seen') else '?'):<7} "
            f"{str(e.get('who') or '')[:6]:<6}")
    if len(rows) > TABLE_ROWS:
        body.append(f"… and {len(rows) - TABLE_ROWS} more")
    lines.append("<pre>" + html.escape("\n".join(body)) + "</pre>")
    due = [e for e in doc.values()
           if e.get("revoked") is None
           and e.get("expires") and now < float(e["expires"]) < now + 7 * 86400]
    if due:
        paid_due = [e for e in due if e.get("acq") == "paid"]
        value: dict[str, float] = {}
        for e in paid_due:
            if isinstance(e.get("price"), (int, float)):
                value[e.get("currency") or "?"] = value.get(e.get("currency") or "?", 0.0) + e["price"]
        worth = (" · " + " + ".join(opsformat.money(v, c) for c, v in sorted(value.items()))
                 if value else "")
        lines.append(f"Due in 7 days: {len(due)} renew or end ({len(paid_due)} paid{worth})")
    lines.append("↻ renews · ✕ auto-renew off · ? not reported yet")
    lines.append("Apple reports renewals, expiries and refunds directly, so this no "
                 "longer waits for an app launch to notice.")
    return "\n".join(lines)


async def _users_text() -> str:
    doc = await opsindex.read_index(opsindex.USERS_INDEX_KEY)
    now = time.time()
    week = sum(1 for e in doc.values() if now - float(e.get("last", 0)) < 7 * 86400)
    month = sum(1 for e in doc.values() if now - float(e.get("last", 0)) < 30 * 86400)
    today = sum(1 for e in doc.values() if opsstats.day(datetime.fromtimestamp(
        float(e.get("last", 0)), timezone.utc)) == opsstats.day())
    pro = sum(1 for e in doc.values() if e.get("tier") == "pro")
    lines = [f"👥 <b>Devices</b> — {len(doc)} seen · {month} last 30d · {week} last 7d · "
             f"{today} today · {pro} Pro"]
    if not doc:
        lines.append("No device has been seen since the bot started watching.")
        return "\n".join(lines)
    rows = sorted(doc.items(), key=lambda kv: (-int(kv[1].get("scans", 0)),
                                               -float(kv[1].get("last", 0))))
    body = [f"{'id':<7} {'tier':<5} {'scans':<6} {'first':<7} {'last':<7}"]
    for who, e in rows[:TABLE_ROWS]:
        body.append(
            f"{who[:6]:<7} {('Pro' if e.get('tier') == 'pro' else 'free'):<5} "
            f"{int(e.get('scans', 0)):<6} "
            f"{(opsformat.short_date(int(e['first'])) if e.get('first') else '?'):<7} "
            f"{(opsformat.short_date(int(e['last'])) if e.get('last') else '?'):<7}")
    if len(rows) > TABLE_ROWS:
        body.append(f"… and {len(rows) - TABLE_ROWS} more")
    lines.append("<pre>" + html.escape("\n".join(body)) + "</pre>")
    lines.append("Devices, not people — there are no accounts. Ids are the audit log's pseudonyms.")
    return "\n".join(lines)


# ── Gemini spend: opsspend's alert, bound to this module's notifier ────────

async def _announce_over_budget(spend: float, budget: float) -> None:
    """opsspend's over-budget alert, sent once a day while the bot is
    configured."""
    if _notifier is None:
        return
    await _notifier.send(
        "💸 <b>Gemini spend over budget</b>\n"
        f"Today ≈ {opsformat.usd(spend)} against a {opsformat.usd(budget)} daily budget. "
        "Scans keep working; this is a heads-up, not a cut-off.",
        _COSTS_BUTTONS)


# ── Social reach ─────────────────────────────────────────────────────────────

def _social_snapshot_key(day: str) -> str:
    return opsstats.stat_key(day, "social")


async def _remember_followers(accounts) -> None:
    """Today's follower counts, so tomorrow's digest can show the delta."""
    snapshot = {a.platform: a.followers for a in accounts if a.ok and a.followers is not None}
    if snapshot:
        try:
            await _cache.set(_social_snapshot_key(opsstats.day()), json.dumps(snapshot), STATS_TTL)
        except Exception as exc:
            log.debug("social snapshot failed: %s", type(exc).__name__)


async def _followers_delta(platform: str, now_count: int) -> str:
    try:
        yesterday = opsstats.day(datetime.now(timezone.utc) - timedelta(days=1))
        previous = json.loads(await _cache.get(_social_snapshot_key(yesterday)) or "{}")
    except Exception:
        return ""
    before = previous.get(platform)
    if not isinstance(before, int):
        return ""
    diff = now_count - before
    return f" (▲ {diff})" if diff > 0 else f" (▼ {-diff})" if diff < 0 else " (＝)"


def _post_line(post) -> str:
    title = " ".join((post.title or "").split())[:60]
    bits = []
    if post.views is not None:
        bits.append(f"{opsformat.kilo(post.views)} views")
    if post.likes is not None:
        bits.append(f"{opsformat.kilo(post.likes)} likes")
    if post.comments is not None:
        bits.append(f"{post.comments} comments")
    if post.shares:
        bits.append(f"{post.shares} shares")
    when = f" · {opsformat.date(post.created_at)[:6]}" if post.created_at else ""
    label = html.escape(title or "post")
    if post.url:
        label = f'<a href="{html.escape(post.url)}">{label}</a>'
    return f" • {label} — {' · '.join(bits) or 'no stats'}{when}"


def _account_lines(account) -> list[str]:
    name = {"tiktok": "TikTok"}.get(account.platform, account.platform)
    if not account.ok:
        note = account.note or "unavailable"
        if note.startswith("not linked — ") or note.startswith("link expired — "):
            prefix, _, url = note.partition(" — ")
            return [f"<b>{name}</b>: {html.escape(prefix)} — "
                    f'<a href="{html.escape(url)}">tap to link your account</a>']
        return [f"<b>{name}</b>: {html.escape(note)}"]
    head = f"<b>{name}</b>"
    if account.handle:
        head += f" @{html.escape(str(account.handle))}"
    facts = []
    if account.followers is not None:
        facts.append(f"{account.followers:,} followers")
    if account.posts is not None:
        facts.append(f"{account.posts} {'videos' if account.platform == 'tiktok' else 'posts'}")
    if account.total_likes is not None:
        facts.append(f"{opsformat.kilo(account.total_likes)} likes")
    lines = [head + (" — " + " · ".join(facts) if facts else "")]
    lines += [_post_line(p) for p in account.recent[:RECENT_SOCIAL_POSTS]]
    return lines


RECENT_SOCIAL_POSTS = 3


async def _social_text() -> str:
    if _social is None:
        return ("📣 <b>Social</b>\nNot configured. Set TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET "
                "for TikTok — see .env.example.")
    accounts = await _social.accounts()
    await _remember_followers(accounts)
    lines = ["📣 <b>Social</b>"]
    for account in accounts:
        block = _account_lines(account)
        if account.ok and account.followers is not None:
            block[0] += await _followers_delta(account.platform, account.followers)
        lines += block
    return "\n".join(lines)


async def _social_line() -> str:
    """One digest line: followers per platform with the day's change."""
    if _social is None:
        return ""
    try:
        accounts = await _social.accounts()
    except Exception as exc:
        log.debug("social digest fetch failed: %s", type(exc).__name__)
        return ""
    parts = []
    for a in accounts:
        if a.ok and a.followers is not None:
            name = {"tiktok": "TikTok"}.get(a.platform, a.platform)
            parts.append(f"{name} {a.followers:,}{await _followers_delta(a.platform, a.followers)}")
    await _remember_followers(accounts)
    return "Social: " + " · ".join(parts) if parts else ""


# ── Best finds and post ideas ────────────────────────────────────────────────

async def _week_top(now: datetime | None = None) -> dict:
    """The last seven days' tallies folded together.

    What the day documents hold, summed: categories and brands with counts,
    the best finds re-ranked across days, and the scan total. Grounding for
    /post and the whole of /finds.
    """
    days = opsstats.days_ending_today(7, now)
    cats: dict[str, int] = {}
    brands: dict[str, int] = {}
    finds: list[dict] = []
    scans = 0
    for day in days:
        try:
            doc = json.loads(await _cache.get(opsstats.stat_key(day, "top")) or "{}")
        except Exception:
            doc = {}
        for c, n in (doc.get("cats") or {}).items():
            cats[c] = cats.get(c, 0) + int(n)
        for b, n in (doc.get("brands") or {}).items():
            brands[b] = brands.get(b, 0) + int(n)
        for f in doc.get("finds") or []:
            if isinstance(f, dict):
                finds.append({**f, "day": day})
        scans += (await opsstats.read_stat(day, "scans_free")
                  + await opsstats.read_stat(day, "scans_pro"))
    finds.sort(key=lambda f: -float(f.get("hi") or 0))
    return {
        "days": len(days), "scans": scans,
        "cats": sorted(cats.items(), key=lambda kv: -kv[1])[:5],
        "brands": sorted(brands.items(), key=lambda kv: -kv[1])[:8],
        "finds": finds[:trends.TOP_FINDS_CAP],
    }


def _find_line(rank: int, f: dict) -> str:
    emoji = CATEGORY_EMOJI.get(str(f.get("c") or ""), "📦")
    name = html.escape(str(f.get("n") or "Unidentified item"))
    when = ""
    day = str(f.get("day") or "")
    if len(day) == 8:
        try:
            when = " · " + datetime.strptime(day, "%Y%m%d").strftime("%d %b")
        except ValueError:
            when = ""
    who = "Pro" if f.get("t") == "pro" else "free"
    return (f"{rank}. {emoji} <b>{name}</b> — ${int(f.get('lo') or 0):,}–{int(f.get('hi') or 0):,}"
            f" · {who}{when}")


async def _finds_text() -> str:
    top = await _week_top()
    lines = ["🏆 <b>Best finds — last 7 days</b>"]
    if not top["finds"]:
        lines.append("No scans recorded this week yet.")
        return "\n".join(lines)
    lines += [_find_line(i, f) for i, f in enumerate(top["finds"], 1)]
    if top["cats"]:
        text = "Top: " + " · ".join(f"{html.escape(c)} {n}" for c, n in top["cats"][:3])
        if top["brands"]:
            text += " — " + ", ".join(f"{html.escape(b)} ×{n}" for b, n in top["brands"][:3])
        lines.append(text)
    lines.append(f"{top['scans']} scans this week. Item and AI estimate only — never who.")
    return "\n".join(lines)


async def _post_text(hint: str = "") -> str:
    if _generator is None:
        return ("📝 <b>Post ideas</b>\nThe model is not wired up for the bot in this "
                "process, so there is nothing to ask. This is a build problem, not a data one.")
    context = await _week_top()
    prompt = ideas.build_prompt(context, hint)
    try:
        text = await _generator(prompt, ideas.MAX_OUTPUT_TOKENS)
    except Exception as exc:
        log.warning("post ideas generation failed: %s", type(exc).__name__)
        return ("📝 <b>Post ideas</b>\nThe model did not answer "
                f"({html.escape(type(exc).__name__)}). Try again in a minute.")
    parsed = ideas.parse(text)
    if not parsed:
        log.warning("post ideas reply unreadable: %.120s", text)
        return "📝 <b>Post ideas</b>\nThe model's reply could not be read. Try again."
    return ideas.render(parsed, context, hint)


# ── The other briefs: caption, hooks, replies, price, a week's calendar ──────

_BRIEFS = {
    # kind: (needs argument, usage, prompt builder, renderer, max tokens)
    "caption": (True, "/caption &lt;what you filmed&gt; — e.g. /caption me scanning a $4 Patagonia fleece",
                ideas.build_caption_prompt, ideas.render_caption, ideas.CAPTION_MAX_TOKENS),
    "hooks": (True, "/hooks &lt;topic&gt; — e.g. /hooks vintage Levi's",
              ideas.build_hooks_prompt, ideas.render_hooks, ideas.HOOKS_MAX_TOKENS),
    "reply": (True, "/reply &lt;paste the comment or review&gt;",
              ideas.build_reply_prompt, ideas.render_replies, ideas.REPLY_MAX_TOKENS),
    "price": (True, "/price &lt;item&gt; — e.g. /price Carhartt Detroit jacket, brown duck, size L, worn",
              ideas.build_price_prompt, ideas.render_price, ideas.PRICE_MAX_TOKENS),
}


async def _ask_model(prompt: str, max_tokens: int) -> dict | str:
    """The model's JSON for a brief, or an HTML error line for the operator."""
    if _generator is None:
        return ("The model is not wired up for the bot in this process. "
                "This is a build problem, not a data one.")
    try:
        text = await _generator(prompt, max_tokens)
    except Exception as exc:
        log.warning("brief generation failed: %s", type(exc).__name__)
        return f"The model did not answer ({html.escape(type(exc).__name__)}). Try again in a minute."
    data = ideas.parse_json(text)
    if data is None:
        log.warning("brief reply unreadable: %.120s", text)
        return "The model's reply could not be read. Try again."
    return data


async def _brief_text(kind: str, argument: str) -> str:
    needs_arg, usage, build, render, max_tokens = _BRIEFS[kind]
    argument = " ".join((argument or "").split())
    if needs_arg and not argument:
        return f"Usage: {usage}"
    result = await _ask_model(build(argument), max_tokens)
    if isinstance(result, str):
        return f"<b>/{kind}</b>\n{result}"
    return render(result, argument)


async def _calendar_text() -> str:
    context = await _week_top()
    result = await _ask_model(ideas.build_calendar_prompt(context), ideas.CALENDAR_MAX_TOKENS)
    if isinstance(result, str):
        return f"🗓 <b>This week's posts</b>\n{result}"
    return ideas.render_calendar(result, context)


# ── Trend: one brand or category over thirty days ────────────────────────────

def _spark(values: list[int]) -> str:
    peak = max(values) if values else 0
    if peak <= 0:
        return SPARK[0] * len(values)
    return "".join(SPARK[min(len(SPARK) - 1, round(v / peak * (len(SPARK) - 1)))] for v in values)


async def _trend_text(term: str) -> str:
    term = " ".join((term or "").split()).lower()
    if not term:
        return ("Usage: /trend &lt;brand or category&gt; — e.g. /trend carhartt, /trend shoes. "
                "Categories: " + ", ".join(sorted(CATEGORY_EMOJI)))
    now = datetime.now(timezone.utc)
    days = list(reversed(opsstats.days_ending_today(TREND_DAYS, now)))       # oldest first
    counts: list[int] = []
    estimates: list[float] = []
    label = term
    for day in days:
        try:
            doc = json.loads(await _cache.get(opsstats.stat_key(day, "top")) or "{}")
        except Exception:
            doc = {}
        n = 0
        for c, k in (doc.get("cats") or {}).items():
            if str(c).lower() == term:
                n += int(k)
        for b, k in (doc.get("brands") or {}).items():
            if term in str(b).lower():
                n += int(k)
                label = str(b)
        counts.append(n)
        for f in doc.get("finds") or []:
            hay = f"{f.get('n', '')} {f.get('b', '')} {f.get('c', '')}".lower()
            if term in hay and f.get("hi"):
                estimates.append((float(f.get("lo") or 0) + float(f["hi"])) / 2)
    total = sum(counts)
    if total == 0:
        return (f"📉 <b>{html.escape(label)}</b>\nNo scans matched in the last {TREND_DAYS} days. "
                "Brands match on a substring; categories exactly.")
    this_week, last_week = sum(counts[-7:]), sum(counts[-14:-7])
    lines = [f"📈 <b>{html.escape(label)}</b> — {total} scans in {TREND_DAYS} days",
             f"<code>{_spark(counts)}</code>",
             f"<code>{days[0][4:6]}/{days[0][6:]}{' ' * (TREND_DAYS - 10)}{days[-1][4:6]}/{days[-1][6:]}</code>",
             f"This week {this_week} vs {last_week} the week before {_trend(this_week, last_week)}"]
    if estimates:
        lines.append(f"Average estimate among the day's best finds: ${sum(estimates) / len(estimates):,.0f} "
                     f"({len(estimates)} items)")
    return "\n".join(lines)


# ── The free-scan experiment ─────────────────────────────────────────────────

def _parse_day(day: str) -> datetime | None:
    try:
        return datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _day_span(start: datetime, end: datetime) -> list[str]:
    """Every day from start to end inclusive, oldest first."""
    out, cur = [], start
    while cur <= end:
        out.append(opsstats.day(cur))
        cur += timedelta(days=1)
    return out


# The partial day's footnote, shared by the table and the export so the two
# cannot disagree about what that row is.
EXPERIMENT_PARTIAL_NOTE = ("limit hits counted from 18:29 UTC that day only — the "
                           "counter shipped mid-day")

# The counters `/experiment` shows, in its column order. The export's header
# uses these names as they are, so a kept copy can be traced back to the code.
#
# `new_subs` stays beside the three that split it (#218): before the split it
# is the only record of a day's subscriptions, and a kept copy without it
# would read those days as none.
EXPERIMENT_COUNTERS = ("active_users", "scans_free", "limit_hits",
                       "trial_starts", "trial_conversions", "paid_direct", "new_subs")


def _stat_expired(day: str, now: datetime) -> bool:
    """Whether a day's counters are past STATS_TTL, so a 0 read for it is an
    absence rather than a count."""
    dt = _parse_day(day)
    return dt is not None and (now - dt).days >= STATS_TTL // 86400


def _stat_expires_on(day: str) -> datetime:
    """When `_stat_expired` starts to hold for `day`, a YYYYMMDD from
    `_day_span`."""
    return (datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)
            + timedelta(days=STATS_TTL // 86400))


def _lever_changes_in(days: list[str], doc: dict) -> list[list]:
    """The changes recorded in levers document `doc` that fall on one of
    `days`, oldest first.

    Handed the document rather than reading it, because the two callers read
    it differently: the table best-effort, the export `required`.
    """
    return [c for c in (doc.get("changes") or [])
            if isinstance(c, list) and len(c) == 3 and c[0] in days]


async def _experiment_text(now: datetime | None = None) -> str:
    """The experiment's server-side half, whole window at once.

    The digest reports one day at a time, which answers "what happened
    yesterday" and not "is this working" — for that the operator would have to
    scroll back through a fortnight of messages and add them up by hand. This is
    the running total, and since issue #125 was closed it is the only automated
    read of the experiment: the client's half is a dashboard someone has to
    remember to open.

    Deliberately not a conversion claim. Trial starts and purchases beside
    `limit_hits` are a coincidence within a window, not an attribution — nothing here knows whether
    the person who subscribed is the one who hit the limit. TelemetryDeck holds
    the per-user path. These are the totals, and the value of two instruments is
    that they can disagree.
    """
    now = now or datetime.now(timezone.utc)
    start, end = _parse_day(EXPERIMENT_START_DAY), _parse_day(EXPERIMENT_END_DAY)
    if start is None or end is None or end < start:
        return ("\U0001F9EA <b>Free-scan experiment</b>\n"
                "Window misconfigured — EXPERIMENT_START_DAY and "
                "EXPERIMENT_END_DAY must both be YYYYMMDD, end on or after start.")

    today = opsstats.day(now)
    if today < EXPERIMENT_START_DAY:
        off = (start.date() - now.date()).days
        return (f"\U0001F9EA <b>Free-scan experiment</b>\nWindow opens "
                f"{start:%d %b} — {off} day{'s' if off != 1 else ''} from now. "
                "Nothing counted yet.")

    span = _day_span(start, end)
    shown = [d for d in span if d <= today]
    # A day's counters carry STATS_TTL from their last write, so a window read
    # long after it closed reports zeros that are really absences. Say which.
    ttl_days = STATS_TTL // 86400

    # Literal spaces between the columns, not just field widths: a number wider
    # than its column would otherwise run into its neighbour and the row would
    # be unreadable without anything reporting a problem.
    rows = [f"<code>{'day':<6}{'act':>6} {'free':>5} {'hit':>5} "
            f"{'trial':>5} {'paid':>5}</code>"]
    hits = trials = paid = started = unsplit = free_scans = expired = 0
    partial = False
    for d in shown:
        label = f"{d[4:6]}-{d[6:]}"
        if _stat_expired(d, now):
            expired += 1
            rows.append(
                f"<code>{label:<6}{'—':>6} {'—':>5} {'—':>5} {'—':>5} {'—':>5}</code>")
            continue
        act = await opsstats.read_stat(d, "active_users")
        fr = await opsstats.read_stat(d, "scans_free")
        hi = await opsstats.read_stat(d, "limit_hits")
        tr, cv, dr = await _sub_counts([d])
        # A day from before #218 has `new_subs` and nothing splitting it, so
        # its subscriptions are in neither column. `new_subs` is every first
        # sighting since, so any excess over its parts is that day's.
        rest = max(0, await opsstats.read_stat(d, "new_subs") - tr - dr
                   - await opsstats.read_stat(d, OFFER_STARTS))
        hits += hi
        trials += tr
        paid += cv + dr
        started += tr + dr + rest
        unsplit += rest
        free_scans += fr
        mark = ""
        if d == EXPERIMENT_PARTIAL_DAY:
            partial, mark = True, " *"
        if rest:
            mark += " †"
        rows.append(
            f"<code>{label:<6}{act:>6} {fr:>5} {hi:>5} {tr:>5} {cv + dr:>5}</code>{mark}")

    closed = today > EXPERIMENT_END_DAY
    left = max(0, (end.date() - now.date()).days)
    head = ("\U0001F9EA <b>Free-scan experiment</b> — "
            + (f"closed after {len(span)} days" if closed
               else f"day {len(shown)} of {len(span)}"))
    # What the quota grants a new user now, asked of the quota. This read the
    # environment only, so arming from chat left it saying "lever not armed"
    # for the whole window; then it read both and printed them raw, so
    # FREE_SCANS_FIRST_DAY=1 — no welcome at a daily limit of 1 — read as armed.
    lever = _welcome_html(await _welcome_setting())
    window = (f"{start:%d %b} → {end:%d %b}"
              + ("" if closed else f" · {left} day{'s' if left != 1 else ''} left")
              + f" · {lever}")

    # Every total is over the days that could actually be read. Saying "no limit
    # hits" about a day whose counters have expired would be a claim the data
    # cannot support — and the footnote contradicting the headline is worse than
    # either alone.
    readable = len(shown) - expired
    scope = (f" across {readable} readable day{'s' if readable != 1 else ''}"
             if expired else "")
    if hits:
        total = (f"<b>{hits} limit hit{'s' if hits != 1 else ''} · {trials} "
                 f"trial start{'s' if trials != 1 else ''} · {paid} paid "
                 f"({100.0 * started / hits:.0f}%)</b>{scope}")
    elif not readable:
        total = ("<b>Nothing readable</b> — every day in the window is past the "
                 f"{ttl_days}-day counter TTL.")
    elif free_scans and not partial:
        total = (f"<b>No limit hits</b>{scope} — {free_scans} free scan"
                 f"{'s' if free_scans != 1 else ''}, none of which spent the "
                 "day's allowance.")
    elif free_scans:
        # "None spent the allowance" is an inference from hits == 0, and it only
        # holds if hits were counted over the same hours as the scans. On the
        # partial day they were not: the scans are a whole day and the hits are
        # the tail of one, so an allowance spent that morning would print as
        # nobody spending one. Report the count and let the mark carry the rest.
        total = (f"<b>No limit hits recorded</b>{scope} — {free_scans} free scan"
                 f"{'s' if free_scans != 1 else ''} in the window.")
    else:
        total = f"<b>No limit hits</b>{scope} — and no free scans recorded yet."

    notes = []
    # A window whose lever moved mid-flight and does not say so is worse than
    # no window: the numbers look continuous and are not.
    for day_changed, before, after in _lever_changes_in(shown, await _levers())[-4:]:
        notes.append(f"⚠️ lever changed on {day_changed[4:6]}-{day_changed[6:]}: "
                     f"{_lever_label(before)} → {_lever_label(after)}")
    if partial:
        notes.append(f"* {EXPERIMENT_PARTIAL_NOTE}. Every other column is a whole day.")
    if unsplit:
        notes.append(f"† {unsplit} trial start{'s' if unsplit != 1 else ''} or "
                     "purchase" + ("s" if unsplit != 1 else "") + " from before "
                     "the server split them (#218): in neither column, and in the %.")
    if hits:
        notes.append("% is trial starts and direct purchases ÷ limit hits "
                     "across the window — coincidence, not attribution.")
    if expired:
        notes.append(f"— {expired} day{'s' if expired != 1 else ''} older than the "
                     f"{ttl_days}-day counter TTL: those figures are gone, not zero.")
    if readable:
        # The record deletes itself a day at a time; say when, while there is
        # still something to keep.
        oldest = next(d for d in shown if not _stat_expired(d, now))
        gone = _stat_expires_on(oldest)
        notes.append(f"💾 The {oldest[4:6]}-{oldest[6:]} counters expire on "
                     f"{gone:%d %b} — /experiment export gives a copy to keep.")

    return "\n".join([head, window, *rows, total, *notes])


def _csv_comment(text: str) -> str:
    """A `#` line for the export that a CSV parser reads as one field.

    CSV has no comment syntax, so a comma in a note split it into cells — the
    welcome line has several — and the block saved as a .csv read as ragged
    rows ahead of its real header. The notes stay inside the block, because
    the partial day and any lever move have to travel with the rows, and lose
    their commas instead: to " · ", and semicolons too, which a spreadsheet
    in a comma-decimal locale splits on. Not quoted: a quoted line starts with
    `"`, and a reader told to skip `#` lines would no longer skip it.
    """
    return "# " + re.sub(r"\s*[,;]\s*", " · ", text)


async def _experiment_export(now: datetime | None = None) -> str:
    """`/experiment export`: the window's table as CSV, to keep.

    The counters behind `/experiment` carry STATS_TTL, so the server's record
    of the window deletes itself a day at a time — for the default window,
    20260910's row goes on 2026-10-15 and the rest over the fortnight after —
    and the daily digests that reported it are one day each. This is the one
    form of it that outlives the cache: a block to copy into `docs/`.

    Read `required`, unlike the table: the counters and the lever's record
    both. A zero in a kept copy is a claim that nothing happened, and so is a
    copy with no lever move in it, so an unreadable cache refuses the export
    rather than writing either. An expired day has empty cells, not zeros, for
    the reason the table prints "—".
    """
    now = now or datetime.now(timezone.utc)
    start, end = _parse_day(EXPERIMENT_START_DAY), _parse_day(EXPERIMENT_END_DAY)
    if start is None or end is None or end < start:
        return ("💾 Nothing exported — the window is misconfigured: "
                "EXPERIMENT_START_DAY and EXPERIMENT_END_DAY must both be "
                "YYYYMMDD, end on or after start.")
    today = opsstats.day(now)
    shown = [d for d in _day_span(start, end) if d <= today]
    if not shown:
        return f"💾 Nothing to export — the window opens {start:%d %b}."

    ttl_days = STATS_TTL // 86400
    rows = [",".join(["day", *EXPERIMENT_COUNTERS, "note"])]
    try:
        # The lever's record as well as the counters. `_levers()` on its own
        # turns a failed read into {}, and a kept copy built from that shows no
        # lever move: the window that `_set_free_scan_lever` records changes
        # so as never to produce.
        levers = await _levers(required=True)
        for d in shown:
            iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            if _stat_expired(d, now):
                rows.append(iso + "," * len(EXPERIMENT_COUNTERS)
                            + f",expired: past the {ttl_days}-day counter TTL")
                continue
            values = []
            for name in EXPERIMENT_COUNTERS:
                raw = await _cache.get(opsstats.stat_key(d, name), required=True)
                values.append(str(int(raw or 0)))
            note = EXPERIMENT_PARTIAL_NOTE if d == EXPERIMENT_PARTIAL_DAY else ""
            rows.append(",".join([iso, *values, note]))
    except Exception as exc:
        return ("💾 <b>Nothing exported</b> — the counters or the lever's record "
                f"could not be read ({html.escape(type(exc).__name__)}), and a "
                "copy without them would say nothing happened. Try again in a "
                "minute.")

    setting = await _welcome_setting()
    if setting is not None:
        # The quota reads the lever best-effort, as a scan must, and an
        # unreadable one reads as the environment's value. In a kept copy that
        # would be the environment's welcome while the lever said otherwise.
        # So the override is the one just read `required`, through the parse
        # `free_scan_lever` hands the quota; what it grants is still the
        # quota's `allowance`.
        setting = dataclasses.replace(setting, override=_lever_value(levers))
    _, head, why = _welcome_summary(setting)
    lines = [_csv_comment(f"SnapWorth free-scan experiment · {start:%Y-%m-%d} to "
                          f"{end:%Y-%m-%d} · exported {now:%Y-%m-%d %H:%M} UTC"
                          + ("" if today > EXPERIMENT_END_DAY
                             else " while the window was open")),
             _csv_comment(f"welcome at export: {head} — {why}")]
    for day_changed, before, after in _lever_changes_in(shown, levers):
        lines.append(_csv_comment(
            f"lever changed {day_changed[:4]}-{day_changed[4:6]}-{day_changed[6:]}: "
            f"{_lever_label(before)} -> {_lever_label(after)}"))
    lines.extend(rows)

    kept = [d for d in shown if not _stat_expired(d, now)]
    expiry = (f"The {kept[0][4:6]}-{kept[0][6:]} counters expire on "
              f"{_stat_expires_on(kept[0]):%d %b}, the rest a day at a time after."
              if kept else "Every day in it is already past the counter TTL.")
    csv = html.escape("\n".join(lines))
    return ("💾 <b>Free-scan experiment — export</b>\n"
            f"Copy the block into <code>docs/</code> to keep it. {expiry}\n"
            f"<pre>{csv}</pre>")


# ── Paywall readout ──────────────────────────────────────────────────────────

async def _paywall_text(now: datetime | None = None) -> str:
    """`/paywall`: the server's half of reading which paywall sells (#218).

    Trial starts and direct purchases per trigger over PAYWALL_WINDOW_DAYS,
    and the trial-to-paid rate for the trials whose free period ended in that
    window. Per trigger is counts only: the trigger is never on the row, so
    trial-to-paid *by trigger* cannot be read here (#218, Notes).

    Counts, not significance: at a few scans a day every figure here is small,
    which is why each rate carries its n. TelemetryDeck's
    `paywall_viewed → purchase_started → purchase_completed` is the other
    instrument, and it has the views this cannot see.
    """
    now = now or datetime.now(timezone.utc)
    days = [opsstats.day(now - timedelta(days=i)) for i in range(PAYWALL_WINDOW_DAYS)]
    trials, conversions, direct = await _sub_counts(days)

    per: list[tuple[str, int, int]] = []
    for trigger in PAYWALL_TRIGGERS:
        t = await opsstats.sum_stat(days, f"{START_COUNTERS['trial']}:{trigger}")
        p = await opsstats.sum_stat(days, f"{START_COUNTERS['paid']}:{trigger}")
        if t or p:
            per.append((trigger, t, p))
    per.sort(key=lambda row: (-(row[1] + row[2]), row[0]))

    lines = [f"💳 <b>Paywall — last {PAYWALL_WINDOW_DAYS} days</b>",
             f"Trial starts: {trials} · direct purchases: {direct} · "
             f"converted trials: {conversions}"]
    if per:
        lines.append(f"<code>{'trigger':<17}{'trial':>6}{'direct':>7}</code>")
        lines += [f"<code>{html.escape(t):<17}{ts:>6}{pd:>7}</code>"
                  for t, ts, pd in per]
    untagged_t = max(0, trials - sum(r[1] for r in per))
    untagged_p = max(0, direct - sum(r[2] for r in per))
    if untagged_t or untagged_p:
        lines.append(f"<code>{'no trigger':<17}{untagged_t:>6}{untagged_p:>7}</code>")
        lines.append("No trigger: a build before 1.5.2, a sync that was not the "
                     "one after the paywall, or Apple reported it first.")
    elif not per:
        lines.append("No trial starts or direct purchases in the window.")

    # The rate is read off the subscription index rather than the counters:
    # a trial that ends in the window may have started before it, and only
    # its row says when its free period ran out and whether it paid.
    start = (now - timedelta(days=PAYWALL_WINDOW_DAYS)).timestamp()
    ended = converted = 0
    for row in (await opsindex.read_index(opsindex.SUBS_INDEX_KEY)).values():
        if not isinstance(row, dict) or row.get("started_as") != "trial":
            continue
        ends = row.get("trial_ends")
        if not isinstance(ends, (int, float)) or not start <= ends < now.timestamp():
            continue
        ended += 1
        if row.get("acq") == "paid":
            converted += 1
    if ended:
        lines.append(f"Trial → paid: {converted} of {ended} trials that ended "
                     f"({100.0 * converted / ended:.0f}%, n={ended})")
        lines.append("A trial that ended in the last day or two may still be in "
                     "Apple's billing retry.")
    else:
        lines.append("Trial → paid: no trial ended in the window (n=0)")
    return "\n".join(lines)


# ── Checkup: what checkup.py reads from here ───────────────────────────────

def _checkup_wiring() -> checkup.Wiring:
    # Commands reach this only after configure has set the cache.
    assert _cache is not None
    # A lambda for getChat, not the bound method: _notifier is read when the
    # archive line asks, as it was before the split.
    return checkup.Wiring(
        cache=_cache, generator=_generator, status_provider=_status_provider,
        device_check_probe=_device_check_probe,
        get_chat=lambda chat_id: _notifier.get_chat(chat_id),
        budget_line=opsspend.budget_line, referral_line=_referral_line,
        replica_label=_replica_label, read_int=_read_int, poll_token=_poll_token,
        poll_lock_key=POLL_LOCK_KEY, last_scan_key=LAST_SCAN_KEY,
        last_appstore_notification_key=LAST_APPSTORE_NOTIFICATION_KEY,
        archive_chat_env=chatlog.ARCHIVE_CHAT_ENV)


# ── Anomalies: a quiet day, a spike ──────────────────────────────────────────

def _quiet_window(now: datetime) -> str:
    """Identify the quiet window `now` falls in, not the calendar day.

    QUIET_HOURS_UTC runs 13:00 through 03:59, so one window straddles UTC
    midnight. Keying the once-per-window guard on `opsstats.day(now)` therefore let a
    single silence fire the note twice — reproduced at 22:15 (note), 23:45
    (correctly suppressed), 00:15 next day (second note for the same silence).
    Hours after midnight belong to the window that opened the day before.
    """
    start = now - timedelta(days=1) if now.hour < 12 else now
    return opsstats.day(start)


async def _quiet_check(now: datetime | None = None) -> bool:
    """Send the quiet-hours note if due. Returns whether it was sent."""
    now = now or datetime.now(timezone.utc)
    if now.hour not in QUIET_HOURS_UTC:
        return False
    last = await _read_int(LAST_SCAN_KEY)
    if not last:
        return False                     # never seen a scan since the key existed
    silent = now.timestamp() - last
    if silent < QUIET_AFTER_SECONDS:
        return False
    try:
        if not await _cache.add(f"opsseen:quiet:{_quiet_window(now)}", "1", STATS_TTL):
            return False
    except Exception:
        return False
    hours = int(silent // 3600)
    return await _notifier.send(
        "😶 <b>Quiet</b>\n"
        f"No successful scan for {hours}h, during US daytime. /health may still say ok — "
        "check the App Store build, the model, and Redis. /checkup runs all three.",
        await _buttons())


async def _watch_loop() -> None:
    while True:
        await asyncio.sleep(WATCH_INTERVAL_SECONDS)
        try:
            await _quiet_check()
        except Exception as exc:          # the loop must outlive any one check
            log.warning("quiet check failed: %s", type(exc).__name__)


def _start_watch() -> None:
    global _watch_task
    if _watch_task is not None:
        _watch_task.cancel()
        _watch_task = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _watch_task = loop.create_task(_watch_loop())


async def _spike_line(when: datetime, scans: int) -> str:
    """A 🔥 line when the day ran hot against the trailing week."""
    if scans < SPIKE_MIN_SCANS:
        return ""
    prior = [opsstats.day(when - timedelta(days=i)) for i in range(1, 8)]
    baseline = (await opsstats.sum_stat(prior, "scans_free") + await opsstats.sum_stat(prior, "scans_pro")) / 7
    if baseline <= 0 or scans < baseline * SPIKE_FACTOR:
        return ""
    return f"🔥 {scans / baseline:.1f}× the trailing week's daily average ({baseline:.1f}/day)"


# ── Clear and History: chatlog.py's, bound to this module's state ───────────

async def _remember_message(message_id: int, text: str | None = None) -> None:
    """`chatlog.remember_message` against the configured cache: the notifier's
    `on_sent`, and the poll loop's note of each operator message."""
    if _cache is not None:
        await chatlog.remember_message(_cache, message_id, text)


async def _clear_prompt() -> tuple[str, opsformat.Buttons]:
    assert _cache is not None
    text, offers, menu = await chatlog.clear_prompt(_cache)
    return text, offers + (await _buttons() if menu else [])


async def _clear_chat() -> None:
    if _notifier is None or _cache is None:
        return
    await chatlog.clear_chat(_notifier, _cache, _buttons, _status_text)


async def _history_text(argument: str) -> str:
    assert _cache is not None
    return await chatlog.history_text(_cache, argument)


# ── Test scan: a photo sent to the bot goes through the real pipeline ────────

async def _test_scan(photos: list[dict]) -> None:
    """Run the operator's photo through the scan pipeline and report.

    Telegram sends several sizes; the last is the largest (≤1280px, JPEG),
    close to what the app uploads. The result is rendered in full rather
    than as the app shows it, because the point is to see what the model
    said — and it is not counted as a scan, not fed to the feed, and not
    stored, because it is not a user.
    """
    if _scanner is None:
        await _notifier.send("🔬 Test scans are not wired up in this process.")
        return
    try:
        file_id = str(sorted(photos, key=lambda ph: int(ph.get("file_size") or 0))[-1]["file_id"])
    except (KeyError, IndexError, TypeError, ValueError):
        await _notifier.send("🔬 That photo had no file I could fetch.")
        return
    image = await _notifier.download_photo(file_id)
    if not image:
        await _notifier.send("🔬 Could not download the photo from Telegram. Try again.")
        return
    started = time.monotonic()
    try:
        result = await _scanner(image, "image/jpeg")
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        detail = getattr(exc, "detail", None) or type(exc).__name__
        await _notifier.send(
            f"🔬 <b>Test scan failed</b> after {time.monotonic() - started:.1f}s\n"
            f"/scan would answer <b>{status or 500}</b>: {html.escape(str(detail))}")
        return
    await _notifier.send(_test_scan_text(result), await _buttons())


def _price(value) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _test_scan_text(r: dict) -> str:
    name = html.escape(str(r.get("item_name") or "Unidentified item"))
    brand = html.escape(str(r.get("brand") or "Unknown"))
    category = html.escape(str(r.get("category") or "other"))
    elapsed = r.get("elapsed")
    head = "🔬 <b>Test scan</b>" + (f" · {float(elapsed):.1f}s" if isinstance(elapsed, (int, float)) else "")
    lines = [head, f"<b>{name}</b>",
             f"{CATEGORY_EMOJI.get(category, '📦')} {category} · {brand} · "
             f"{_price(r.get('est_value_low_usd'))}–{_price(r.get('est_value_high_usd')).lstrip('$')}"
             + (f" · expected {_price(r.get('expected_price_usd'))}" if r.get("expected_price_usd") else "")]
    if r.get("quick_sale_price_usd") or r.get("best_case_price_usd"):
        lines.append(f"Quick sale {_price(r.get('quick_sale_price_usd'))} · "
                     f"best case {_price(r.get('best_case_price_usd'))}")
    score = r.get("confidence_score")
    band = html.escape(str(r.get("confidence") or ""))
    summary = html.escape(str(r.get("confidence_summary") or ""))
    lines.append(f"Confidence {score} ({band})" + (f" — {summary}" if summary else ""))
    facts = [html.escape(str(r[k])) for k in ("condition_grade", "size", "era", "material") if r.get(k)]
    if facts:
        lines.append(" · ".join(facts))
    if r.get("demand") or r.get("supply"):
        lines.append(f"Demand {html.escape(str(r.get('demand') or '?'))} · supply {html.escape(str(r.get('supply') or '?'))}")
    reasons = [html.escape(str(x)) for x in (r.get("confidence_reasons") or [])[:3]]
    if reasons:
        lines += [f" • {x}" for x in reasons]
    if r.get("listing_title"):
        lines.append(f"<i>{html.escape(str(r['listing_title']))}</i>")
    lines.append(f"Prompt {html.escape(str(r.get('prompt_version') or '?'))} · source "
                 f"{html.escape(str(r.get('valuation_source') or 'model'))} · not counted as a scan, "
                 "photo not stored")
    return "\n".join(lines)
