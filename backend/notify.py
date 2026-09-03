"""Operator alerts to a private Telegram chat.

A single-operator service has no on-call rotation and no pager: production
telling *someone* what just happened means telling one phone. Telegram is the
cheapest reliable way to do that — the Bot API is free, needs no SDK, and a
message to a private chat is push-delivered.

Everything here is OFF unless both ``TELEGRAM_BOT_TOKEN`` and
``TELEGRAM_CHAT_ID`` are set, and every path is best-effort by construction:
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

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import auditlog
import ideas

log = logging.getLogger("snapworth.notify")

TELEGRAM_API = "https://api.telegram.org"

SEND_TIMEOUT_SECONDS = 10.0

# Counters live long enough for a 30-day spend view and the weekly report's
# two full weeks, plus slack; they are operational tallies, not records.
STATS_TTL = 60 * 60 * 24 * 35

# What the model costs, per million tokens, so spend can be derived from the
# token counts every call already reports. Defaults are Gemini 2.5 Flash's
# published rates (thinking tokens bill as output); Google changes prices and
# GEMINI_MODEL can point elsewhere, so both are env-overridable.
GEMINI_PRICE_INPUT_PER_M = float(os.environ.get("GEMINI_PRICE_INPUT_PER_M", "0.30"))
GEMINI_PRICE_OUTPUT_PER_M = float(os.environ.get("GEMINI_PRICE_OUTPUT_PER_M", "2.50"))
# A daily spend ceiling that pages once when crossed. 0 disables it.
GEMINI_DAILY_BUDGET_USD = float(os.environ.get("GEMINI_DAILY_BUDGET_USD", "0"))

# The live scan feed: one message per successful scan, item and price only.
# Persisted in the cache so the toggle survives deploys. On by default — the
# operator asked for it — and one command away from quiet.
FEED_KEY = "opsfeed:enabled"

# Brand tallies are keyed by whatever the model wrote, so the day's table is
# capped; categories are a closed set and need no cap.
TOP_BRANDS_CAP = 200

CATEGORY_EMOJI = {
    "clothing": "🧥", "shoes": "👟", "accessories": "👜", "electronics": "📱",
    "books": "📚", "furniture": "🪑", "home": "🏠", "sports": "⚽",
    "toys": "🧸", "collectibles": "🏺", "other": "📦",
}

# The weekly report goes out with Monday's digest, covering the seven days
# that just ended against the seven before.
WEEKLY_REPORT_WEEKDAY = 0

# First-sighting record for an originalTransactionId. Matches the proof and
# device-binding horizon: past it the subscription itself is the bound.
SUB_SEEN_TTL = 60 * 60 * 24 * 400

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

# Bot API long-poll. Telegram holds the request open until a message arrives
# or the timeout passes, so an idle loop costs one HTTP request per timeout.
POLL_TIMEOUT_SECONDS = 25

# Only one replica may poll getUpdates — Telegram rejects concurrent pollers
# and would hand each replica a random subset of messages. The lock is a
# cache NX write that the holder renews; a dead holder loses it within TTL.
POLL_LOCK_KEY = "opslock:tgpoll"
POLL_LOCK_TTL = 90
# How often a replica without the lock checks whether it has become free.
# Short, because this is exactly the gap between a deploy landing and the bot
# answering again: a cache read every fifteen seconds is nothing.
POLL_LOCK_RETRY_SECONDS = 15
# Where the poller left off, so a successor replica confirms what its
# predecessor already handled rather than being handed it again.
POLL_OFFSET_KEY = "opsstate:tgoffset"

# The deploy ping runs in the first seconds of a container's life, when the
# network is at its least reliable. It is also the one message whose absence
# is read as "the bot is broken", so it retries, with these pauses between
# attempts, before giving up.
DEPLOY_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0, 10.0)

# The week's most valuable scans, kept alongside the day's category and brand
# tallies for /finds and as grounding for /post. Item and price only.
TOP_FINDS_CAP = 8

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
SPARK = "▁▂▃▄▅▆▇█"

# /checkup's model probe. JSON, because the model runs in JSON mode; and room
# to think, because the first version asked for "OK" in 16 tokens and a
# thinking model spent them all thinking — an empty reply, reported as
# "Gemini: FAILED" while every real scan was succeeding.
PROBE_PROMPT = 'Return ONLY this JSON object and nothing else: {"ok": true}'
PROBE_MAX_TOKENS = 1024

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

# Every message id in the operator's chat — the bot's and the operator's — so
# 🧹 Clear can delete them. Telegram refuses anything older than 48 hours, so
# the list is pruned to that and capped; a longer memory would buy nothing.
MESSAGES_KEY = "opsstate:tgmsgs"
MESSAGES_CAP = 400
MESSAGES_TTL = 48 * 3600

COMMANDS: tuple[tuple[str, str], ...] = (
    ("status", "Active users, scans today, provider health"),
    ("subs", "Every subscription seen: plan, how obtained, renews"),
    ("users", "Devices seen, 7-day and 30-day actives, most active"),
    ("costs", "Gemini spend: today, 7 and 30 days, per scan, vs MRR"),
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
    ("checkup", "Redis, Gemini, DeviceCheck, TLS expiry — one screen"),
    ("clear", "Delete the last two days of this chat and start fresh"),
    ("feed", "Live scan feed: on, off, or show"),
    ("digest", "Yesterday's digest, now"),
    ("week", "Last 7 days against the 7 before"),
    ("help", "List commands"),
)

# The two operator tables. Each is one JSON document the cache can hand back
# whole — it cannot enumerate keys — bounded so a write never grows past a
# few hundred kilobytes. There are no accounts: "users" are pseudonymous
# devices, exactly as the audit log identifies them.
SUBS_INDEX_KEY = "opsidx:subs"
USERS_INDEX_KEY = "opsidx:users"
SUBS_INDEX_CAP = 500
USERS_INDEX_CAP = 500
INDEX_TTL = 60 * 60 * 24 * 400
TABLE_ROWS = 20

# Apple's offerType values.
OFFER_INTRODUCTORY, OFFER_PROMOTIONAL, OFFER_CODE = 1, 2, 3

# Inline-keyboard rows: (label, callback data). The data is fed straight back
# through `handle_command` as "/<data>", so buttons and commands share one path.
Buttons = list[list[tuple[str, str]]]


class TelegramNotifier:
    """Thin sendMessage client over the shared httpx stack."""

    def __init__(self, bot_token: str, chat_id: str, client=None) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._client = client          # injectable for tests
        # Told the message_id of every message this notifier sends, so /clear
        # can take them back. Set by `configure`; None is "don't bother".
        self.on_sent: Callable[[int], Awaitable[None]] | None = None

    async def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS)
        return self._client

    async def send(self, text: str, buttons: Buttons | None = None, *,
                   ask: str | None = None) -> bool:
        """Deliver one message. Returns success; never raises.

        `ask` turns the message into a question: Telegram opens the reply box
        on it with that placeholder, so a button can stand in for a command
        that needs typed input — the operator taps, types, sends.

        Failures log the exception *class* only: httpx error messages quote the
        request URL, and the URL carries the bot token.
        """
        try:
            client = await self._http()
            payload: dict = {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if ask is not None:
                payload["reply_markup"] = {"force_reply": True, "selective": True,
                                           "input_field_placeholder": ask[:64]}
            elif buttons:
                payload["reply_markup"] = {"inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row]
                    for row in buttons]}
            resp = await client.post(
                f"{TELEGRAM_API}/bot{self._token}/sendMessage", json=payload)
            if resp.status_code != 200:
                # Telegram's own reason ("can't parse entities: …") names the
                # bug; the bare status code never did. It carries no token.
                log.warning("telegram send failed: HTTP %s %s",
                            resp.status_code, self._description(resp))
                return False
            if self.on_sent is not None:
                try:
                    message_id = int(((resp.json() or {}).get("result") or {}).get("message_id"))
                    await self.on_sent(message_id)
                except Exception:
                    pass
            return True
        except Exception as exc:
            log.warning("telegram send failed: %s", type(exc).__name__)
            return False

    @staticmethod
    def _description(resp) -> str:
        try:
            return str((resp.json() or {}).get("description") or "")[:200]
        except Exception:
            return ""

    @property
    def chat_id(self) -> str:
        return self._chat_id

    async def get_updates(self, offset: int | None) -> list[dict]:
        """Long-poll for incoming messages. Returns [] on any failure."""
        params: dict = {"timeout": POLL_TIMEOUT_SECONDS,
                        "allowed_updates": '["message","callback_query"]'}
        if offset is not None:
            params["offset"] = offset
        try:
            client = await self._http()
            resp = await client.get(
                f"{TELEGRAM_API}/bot{self._token}/getUpdates",
                params=params, timeout=POLL_TIMEOUT_SECONDS + 10)
            if resp.status_code != 200:
                log.warning("telegram poll failed: HTTP %s", resp.status_code)
                return []
            body = resp.json()
            return list(body.get("result") or []) if body.get("ok") else []
        except Exception as exc:
            log.warning("telegram poll failed: %s", type(exc).__name__)
            return []

    async def delete_messages(self, message_ids: list[int]) -> int:
        """Delete the bot's own (and, in a private chat, the operator's)
        messages, up to 100 per call. Returns how many ids Telegram accepted.
        Messages older than 48 hours cannot be deleted by any bot — that is
        Telegram's rule, and the operator clears those from the chat menu."""
        deleted = 0
        try:
            client = await self._http()
            for start in range(0, len(message_ids), 100):
                chunk = message_ids[start:start + 100]
                resp = await client.post(
                    f"{TELEGRAM_API}/bot{self._token}/deleteMessages",
                    json={"chat_id": self._chat_id, "message_ids": chunk})
                if resp.status_code == 200 and (resp.json() or {}).get("ok"):
                    deleted += len(chunk)
                else:
                    log.info("telegram deleteMessages refused: HTTP %s %s",
                             resp.status_code, self._description(resp))
        except Exception as exc:
            log.warning("telegram deleteMessages failed: %s", type(exc).__name__)
        return deleted

    async def download_photo(self, file_id: str, max_bytes: int = 10 * 1024 * 1024) -> bytes | None:
        """Fetch a photo the operator sent, via getFile. None on any failure."""
        try:
            client = await self._http()
            meta = await client.get(f"{TELEGRAM_API}/bot{self._token}/getFile",
                                    params={"file_id": file_id})
            path = ((meta.json() or {}).get("result") or {}).get("file_path") if meta.status_code == 200 else None
            if not path:
                log.warning("telegram getFile failed: HTTP %s %s", meta.status_code, self._description(meta))
                return None
            resp = await client.get(f"{TELEGRAM_API}/file/bot{self._token}/{path}",
                                    timeout=SEND_TIMEOUT_SECONDS * 3)
            if resp.status_code != 200 or len(resp.content) > max_bytes:
                log.warning("telegram file download failed: HTTP %s, %d bytes",
                            resp.status_code, len(resp.content))
                return None
            return resp.content
        except Exception as exc:
            log.warning("telegram file download failed: %s", type(exc).__name__)
            return None

    async def answer_callback(self, callback_id: str) -> None:
        """Stop the button's spinner. Best-effort; the reply is sent regardless."""
        try:
            client = await self._http()
            await client.post(
                f"{TELEGRAM_API}/bot{self._token}/answerCallbackQuery",
                json={"callback_query_id": callback_id})
        except Exception as exc:
            log.debug("telegram answerCallbackQuery failed: %s", type(exc).__name__)

    async def set_commands(self, commands=COMMANDS) -> bool:
        """Publish the command menu Telegram shows behind the "/" button."""
        try:
            client = await self._http()
            resp = await client.post(
                f"{TELEGRAM_API}/bot{self._token}/setMyCommands",
                json={"commands": [{"command": c, "description": d}
                                   for c, d in commands]})
            return resp.status_code == 200
        except Exception as exc:
            log.warning("telegram setMyCommands failed: %s", type(exc).__name__)
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            try:
                await client.aclose()
            except Exception:
                pass


# ── Module state, wired by `configure` from the app lifespan ─────────────────
_notifier: TelegramNotifier | None = None
_cache = None                                   # ResilientCache once configured
_digest_task: asyncio.Task | None = None
_command_task: asyncio.Task | None = None
_watch_task: asyncio.Task | None = None
_tasks: set[asyncio.Task] = set()

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

# Identifies this replica as the poll-lock holder.
_poll_token = secrets.token_hex(8)

# In-process alert throttling. Per-replica on purpose: an alert is about *this*
# process's view, and a duplicate from a second replica during an incident is
# an acceptable cost for not paying a cache round-trip on the failure path.
_alert_last_sent: dict[str, float] = {}
_alert_awaiting_recovery: set[str] = set()


def enabled() -> bool:
    return _notifier is not None


def configure(cache, notifier: TelegramNotifier | None = None,
              status_provider: Callable[[], dict] | None = None,
              social=None, generator: Callable[..., Awaitable[str]] | None = None,
              scanner: Callable[..., Awaitable[dict]] | None = None) -> None:
    """Wire the notifier from the environment. Called once at startup.

    With the env vars unset this leaves everything disabled and every public
    function a no-op — the feature costs nothing until it is turned on.
    """
    global _notifier, _cache, _status_provider, _social, _generator, _scanner
    _cache = cache
    _status_provider = status_provider
    _social = social
    _generator = generator
    _scanner = scanner

    if notifier is not None:
        _notifier = notifier
        _notifier.on_sent = _remember_message
    else:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not (token and chat_id):
            _notifier = None
            log.info("telegram alerts disabled — TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID unset")
            return
        _notifier = TelegramNotifier(token, chat_id)
        _notifier.on_sent = _remember_message

    _start_digest()
    _start_command_loop()
    _start_watch()
    _spawn(_notifier.set_commands())
    log.info("telegram alerts enabled", extra={"digest_utc_hour": _digest_hour()})


async def aclose() -> None:
    """Tear down background work. Alerts in flight at shutdown are dropped."""
    global _notifier, _digest_task, _command_task, _watch_task
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
    _alert_last_sent.clear()
    _alert_awaiting_recovery.clear()
    await _release_poll_lock()
    if _notifier is not None:
        notifier, _notifier = _notifier, None
        await notifier.aclose()


def _spawn(coro) -> None:
    """Run `coro` in the background, holding a reference until it finishes.

    Without the reference set, an un-awaited task is garbage-collectable
    mid-flight. Outside a running loop (sync tests, tooling) the coroutine is
    closed unrun rather than raising.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ── Daily counters ───────────────────────────────────────────────────────────

def _date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%d %b %Y")


def _day(at: datetime | None = None) -> str:
    return (at or datetime.now(timezone.utc)).strftime("%Y%m%d")


def _stat_key(day: str, name: str) -> str:
    return f"opsstats:{day}:{name}"


async def _bump(name: str) -> None:
    if _cache is None:
        return
    try:
        await _cache.incr(_stat_key(_day(), name), STATS_TTL)
    except Exception as exc:
        log.debug("ops counter %s failed: %s", name, type(exc).__name__)


def count_scan(tier: str) -> None:
    """Tally one successful scan. Fire-and-forget; free when alerts are off."""
    if _notifier is None:
        return
    _spawn(_bump("scans_pro" if tier == "pro" else "scans_free"))


def count_scan_failure() -> None:
    """Tally one scan that reached the model and still failed the user."""
    if _notifier is None:
        return
    _spawn(_bump("scans_failed"))


# ── Subscription events ──────────────────────────────────────────────────────

async def entitlement_recorded(subject: str, ent) -> None:
    """Note a verified StoreKit transaction. Awaited, but never raises.

    Called from /auth/entitlement, which fires at cold launch, purchase,
    restore and Transaction.updates — so almost every call is a re-sync of a
    subscription already seen. The cache keeps this quiet: a Pro result only
    alerts on the first sighting of its originalTransactionId (renewals and
    re-syncs share it), and a not-Pro result — a refund, revocation or expiry
    the client just proved — alerts at most once per subject per day.
    """
    if _notifier is None or _cache is None:
        return
    try:
        if ent.tier == "pro":
            otid = ent.original_transaction_id
            if not otid:
                return
            await _index_subscription(subject, ent)
            if not await _cache.add(f"opsseen:sub:{otid}", "1", SUB_SEEN_TTL):
                return
            purchased = getattr(ent, "original_purchase_at", None)
            # Unknown purchase date reads as new: Apple always supplies it, so
            # its absence is a test fixture, not a customer.
            is_new = (purchased is None
                      or time.time() - purchased < NEW_SUBSCRIPTION_WINDOW_SECONDS)
            if is_new:
                await _cache.incr(_stat_key(_day(), "new_subs"), STATS_TTL)
                headline = "🎉 <b>New Pro subscription</b>"
            else:
                headline = ("👋 <b>Existing Pro subscriber checked in</b> "
                            "(first time this bot has seen them)")
            product = html.escape(ent.product_id or "unknown product")
            environment = html.escape(ent.environment)
            lines = [headline, f"{product} ({environment}) · {_acquisition(ent)}"]
            if purchased is not None:
                lines.append(f"first purchased {_date(purchased)}")
            if ent.expires_at:
                lines.append(f"renews or expires {_date(ent.expires_at)}")
            await _notifier.send("\n".join(lines))
        else:
            if not await _cache.add(
                    f"opsseen:down:{subject}", "1", DOWNGRADE_THROTTLE_TTL):
                return
            who = html.escape(auditlog.pseudonymise(subject))
            await _notifier.send(
                "⚠️ <b>Subscription ended</b>\n"
                f"Subject <code>{who}</code> presented a transaction that "
                "verified as not-Pro — refunded, revoked or expired.")
    except Exception as exc:
        log.warning("subscription alert failed: %s", type(exc).__name__)


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
        f"seen {hours}h ago — likely sharing, not a replaced phone.")


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
        # One ping per commit, however many replicas boot it or however often
        # Railway restarts the container. A build that crash-loops has other
        # symptoms; a stream of identical "deployed" messages would only bury them.
        if not await _cache.add(guard, "1", STATS_TTL):
            return
    except Exception as exc:
        # The cache is not up yet — which, seconds into a boot, it may well
        # not be. A duplicate ping is a shrug; a missing one is "did the
        # deploy land?" asked over and over. Send anyway.
        log.warning("deploy ping guard failed, sending anyway: %s", type(exc).__name__)
    text = _deploy_text(commit, cache_backend, auth_enforcing, info)
    notifier = _notifier
    for attempt, delay in enumerate((0.0, *DEPLOY_RETRY_DELAYS)):
        if delay:
            await asyncio.sleep(delay)
        if notifier is None or await notifier.send(text):
            return
        log.warning("deploy ping attempt %d failed", attempt + 1)
    # Every attempt failed: give the guard back so the next boot of this same
    # commit — a Railway restart, say — gets to try again.
    try:
        await _cache.delete(guard)
    except Exception:
        pass


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

def _alert(key: str, text: str) -> None:
    if _notifier is None:
        return
    now = time.monotonic()
    last = _alert_last_sent.get(key)
    if last is not None and now - last < ALERT_MIN_INTERVAL_SECONDS:
        return
    _alert_last_sent[key] = now
    _alert_awaiting_recovery.add(key)
    _spawn(_notifier.send(text))


def _recovered(key: str, text: str) -> None:
    """Send the all-clear — only if the matching alert actually went out."""
    if _notifier is None or key not in _alert_awaiting_recovery:
        return
    _alert_awaiting_recovery.discard(key)
    # Clear the throttle so a relapse alerts immediately rather than being
    # mistaken for a repeat of the incident that just ended.
    _alert_last_sent.pop(key, None)
    _spawn(_notifier.send(text))


def model_unhealthy(kind: str | None) -> None:
    """The AI provider stopped answering — the same state /health reports."""
    reason = html.escape(kind or "unknown")
    extra = ""
    if kind == "quota_exhausted":
        extra = "\nThis one will not self-heal: top up the provider's billing."
    _alert("model",
           f"🔴 <b>AI provider degraded</b>\nScans are failing ({reason})."
           f"{extra}")


def model_recovered() -> None:
    _recovered("model", "🟢 <b>AI provider recovered</b> — scans are succeeding again.")


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


async def _read_stat(day: str, name: str) -> int:
    try:
        raw = await _cache.get(_stat_key(day, name))
        return int(raw or 0)
    except Exception:
        return 0


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
    day = _day(yesterday)
    try:
        if not await _cache.add(f"opsstats:digestsent:{day}", "1", STATS_TTL):
            return False
    except Exception as exc:
        log.warning("digest guard failed, skipping: %s", type(exc).__name__)
        return False

    return await _notifier.send(await _digest_text(yesterday), await _buttons())


async def _digest_text(when: datetime) -> str:
    day = _day(when)
    free = await _read_stat(day, "scans_free")
    pro = await _read_stat(day, "scans_pro")
    failed = await _read_stat(day, "scans_failed")
    subs = await _read_stat(day, "new_subs")
    users = await _read_stat(day, "active_users")
    lines = [
        f"📊 <b>SnapWorth — {when.strftime('%Y-%m-%d')}</b>",
        f"Active users: {users}",
        f"Scans: {free + pro} ok ({free} free · {pro} Pro) · {failed} failed",
        f"New subscriptions: {subs}",
        await _subscribers_line(),
        await _spend_line([day], free + pro),
    ]
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
            await _index_user(who, tier=tier)
        day = _day()
        if await _cache.add(f"opsseen:d:{day}:{who}", "1", STATS_TTL):
            await _cache.incr(_stat_key(day, "active_users"), STATS_TTL)
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


async def _status_text() -> str:
    now = datetime.now(timezone.utc)
    day = _day(now)
    window = _window()
    active_now = await _read_int(f"opsact:w:{window}")
    active_today = await _read_stat(day, "active_users")
    free = await _read_stat(day, "scans_free")
    pro = await _read_stat(day, "scans_pro")
    failed = await _read_stat(day, "scans_failed")
    subs = await _read_stat(day, "new_subs")

    lines = [
        "📡 <b>SnapWorth status</b>",
        f"Active users: {active_now} since {_window_start(window):%H:%M} UTC "
        f"· {active_today} today",
        f"Scans today: {free + pro} ok ({free} free · {pro} Pro) · {failed} failed",
        f"New subscriptions today: {subs}",
        await _subscribers_line(),
        await _spend_line([day], free + pro),
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
                f"Build <code>{html.escape(str(info.get('commit', '?')))}</code> · "
                f"cache {html.escape(str(info.get('cache', '?')))} · auth {auth}")
    return "\n".join(lines)


async def _buttons() -> Buttons:
    feed = "🔕 Feed off" if await _feed_enabled() else "🔔 Feed on"
    return [[("🔄 Refresh", "status"), ("📊 Digest", "digest"), ("📈 Week", "week")],
            [("💳 Subs", "subs"), ("👥 Users", "users"), ("💸 Costs", "costs")],
            [("📣 Social", "social"), ("🏆 Finds", "finds"), ("📝 Post ideas", "post")],
            [("🗓 Calendar", "calendar"), ("🩺 Checkup", "checkup"), (feed, "feed toggle")],
            [("✍️ Caption", "ask caption"), ("🪝 Hooks", "ask hooks"), ("💬 Reply", "ask reply")],
            [("💵 Price", "ask price"), ("📈 Trend", "ask trend"), ("👤 User", "ask user")],
            [("🧹 Clear chat", "clear")]]


async def handle_command(text: str) -> str | None:
    """Reply text for one operator message, or None to stay silent."""
    reply = await handle_command_with_buttons(text)
    return reply[0] if reply else None


async def handle_command_with_buttons(text: str) -> tuple[str, Buttons] | None:
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
    if command == "/users":
        return await _users_text(), await _buttons()
    if command == "/costs":
        return await _costs_text(), await _buttons()
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
        return await _user_text(rest), await _buttons()
    if command == "/checkup":
        return await _checkup_text(), await _buttons()
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
        await _cache.set(POLL_OFFSET_KEY, str(offset), INDEX_TTL)
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
            continue
        if callback:
            await _notifier.answer_callback(str(callback.get("id", "")))
        if not callback and message.get("photo"):
            await _test_scan(message["photo"])
            handled += 1
            continue
        if text.startswith("/ask "):
            command = text.split(None, 1)[1].strip().lower()
            if command in ASKS:
                question, placeholder = ASKS[command]
                await _notifier.send(question, ask=placeholder)
                handled += 1
            continue
        if text.split("@", 1)[0].lower() == "/clear":
            await _clear_chat()
            handled += 1
            continue
        reply = await handle_command_with_buttons(text)
        if reply:
            await _notifier.send(reply[0], reply[1])
            handled += 1
    if offset != before:
        await _remember_offset(offset)
    return offset, handled


async def _command_loop() -> None:
    offset: int | None = await _read_offset()
    while True:
        try:
            if not await _hold_poll_lock():
                await asyncio.sleep(POLL_LOCK_RETRY_SECONDS)
                continue
            before = offset
            offset, _ = await poll_once(offset)
            if offset == before:
                # Empty poll: a long-poll timeout (25s spent) or a transport
                # failure (returned at once). The pause only matters for the
                # second, so a failing API is not hammered.
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:           # the loop must outlive any one poll
            log.warning("command loop error: %s", type(exc).__name__)
            await asyncio.sleep(5)


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

def _normalise_category(category: str | None) -> str:
    key = (category or "").strip().lower()
    return key if key in CATEGORY_EMOJI else "other"


def _clean_brand(brand: str | None) -> str | None:
    """A brand worth tallying, or None. Model output: trimmed and bounded."""
    value = " ".join((brand or "").split())[:40]
    if value.lower() in {"", "unknown", "n/a", "none", "generic", "unbranded"}:
        return None
    return value


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
    emoji = CATEGORY_EMOJI[_normalise_category(category)]
    name = html.escape(" ".join((item_name or "").split())[:80] or "Unidentified item")
    band = html.escape((confidence or "").strip().lower() or "unknown")
    who = "Pro" if tier == "pro" else "free"
    return (f"{emoji} <b>{name}</b>\n"
            f"{_normalise_category(category)} · ${low:,.0f}–{high:,.0f} · "
            f"{band} confidence · {who}")


async def _tally_top(day: str, category: str, brand: str | None,
                     find: dict | None = None) -> None:
    """Read-modify-write of the day's category and brand counts, and its
    handful of most valuable finds.

    One small JSON document rather than a key per brand, because the cache
    interface cannot enumerate keys and the report needs the whole table.
    A lost update between two replicas costs one count, which is fine for a
    tally that exists to say "clothing 5 · Nike ×3".
    """
    key = _stat_key(day, "top")
    try:
        doc = json.loads(await _cache.get(key) or "{}")
    except Exception:
        doc = {}
    cats = doc.get("cats") if isinstance(doc.get("cats"), dict) else {}
    brands = doc.get("brands") if isinstance(doc.get("brands"), dict) else {}
    finds = doc.get("finds") if isinstance(doc.get("finds"), list) else []
    cats[category] = int(cats.get(category, 0)) + 1
    if brand is not None and (brand in brands or len(brands) < TOP_BRANDS_CAP):
        brands[brand] = int(brands.get(brand, 0)) + 1
    if find is not None:
        finds = sorted([*finds, find], key=lambda f: -float(f.get("hi") or 0))[:TOP_FINDS_CAP]
    await _cache.set(key, json.dumps({"cats": cats, "brands": brands, "finds": finds}),
                     STATS_TTL)


def _find_record(*, item_name: str, brand: str | None, category: str,
                 low: float, high: float, tier: str) -> dict:
    """What /finds keeps about a scan: the item and its price, nothing else."""
    return {"n": " ".join((item_name or "").split())[:60] or "Unidentified item",
            "b": _clean_brand(brand), "c": _normalise_category(category),
            "lo": round(float(low)), "hi": round(float(high)),
            "t": "pro" if tier == "pro" else "free"}


async def _top_text(day: str, limit: int = 3) -> str:
    try:
        doc = json.loads(await _cache.get(_stat_key(day, "top")) or "{}")
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
                     subject: str | None = None, elapsed_ms: int | None = None) -> None:
    try:
        await _bump("scans_pro" if tier == "pro" else "scans_free")
        await _cache.set(LAST_SCAN_KEY, str(int(time.time())), STATS_TTL)
        if elapsed_ms:
            await _cache.incr(_stat_key(_day(), "scan_ms"), STATS_TTL, int(elapsed_ms))
        await _tally_top(_day(), _normalise_category(category), _clean_brand(brand),
                         _find_record(item_name=item_name, brand=brand, category=category,
                                      low=low, high=high, tier=tier))
        if subject:
            await _index_user(auditlog.pseudonymise(subject), tier=tier, scanned=True)
        if await _feed_enabled():
            await _notifier.send(_feed_text(
                item_name=item_name, category=category, low=low, high=high,
                confidence=confidence, tier=tier))
    except Exception as exc:
        log.debug("scan feed failed: %s", type(exc).__name__)


def scan_completed(*, tier: str, item_name: str, brand: str | None, category: str,
                   low: float, high: float, confidence: str,
                   subject: str | None = None, elapsed_ms: int | None = None) -> None:
    """A scan produced a valuation. Counts it, tallies what it was, and — when
    the feed is on — tells the operator. Fire-and-forget; item and price only,
    never who scanned it and never the photo."""
    if _notifier is None or _cache is None:
        return
    _spawn(_note_scan(tier=tier, item_name=item_name, brand=brand, category=category,
                      low=low, high=high, confidence=confidence, subject=subject,
                      elapsed_ms=elapsed_ms))


# ── Weekly report ────────────────────────────────────────────────────────────

async def _sum_stat(days: list[str], name: str) -> int:
    total = 0
    for day in days:
        total += await _read_stat(day, name)
    return total


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
    this_week = [_day(datetime.combine(end - timedelta(days=i), datetime.min.time(),
                                       tzinfo=timezone.utc)) for i in range(7)]
    last_week = [_day(datetime.combine(end - timedelta(days=i), datetime.min.time(),
                                       tzinfo=timezone.utc)) for i in range(7, 14)]

    async def pair(name: str) -> tuple[int, int]:
        return await _sum_stat(this_week, name), await _sum_stat(last_week, name)

    free_now, free_prev = await pair("scans_free")
    pro_now, pro_prev = await pair("scans_pro")
    failed_now, failed_prev = await pair("scans_failed")
    users_now, users_prev = await pair("active_users")
    subs_now, subs_prev = await pair("new_subs")
    scans_now, scans_prev = free_now + pro_now, free_prev + pro_prev
    spend_now = await _spend(this_week)
    spend_prev = await _spend(last_week)

    start = end - timedelta(days=6)
    return "\n".join([
        f"📈 <b>Week {start.strftime('%d %b')} – {end.strftime('%d %b')}</b>",
        f"Scans: {scans_now} ({free_now} free · {pro_now} Pro) {_trend(scans_now, scans_prev)}",
        f"Failed: {failed_now} {_trend(failed_now, failed_prev)}",
        f"Active user-days: {users_now} {_trend(users_now, users_prev)}",
        f"New subscriptions: {subs_now} {_trend(subs_now, subs_prev)}",
        f"Gemini spend: {_usd(spend_now)} {_trend(round(spend_now * 100), round(spend_prev * 100))}",
        f"vs {scans_prev} scans · {users_prev} user-days · {subs_prev} subs · "
        f"{_usd(spend_prev)} the week before",
    ])


async def send_weekly(now: datetime | None = None) -> bool:
    """Send the weekly report once, however many replicas reach Monday."""
    if _notifier is None or _cache is None:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        if not await _cache.add(f"opsstats:weeklysent:{_day(now)}", "1", STATS_TTL):
            return False
    except Exception as exc:
        log.warning("weekly guard failed, skipping: %s", type(exc).__name__)
        return False
    return await _notifier.send(await _weekly_text(now), await _buttons())


# ── Operator tables: subscriptions and devices ───────────────────────────────

async def _read_index(key: str) -> dict:
    try:
        doc = json.loads(await _cache.get(key) or "{}")
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


async def _write_index(key: str, doc: dict, cap: int, recency: str) -> None:
    if len(doc) > cap:
        # Drop the least recently seen until it fits.
        for stale in sorted(doc, key=lambda k: doc[k].get(recency, 0))[:len(doc) - cap]:
            doc.pop(stale, None)
    await _cache.set(key, json.dumps(doc, separators=(",", ":")), INDEX_TTL)


def _acquisition(ent) -> str:
    """How a subscription was obtained, in the operator's words."""
    offer = getattr(ent, "offer_type", None)
    discount = getattr(ent, "offer_discount_type", None)
    if offer == OFFER_CODE:
        return "offer code"
    if offer == OFFER_PROMOTIONAL:
        return "promo offer"
    if offer == OFFER_INTRODUCTORY:
        return "trial" if discount == "FREE_TRIAL" else "intro offer"
    return "paid"


async def _index_subscription(subject: str, ent) -> None:
    doc = await _read_index(SUBS_INDEX_KEY)
    otid = str(ent.original_transaction_id)
    entry = doc.get(otid) if isinstance(doc.get(otid), dict) else {}
    entry.update({
        "product": ent.product_id, "env": ent.environment,
        "first": getattr(ent, "original_purchase_at", None),
        "expires": ent.expires_at,
        "acq": _acquisition(ent),
        "price": getattr(ent, "price", None), "currency": getattr(ent, "currency", None),
        "who": auditlog.pseudonymise(subject)[:6],
        "seen": int(time.time()),
    })
    doc[otid] = entry
    await _write_index(SUBS_INDEX_KEY, doc, SUBS_INDEX_CAP, "seen")


async def _index_user(who: str, *, tier: str, scanned: bool = False) -> None:
    doc = await _read_index(USERS_INDEX_KEY)
    now = int(time.time())
    entry = doc.get(who) if isinstance(doc.get(who), dict) else {"first": now, "scans": 0}
    entry["last"] = now
    entry["tier"] = "pro" if tier == "pro" else "free"
    if scanned:
        entry["scans"] = int(entry.get("scans", 0)) + 1
    doc[who] = entry
    await _write_index(USERS_INDEX_KEY, doc, USERS_INDEX_CAP, "last")


def _plan(product: str | None) -> str:
    return (product or "?").replace("com.snapworth.", "")


def _money(amount: float, currency: str | None) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency or "")
    return f"{symbol}{amount:,.2f}" if symbol else f"{amount:,.2f} {currency or ''}".strip()


def _subs_summary(doc: dict) -> tuple[int, int, int, int, dict[str, float]]:
    """(active, paid, comped, expired, mrr by currency)."""
    now = time.time()
    active = paid = comped = expired = 0
    mrr: dict[str, float] = {}
    for e in doc.values():
        alive = e.get("expires") is None or float(e["expires"]) > now
        if not alive:
            expired += 1
            continue
        active += 1
        if e.get("acq") == "paid":
            paid += 1
            price, cur = e.get("price"), e.get("currency") or "?"
            if isinstance(price, (int, float)) and price > 0:
                monthly = price / 12 if "yearly" in _plan(e.get("product")) else price
                mrr[cur] = mrr.get(cur, 0.0) + monthly
        else:
            comped += 1
    return active, paid, comped, expired, mrr


async def _subscribers_line() -> str:
    active, paid, comped, _, _ = _subs_summary(await _read_index(SUBS_INDEX_KEY))
    return f"Subscribers: {active} active · {paid} paid · {comped} comped/trial"


async def _subs_text() -> str:
    doc = await _read_index(SUBS_INDEX_KEY)
    active, paid, comped, expired, mrr = _subs_summary(doc)
    lines = [f"💳 <b>Subscriptions</b> — {active} active · {paid} paid · "
             f"{comped} comped/trial · {expired} expired"]
    if mrr:
        lines.append("MRR ≈ " + " + ".join(_money(v, c) for c, v in sorted(mrr.items()))
                     + " (paid plans, from transaction prices)")
    else:
        lines.append("MRR ≈ n/a (no priced paid plan seen yet)")
    if not doc:
        lines.append("No subscription has synced since the bot started watching.")
        return "\n".join(lines)

    now = time.time()
    rows = sorted(doc.values(),
                  key=lambda e: (not (e.get("expires") is None or float(e["expires"]) > now),
                                 float(e.get("expires") or 0)))
    header = f"{'plan':<8}{'via':<11}{'since':<8}{'renews':<8}{'seen':<7}{'id':<6}"
    body = [header]
    for e in rows[:TABLE_ROWS]:
        alive = e.get("expires") is None or float(e["expires"]) > now
        renews = _date(int(e["expires"]))[:6] if e.get("expires") else "never"
        if not alive:
            renews = "ended"
        body.append(
            f"{_plan(e.get('product')):<8}{str(e.get('acq') or '?'):<11}"
            f"{(_date(int(e['first']))[:6] if e.get('first') else '?'):<8}"
            f"{renews:<8}{(_date(int(e['seen']))[:6] if e.get('seen') else '?'):<7}"
            f"{str(e.get('who') or ''):<6}")
    if len(rows) > TABLE_ROWS:
        body.append(f"… and {len(rows) - TABLE_ROWS} more")
    lines.append("<pre>" + html.escape("\n".join(body)) + "</pre>")
    due = [e for e in doc.values()
           if e.get("expires") and now < float(e["expires"]) < now + 7 * 86400]
    if due:
        paid_due = [e for e in due if e.get("acq") == "paid"]
        value: dict[str, float] = {}
        for e in paid_due:
            if isinstance(e.get("price"), (int, float)):
                value[e.get("currency") or "?"] = value.get(e.get("currency") or "?", 0.0) + e["price"]
        worth = (" · " + " + ".join(_money(v, c) for c, v in sorted(value.items()))
                 if value else "")
        lines.append(f"Due in 7 days: {len(due)} renew or end ({len(paid_due)} paid{worth})")
    lines.append("Only subscriptions that have synced since the bot started are listed; "
                 "every active one checks in at its next app launch.")
    return "\n".join(lines)


async def _users_text() -> str:
    doc = await _read_index(USERS_INDEX_KEY)
    now = time.time()
    week = sum(1 for e in doc.values() if now - float(e.get("last", 0)) < 7 * 86400)
    month = sum(1 for e in doc.values() if now - float(e.get("last", 0)) < 30 * 86400)
    today = sum(1 for e in doc.values() if _day(datetime.fromtimestamp(
        float(e.get("last", 0)), timezone.utc)) == _day())
    pro = sum(1 for e in doc.values() if e.get("tier") == "pro")
    lines = [f"👥 <b>Devices</b> — {len(doc)} seen · {month} last 30d · {week} last 7d · "
             f"{today} today · {pro} Pro"]
    if not doc:
        lines.append("No device has been seen since the bot started watching.")
        return "\n".join(lines)
    rows = sorted(doc.items(), key=lambda kv: (-int(kv[1].get("scans", 0)),
                                               -float(kv[1].get("last", 0))))
    body = [f"{'id':<8}{'tier':<6}{'scans':<7}{'first':<8}{'last':<7}"]
    for who, e in rows[:TABLE_ROWS]:
        body.append(
            f"{who[:6]:<8}{('Pro' if e.get('tier') == 'pro' else 'free'):<6}"
            f"{int(e.get('scans', 0)):<7}"
            f"{(_date(int(e['first']))[:6] if e.get('first') else '?'):<8}"
            f"{(_date(int(e['last']))[:6] if e.get('last') else '?'):<7}")
    if len(rows) > TABLE_ROWS:
        body.append(f"… and {len(rows) - TABLE_ROWS} more")
    lines.append("<pre>" + html.escape("\n".join(body)) + "</pre>")
    lines.append("Devices, not people — there are no accounts. Ids are the audit log's pseudonyms.")
    return "\n".join(lines)


# ── Gemini spend ─────────────────────────────────────────────────────────────

def _cost_usd(tok_in: int, tok_out: int) -> float:
    return (tok_in / 1e6) * GEMINI_PRICE_INPUT_PER_M + (tok_out / 1e6) * GEMINI_PRICE_OUTPUT_PER_M


def _usd(amount: float) -> str:
    return f"${amount:,.2f}"


def _usd_fine(amount: float) -> str:
    """Per-scan money: three decimals below ten cents, or the number lies."""
    return f"${amount:,.3f}" if amount < 0.10 else f"${amount:,.2f}"


def _kilo(n: int) -> str:
    return f"{n / 1000:.1f}K" if n >= 1000 else str(n)


async def _spend(days: list[str]) -> float:
    return _cost_usd(await _sum_stat(days, "tok_in"), await _sum_stat(days, "tok_out"))


async def _spend_line(days: list[str], scans: int) -> str:
    spend = await _spend(days)
    parts = [f"Gemini ≈ {_usd(spend)}"]
    if scans:
        parts.append(f"{_usd_fine(spend / scans)}/scan")
        avg_ms = await _sum_stat(days, "scan_ms")
        if avg_ms:
            parts.append(f"avg scan {avg_ms / scans / 1000:.1f}s")
    return " · ".join(parts)


async def _note_usage(label: str, usage: dict) -> None:
    try:
        day = _day()
        tok_in = int(usage.get("prompt_tokens") or 0)
        tok_out = int(usage.get("output_tokens") or 0) + int(usage.get("thoughts_tokens") or 0)
        if tok_in:
            await _cache.incr(_stat_key(day, "tok_in"), STATS_TTL, tok_in)
        if tok_out:
            await _cache.incr(_stat_key(day, "tok_out"), STATS_TTL, tok_out)
        await _cache.incr(_stat_key(day, "model_calls"), STATS_TTL)
        await _cache.incr(_stat_key(day, f"calls_{label}"), STATS_TTL)

        budget = GEMINI_DAILY_BUDGET_USD
        if budget > 0:
            spend = await _spend([day])
            if spend > budget and await _cache.add(f"opsseen:budget:{day}", "1", STATS_TTL):
                await _notifier.send(
                    "💸 <b>Gemini spend over budget</b>\n"
                    f"Today ≈ {_usd(spend)} against a {_usd(budget)} daily budget. "
                    "Scans keep working; this is a heads-up, not a cut-off.")
    except Exception as exc:
        log.debug("usage note failed: %s", type(exc).__name__)


def model_usage(label: str, usage: dict | None) -> None:
    """Tally one model call's tokens. Fire-and-forget; free when alerts are off."""
    if _notifier is None or _cache is None:
        return
    _spawn(_note_usage(label, dict(usage or {})))


def _days_ending_today(n: int, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    return [_day(now - timedelta(days=i)) for i in range(n)]


async def _costs_text() -> str:
    lines = ["💸 <b>Gemini spend</b>"]
    for label, n in (("Today", 1), ("Last 7 days", 7), ("Last 30 days", 30)):
        days = _days_ending_today(n)
        tok_in = await _sum_stat(days, "tok_in")
        tok_out = await _sum_stat(days, "tok_out")
        calls = await _sum_stat(days, "model_calls")
        scans = await _sum_stat(days, "scans_free") + await _sum_stat(days, "scans_pro")
        spend = _cost_usd(tok_in, tok_out)
        parts = [f"{label}: {_usd(spend)}", f"{calls} calls",
                 f"{_kilo(tok_in)} in / {_kilo(tok_out)} out"]
        if scans:
            parts.append(f"{_usd_fine(spend / scans)}/scan")
        lines.append(" · ".join(parts))

    month = _days_ending_today(30)
    free = await _sum_stat(month, "scans_free")
    total = free + await _sum_stat(month, "scans_pro")
    if total:
        given = (await _spend(month)) * free / total
        lines.append(f"Free tier, 30 days: {free} of {total} scans ≈ {_usd(given)} given away")

    _, _, _, _, mrr = _subs_summary(await _read_index(SUBS_INDEX_KEY))
    lines.append("vs MRR ≈ " + (" + ".join(_money(v, c) for c, v in sorted(mrr.items()))
                                 if mrr else "n/a") + " (paid plans)")
    budget = f" · budget {_usd(GEMINI_DAILY_BUDGET_USD)}/day" if GEMINI_DAILY_BUDGET_USD > 0 else ""
    lines.append(f"Prices: ${GEMINI_PRICE_INPUT_PER_M:.2f}/M in · "
                 f"${GEMINI_PRICE_OUTPUT_PER_M:.2f}/M out{budget}")
    return "\n".join(lines)


# ── Social reach ─────────────────────────────────────────────────────────────

def _social_snapshot_key(day: str) -> str:
    return _stat_key(day, "social")


async def _remember_followers(accounts) -> None:
    """Today's follower counts, so tomorrow's digest can show the delta."""
    snapshot = {a.platform: a.followers for a in accounts if a.ok and a.followers is not None}
    if snapshot:
        try:
            await _cache.set(_social_snapshot_key(_day()), json.dumps(snapshot), STATS_TTL)
        except Exception as exc:
            log.debug("social snapshot failed: %s", type(exc).__name__)


async def _followers_delta(platform: str, now_count: int) -> str:
    try:
        yesterday = _day(datetime.now(timezone.utc) - timedelta(days=1))
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
        bits.append(f"{_kilo(post.views)} views")
    if post.likes is not None:
        bits.append(f"{_kilo(post.likes)} likes")
    if post.comments is not None:
        bits.append(f"{post.comments} comments")
    if post.shares:
        bits.append(f"{post.shares} shares")
    when = f" · {_date(post.created_at)[:6]}" if post.created_at else ""
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
        facts.append(f"{_kilo(account.total_likes)} likes")
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
    days = _days_ending_today(7, now)
    cats: dict[str, int] = {}
    brands: dict[str, int] = {}
    finds: list[dict] = []
    scans = 0
    for day in days:
        try:
            doc = json.loads(await _cache.get(_stat_key(day, "top")) or "{}")
        except Exception:
            doc = {}
        for c, n in (doc.get("cats") or {}).items():
            cats[c] = cats.get(c, 0) + int(n)
        for b, n in (doc.get("brands") or {}).items():
            brands[b] = brands.get(b, 0) + int(n)
        for f in doc.get("finds") or []:
            if isinstance(f, dict):
                finds.append({**f, "day": day})
        scans += await _read_stat(day, "scans_free") + await _read_stat(day, "scans_pro")
    finds.sort(key=lambda f: -float(f.get("hi") or 0))
    return {
        "days": len(days), "scans": scans,
        "cats": sorted(cats.items(), key=lambda kv: -kv[1])[:5],
        "brands": sorted(brands.items(), key=lambda kv: -kv[1])[:8],
        "finds": finds[:TOP_FINDS_CAP],
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
    days = list(reversed(_days_ending_today(TREND_DAYS, now)))       # oldest first
    counts: list[int] = []
    estimates: list[float] = []
    label = term
    for day in days:
        try:
            doc = json.loads(await _cache.get(_stat_key(day, "top")) or "{}")
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


# ── One device, for a support email ──────────────────────────────────────────

async def _user_text(argument: str) -> str:
    wanted = (argument or "").strip().lower()
    if not wanted:
        return "Usage: /user &lt;id&gt; — the id column from /users or /subs."
    users = await _read_index(USERS_INDEX_KEY)
    matches = [(who, e) for who, e in users.items() if who.lower().startswith(wanted)]
    if not matches:
        return f"👤 No device seen with an id starting <code>{html.escape(wanted)}</code>."
    if len(matches) > 1:
        return (f"👤 {len(matches)} devices start with <code>{html.escape(wanted)}</code> — "
                "give more characters: " + ", ".join(html.escape(w[:8]) for w, _ in matches[:6]))
    (who, e), = matches
    now = time.time()
    lines = [f"👤 <b>Device {html.escape(who[:8])}</b> — {'Pro' if e.get('tier') == 'pro' else 'free'}"]
    if e.get("first"):
        lines.append(f"First seen {_date(int(e['first']))}")
    if e.get("last"):
        ago = int(now - float(e["last"]))
        when = (f"{ago // 3600}h ago" if ago < 86400 else f"{ago // 86400}d ago")
        lines.append(f"Last seen {_date(int(e['last']))} ({when})")
    lines.append(f"Scans since the bot started watching: {int(e.get('scans', 0))}")
    subs = [s for s in (await _read_index(SUBS_INDEX_KEY)).values() if s.get("who") == who[:6]]
    for s in subs:
        alive = s.get("expires") is None or float(s["expires"]) > now
        renews = _date(int(s["expires"])) if s.get("expires") else "never"
        lines.append(f"Subscription: {_plan(s.get('product'))} · {s.get('acq') or '?'} · "
                     f"{'renews ' + renews if alive else 'ended ' + renews}")
    if not subs:
        lines.append("No subscription has synced from this device.")
    lines.append("Devices, not people — this is the audit log's pseudonym.")
    return "\n".join(lines)


# ── Checkup: every dependency on one screen ──────────────────────────────────

def _tls_days_left(host: str, timeout: float = 5.0) -> int | None:
    """Days until the served leaf certificate expires, or None if unreachable."""
    import socket
    import ssl
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert()
    not_after = cert.get("notAfter") if cert else None
    if not not_after:
        return None
    expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    return (expires - datetime.now(timezone.utc)).days


def _public_host() -> str:
    base = os.environ.get("SOCIAL_PUBLIC_BASE_URL", "https://api.snapworth.eu")
    return base.split("//", 1)[-1].split("/", 1)[0] or "api.snapworth.eu"


def _probe_reason(exc: Exception) -> str:
    """Why the probe failed, in the operator's words, so a rate limit is not
    mistaken for an outage and a truncated reply is not mistaken for either."""
    text = str(exc).lower()
    if "429" in text or "resource_exhausted" in text or "rate" in text and "limit" in text:
        return "rate limited (429) — retry in a minute; real scans retry on their own"
    if "empty text" in text or "max_tokens" in text:
        return "empty reply — the model spent its token allowance thinking"
    if "quota" in text or "credits" in text or "billing" in text:
        return "quota or billing — top up the provider account"
    if "api key" in text or "401" in text or "403" in text or "permission" in text:
        return "credentials refused — check GEMINI_API_KEY"
    return type(exc).__name__


async def _checkup_text() -> str:
    lines = ["🩺 <b>Checkup</b>"]

    # Cache: reachable, and how fast.
    t0 = time.monotonic()
    try:
        health = await _cache.health()
        ms = (time.monotonic() - t0) * 1000
        backend = html.escape(str(health.get("backend") or getattr(_cache, "backend", "cache")))
        state = "ok" if health.get("healthy", True) else "NOT answering"
        # "Degraded" only means something when Redis is configured; an
        # unconfigured cache is memory by design, not by failure.
        if health.get("configured") and health.get("degraded"):
            state += ", degraded"
        lines.append(f"Cache ({backend}): {state} · {ms:.0f} ms")
    except Exception as exc:
        lines.append(f"Cache: error ({html.escape(type(exc).__name__)})")

    # Model: a one-token round trip, billed like everything else.
    if _generator is None:
        lines.append("Gemini: not wired for the bot in this process")
    else:
        t0 = time.monotonic()
        try:
            text = await _generator(PROBE_PROMPT, PROBE_MAX_TOKENS, probe=True)
            ms = (time.monotonic() - t0) * 1000
            answered = '"ok"' in (text or "").lower()
            lines.append(f"Gemini: {'ok' if answered else 'answered oddly'} · {ms:.0f} ms")
        except Exception as exc:
            lines.append(f"Gemini: FAILED — {html.escape(_probe_reason(exc))} · a probe, "
                         "not counted against provider health")

    # What the process itself knows.
    info: dict = {}
    if _status_provider is not None:
        try:
            info = _status_provider() or {}
        except Exception as exc:
            log.warning("status provider failed: %s", type(exc).__name__)
    if info:
        model = "healthy" if info.get("model_healthy", True) else \
            f"degraded ({html.escape(str(info.get('model_failure_kind') or 'unknown'))})"
        lines.append(f"Provider health as seen by /scan: {model}")
        if "devicecheck" in info:
            lines.append(f"DeviceCheck: {'configured' if info['devicecheck'] else 'NOT configured — reinstalls get a fresh allowance'}")
        lines.append(f"Auth: {'enforcing' if info.get('auth_enforcing') else 'NOT enforcing'} · "
                     f"build <code>{html.escape(str(info.get('commit', '?')))}</code>")

    # TLS on the public host.
    host = _public_host()
    try:
        days = await asyncio.wait_for(asyncio.to_thread(_tls_days_left, host), 8)
        if days is None:
            lines.append(f"TLS {html.escape(host)}: certificate unreadable")
        else:
            flag = " ⚠️" if days < 14 else ""
            lines.append(f"TLS {html.escape(host)}: leaf expires in {days} days{flag} "
                         "(Let's Encrypt renews at 30; pinned intermediate to 2028-09-02)")
    except Exception as exc:
        lines.append(f"TLS {html.escape(host)}: unreachable ({html.escape(type(exc).__name__)})")

    # Poll lock: is it this replica answering?
    try:
        holder = await _cache.get(POLL_LOCK_KEY)
        lines.append("Telegram poller: this replica" if holder == _poll_token
                     else ("Telegram poller: another replica" if holder else "Telegram poller: nobody holds the lock"))
    except Exception:
        pass

    last = await _read_int(LAST_SCAN_KEY)
    if last:
        ago = int(time.time() - last)
        lines.append(f"Last successful scan: {ago // 60} min ago" if ago < 7200 else
                     f"Last successful scan: {ago // 3600}h ago")
    return "\n".join(lines)


# ── Anomalies: a quiet day, a spike ──────────────────────────────────────────

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
        if not await _cache.add(f"opsseen:quiet:{_day(now)}", "1", STATS_TTL):
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
    prior = [_day(when - timedelta(days=i)) for i in range(1, 8)]
    baseline = (await _sum_stat(prior, "scans_free") + await _sum_stat(prior, "scans_pro")) / 7
    if baseline <= 0 or scans < baseline * SPIKE_FACTOR:
        return ""
    return f"🔥 {scans / baseline:.1f}× the trailing week's daily average ({baseline:.1f}/day)"


# ── Clear: take back the last two days of the chat ──────────────────────────

async def _remember_message(message_id: int) -> None:
    try:
        now = int(time.time())
        raw = await _cache.get(MESSAGES_KEY)
        entries = [e for e in (json.loads(raw) if raw else [])
                   if isinstance(e, list) and len(e) == 2 and now - int(e[1]) < MESSAGES_TTL]
        entries.append([int(message_id), now])
        await _cache.set(MESSAGES_KEY, json.dumps(entries[-MESSAGES_CAP:]), MESSAGES_TTL)
    except Exception as exc:
        log.debug("message id note failed: %s", type(exc).__name__)


async def _clear_chat() -> None:
    """Delete every message the bot remembers in this chat, then post a fresh
    status so the keyboard is still there. Only the last 48 hours can go —
    Telegram's limit for bots, not ours — and only what was sent since this
    feature deployed, because ids before that were never recorded."""
    try:
        raw = await _cache.get(MESSAGES_KEY)
        ids = sorted({int(e[0]) for e in (json.loads(raw) if raw else [])
                      if isinstance(e, list) and e})
    except Exception:
        ids = []
    deleted = await _notifier.delete_messages(ids) if ids else 0
    try:
        await _cache.delete(MESSAGES_KEY)
    except Exception:
        pass
    note = (f"🧹 Cleared {deleted} messages." if deleted
            else "🧹 Nothing to clear — only the last 48 hours can be deleted, and only "
                 "what was sent since this button existed.")
    if deleted < len(ids):
        note += f" {len(ids) - deleted} were too old or already gone."
    await _notifier.send(note + "\n\n" + await _status_text(), await _buttons())


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
