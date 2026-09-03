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

And it listens: `/status`, `/digest`, `/week`, `/feed` from the operator's
chat, with inline buttons under every reply so nothing has to be typed.
Anyone else who finds the bot gets silence.

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
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import auditlog

log = logging.getLogger("snapworth.notify")

TELEGRAM_API = "https://api.telegram.org"

SEND_TIMEOUT_SECONDS = 10.0

# Counters live long enough for the weekly report to compare two full weeks,
# plus slack for a missed run; they are operational tallies, not records.
STATS_TTL = 60 * 60 * 24 * 16

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

COMMANDS: tuple[tuple[str, str], ...] = (
    ("status", "Active users, scans today, provider health"),
    ("feed", "Live scan feed: on, off, or show"),
    ("digest", "Yesterday's digest, now"),
    ("week", "Last 7 days against the 7 before"),
    ("help", "List commands"),
)

# Inline-keyboard rows: (label, callback data). The data is fed straight back
# through `handle_command` as "/<data>", so buttons and commands share one path.
Buttons = list[list[tuple[str, str]]]


class TelegramNotifier:
    """Thin sendMessage client over the shared httpx stack."""

    def __init__(self, bot_token: str, chat_id: str, client=None) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._client = client          # injectable for tests

    async def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS)
        return self._client

    async def send(self, text: str, buttons: Buttons | None = None) -> bool:
        """Deliver one message. Returns success; never raises.

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
            if buttons:
                payload["reply_markup"] = {"inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row]
                    for row in buttons]}
            resp = await client.post(
                f"{TELEGRAM_API}/bot{self._token}/sendMessage", json=payload)
            if resp.status_code != 200:
                log.warning("telegram send failed: HTTP %s", resp.status_code)
                return False
            return True
        except Exception as exc:
            log.warning("telegram send failed: %s", type(exc).__name__)
            return False

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
_tasks: set[asyncio.Task] = set()

# Supplies the live process facts /status reports (commit, cache backend,
# auth enforcement, model health). Injected by main so this module never
# imports it.
_status_provider: Callable[[], dict] | None = None

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
              status_provider: Callable[[], dict] | None = None) -> None:
    """Wire the notifier from the environment. Called once at startup.

    With the env vars unset this leaves everything disabled and every public
    function a no-op — the feature costs nothing until it is turned on.
    """
    global _notifier, _cache, _status_provider
    _cache = cache
    _status_provider = status_provider

    if notifier is not None:
        _notifier = notifier
    else:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not (token and chat_id):
            _notifier = None
            log.info("telegram alerts disabled — TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID unset")
            return
        _notifier = TelegramNotifier(token, chat_id)

    _start_digest()
    _start_command_loop()
    _spawn(_notifier.set_commands())
    log.info("telegram alerts enabled", extra={"digest_utc_hour": _digest_hour()})


async def aclose() -> None:
    """Tear down background work. Alerts in flight at shutdown are dropped."""
    global _notifier, _digest_task, _command_task
    if _digest_task is not None:
        _digest_task.cancel()
        _digest_task = None
    if _command_task is not None:
        _command_task.cancel()
        _command_task = None
    for task in list(_tasks):
        task.cancel()
    _tasks.clear()
    _alert_last_sent.clear()
    _alert_awaiting_recovery.clear()
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
            lines = [headline, f"{product} ({environment})"]
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

async def _announce_deploy(commit: str, cache_backend: str, auth_enforcing: bool) -> None:
    try:
        # One ping per commit, however many replicas boot it or however often
        # Railway restarts the container. A build that crash-loops has other
        # symptoms; a stream of identical "deployed" messages would only bury them.
        if not await _cache.add(f"opsseen:deploy:{commit}", "1", STATS_TTL):
            return
    except Exception as exc:
        log.debug("deploy ping guard failed, skipping: %s", type(exc).__name__)
        return
    auth = "enforcing" if auth_enforcing else "NOT enforcing"
    await _notifier.send(
        "🚀 <b>Backend deployed</b>\n"
        f"commit <code>{html.escape(commit)}</code> · "
        f"cache {html.escape(cache_backend)} · auth {auth}")


def deployed(commit: str, *, cache_backend: str, auth_enforcing: bool) -> None:
    """Announce that a new build is serving traffic.

    Answers "did the deploy land?" without a curl to /health, and doubles as a
    live check of the notifier itself on every release: if this message does
    not arrive, nothing else from this module will either.
    """
    if _notifier is None or _cache is None:
        return
    _spawn(_announce_deploy(commit, cache_backend, auth_enforcing))


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
    ]
    top = await _top_text(day)
    if top:
        lines.append(top)
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


async def _note_activity(subject: str) -> None:
    try:
        # The pseudonym, never the raw subject: these keys outlive the request
        # and the audit log already decided what identity is allowed to persist.
        who = auditlog.pseudonymise(subject)
        window = _window()
        if await _cache.add(f"opsseen:w:{window}:{who}", "1", 2 * ACTIVE_WINDOW_SECONDS):
            await _cache.incr(f"opsact:w:{window}", 2 * ACTIVE_WINDOW_SECONDS)
        day = _day()
        if await _cache.add(f"opsseen:d:{day}:{who}", "1", STATS_TTL):
            await _cache.incr(_stat_key(day, "active_users"), STATS_TTL)
    except Exception as exc:
        log.debug("activity note failed: %s", type(exc).__name__)


def saw_user(subject: str) -> None:
    """Count this device as active now and today. Fire-and-forget.

    Called on every authenticated request. Two cache writes per *new* device
    per window, both in the background — the request never waits.
    """
    if _notifier is None or _cache is None or not subject:
        return
    _spawn(_note_activity(subject))


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
            [(feed, "feed toggle")]]


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
    if command == "/status":
        return await _status_text(), await _buttons()
    if command == "/digest":
        return (await _digest_text(datetime.now(timezone.utc) - timedelta(days=1)),
                await _buttons())
    if command == "/week":
        return await _weekly_text(datetime.now(timezone.utc)), await _buttons()
    if command == "/feed":
        return await _feed_command(argument), await _buttons()
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


async def poll_once(offset: int | None) -> tuple[int | None, int]:
    """One getUpdates round. Returns (next offset, messages handled).

    Only the operator's chat is answered. Anyone else who finds the bot gets
    nothing back — not even an error — so there is nothing to probe.
    """
    handled = 0
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
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != _notifier.chat_id:
            continue
        if callback:
            await _notifier.answer_callback(str(callback.get("id", "")))
        reply = await handle_command_with_buttons(text)
        if reply:
            await _notifier.send(reply[0], reply[1])
            handled += 1
    return offset, handled


async def _command_loop() -> None:
    offset: int | None = None
    while True:
        try:
            if not await _hold_poll_lock():
                await asyncio.sleep(POLL_LOCK_TTL / 3)
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


async def _tally_top(day: str, category: str, brand: str | None) -> None:
    """Read-modify-write of the day's category and brand counts.

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
    cats[category] = int(cats.get(category, 0)) + 1
    if brand is not None and (brand in brands or len(brands) < TOP_BRANDS_CAP):
        brands[brand] = int(brands.get(brand, 0)) + 1
    await _cache.set(key, json.dumps({"cats": cats, "brands": brands}), STATS_TTL)


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
                     category: str, low: float, high: float, confidence: str) -> None:
    try:
        await _bump("scans_pro" if tier == "pro" else "scans_free")
        await _tally_top(_day(), _normalise_category(category), _clean_brand(brand))
        if await _feed_enabled():
            await _notifier.send(_feed_text(
                item_name=item_name, category=category, low=low, high=high,
                confidence=confidence, tier=tier))
    except Exception as exc:
        log.debug("scan feed failed: %s", type(exc).__name__)


def scan_completed(*, tier: str, item_name: str, brand: str | None, category: str,
                   low: float, high: float, confidence: str) -> None:
    """A scan produced a valuation. Counts it, tallies what it was, and — when
    the feed is on — tells the operator. Fire-and-forget; item and price only,
    never who scanned it and never the photo."""
    if _notifier is None or _cache is None:
        return
    _spawn(_note_scan(tier=tier, item_name=item_name, brand=brand, category=category,
                      low=low, high=high, confidence=confidence))


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

    start = end - timedelta(days=6)
    return "\n".join([
        f"📈 <b>Week {start.strftime('%d %b')} – {end.strftime('%d %b')}</b>",
        f"Scans: {scans_now} ({free_now} free · {pro_now} Pro) {_trend(scans_now, scans_prev)}",
        f"Failed: {failed_now} {_trend(failed_now, failed_prev)}",
        f"Active user-days: {users_now} {_trend(users_now, users_prev)}",
        f"New subscriptions: {subs_now} {_trend(subs_now, subs_prev)}",
        f"vs {scans_prev} scans · {users_prev} user-days · {subs_prev} subs the week before",
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
