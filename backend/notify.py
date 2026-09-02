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

The bot token is a credential. It appears in request URLs, so failures are
logged by exception class name only, and observability.py redacts the token
pattern as a backstop.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import auditlog

log = logging.getLogger("snapworth.notify")

TELEGRAM_API = "https://api.telegram.org"

SEND_TIMEOUT_SECONDS = 10.0

# Counters live long enough for the digest to read yesterday plus slack for a
# missed run; they are operational tallies, not records.
STATS_TTL = 60 * 60 * 24 * 3

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

    async def send(self, text: str) -> bool:
        """Deliver one message. Returns success; never raises.

        Failures log the exception *class* only: httpx error messages quote the
        request URL, and the URL carries the bot token.
        """
        try:
            client = await self._http()
            resp = await client.post(
                f"{TELEGRAM_API}/bot{self._token}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code != 200:
                log.warning("telegram send failed: HTTP %s", resp.status_code)
                return False
            return True
        except Exception as exc:
            log.warning("telegram send failed: %s", type(exc).__name__)
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
_tasks: set[asyncio.Task] = set()

# In-process alert throttling. Per-replica on purpose: an alert is about *this*
# process's view, and a duplicate from a second replica during an incident is
# an acceptable cost for not paying a cache round-trip on the failure path.
_alert_last_sent: dict[str, float] = {}
_alert_awaiting_recovery: set[str] = set()


def enabled() -> bool:
    return _notifier is not None


def configure(cache, notifier: TelegramNotifier | None = None) -> None:
    """Wire the notifier from the environment. Called once at startup.

    With the env vars unset this leaves everything disabled and every public
    function a no-op — the feature costs nothing until it is turned on.
    """
    global _notifier, _cache
    _cache = cache

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
    log.info("telegram alerts enabled", extra={"digest_utc_hour": _digest_hour()})


async def aclose() -> None:
    """Tear down background work. Alerts in flight at shutdown are dropped."""
    global _notifier, _digest_task
    if _digest_task is not None:
        _digest_task.cancel()
        _digest_task = None
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

    free = await _read_stat(day, "scans_free")
    pro = await _read_stat(day, "scans_pro")
    failed = await _read_stat(day, "scans_failed")
    subs = await _read_stat(day, "new_subs")

    text = (
        f"📊 <b>SnapWorth — {yesterday.strftime('%Y-%m-%d')}</b>\n"
        f"Scans: {free + pro} ok ({free} free · {pro} Pro) · {failed} failed\n"
        f"New subscriptions: {subs}"
    )
    return await _notifier.send(text)


async def _digest_loop() -> None:
    while True:
        await asyncio.sleep(
            _seconds_until_next(_digest_hour(), datetime.now(timezone.utc)))
        try:
            await send_digest()
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
