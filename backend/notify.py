"""Operator alerts to a private Telegram chat.

A single-operator service has no on-call rotation and no pager: production
telling *someone* what just happened means telling one phone. Telegram is the
cheapest reliable way to do that — the Bot API is free, needs no SDK, and a
message to a private chat is push-delivered.

Everything here is OFF unless both ``TELEGRAM_BOT_TOKEN`` and
``TELEGRAM_CHAT_ID`` are set — except the scan count and the category, brand
and finds tallies, which `/trends` serves to the app and which therefore run
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
import html
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import auditlog
import categories
import ideas
from confidence import brand_is_known

if TYPE_CHECKING:
    # For the annotation only. The value arrives through `configure`, from the
    # one `ScanQuota` main builds, so the bot asks the quota what the welcome
    # is instead of working it out again.
    from quota import WelcomeSetting

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

CATEGORY_EMOJI = {c.name: c.emoji for c in categories.CATEGORIES}

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
# What 🧹 Clear says when the list is empty. The list is in the cache, not the
# process, so it survives restarts — "since this process started" was wrong
# both ways. It is empty when nothing was tracked in 48 hours, or, for the
# prompt, when the read failed, which `_tracked_messages` cannot tell apart
# from that (`_clear_chat` reads `required` and refuses instead); and never
# right after a clear, whose own confirmation is tracked.
CLEAR_NOTHING_TRACKED = ("🧹 Nothing to clear — the bot has no record of a message "
                         "in this chat from the last 48 hours.")

# What 🧹 Clear removed, kept so it is not lost: the text of the bot's own
# messages (the operator's are one-word commands and are not worth keeping),
# shown back by /history. A month, a few hundred entries.
ARCHIVE_KEY = "opsstate:tgarchive"
ARCHIVE_CAP = 300
ARCHIVE_TTL = 30 * 24 * 3600
HISTORY_DEFAULT = 8
HISTORY_MAX = 25
HISTORY_SNIPPET_CHARS = 220
HISTORY_SNIPPET_LINES = 4
# Optionally, a second chat — a private channel with the bot as admin — that
# /clear forwards everything to before deleting, so the copy is a real
# Telegram copy, photos included. Off when unset.
ARCHIVE_CHAT_ENV = "TELEGRAM_ARCHIVE_CHAT_ID"
# Message ids in a private chat are sequential, so /clear also sweeps the gaps
# between the oldest and newest ids it knows — at most this many ids. Those are
# messages inside the span being cleared that the bot lost track of (the
# tracked list is an unlocked read-modify-write, so two sends at once can drop
# one). Telegram skips ids it cannot delete. It used to sweep 600 ids below the
# newest whatever the tracked span, reaching past it into messages the bot had
# no copy of and had never been asked to clear.
CLEAR_SWEEP_IDS = 600
# Upper bound on deleteMessages calls per /clear, however the batches split.
DELETE_MAX_CALLS = 60
FORWARD_MAX_CALLS = 60

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
    ("minbuild", "Tell app builds below a number to update, without a redeploy"),
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
# How long /user can say what the last purchase sync from a device came to.
# A subscriber's app re-syncs at every cold launch, so this is the horizon
# for a device that stopped opening the app.
SYNC_TTL = 60 * 60 * 24 * 90
# Devices remembered per subscription row, most recent last. Above the
# entitlement device cap (MAX_DEVICES_PER_SUBSCRIPTION, 6 by default) so a
# household's phones and the ones they replaced all still resolve.
SUB_DEVICES_CAP = 10

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
        # Why the last forward batch was refused, for /clear to show.
        self.last_forward_refusal: str | None = None
        # The id Telegram redirected the archive to, if the chat moved.
        self.last_forward_migrated_to: str | None = None
        self._client = client          # injectable for tests
        # Consecutive-identical-failure tracking, so an outage is logged with
        # decreasing frequency rather than every ~2s. See `_note_failure`.
        self._last_failure: str = ""
        self._failure_streak: int = 0
        # Told the message_id of every message this notifier sends, so /clear
        # can take them back. Set by `configure`; None is "don't bother".
        self.on_sent: Callable[[int, str], Awaitable[None]] | None = None

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
                    message_id = ((resp.json() or {}).get("result") or {}).get("message_id")
                    if isinstance(message_id, int):
                        await self.on_sent(message_id, text)
                except Exception:
                    pass
            return True
        except Exception as exc:
            self._note_failure("send", type(exc).__name__)
            return False

    @staticmethod
    def _description(resp) -> str:
        try:
            return str((resp.json() or {}).get("description") or "")[:200]
        except Exception:
            return ""

    @staticmethod
    def _migrate_to(resp) -> str | None:
        """The id Telegram hands back when a chat has moved.

        Promoting a bot to administrator turns a basic group into a supergroup,
        and the supergroup gets a different id. Telegram says so in the error's
        `parameters.migrate_to_chat_id` rather than making anyone derive it —
        so read it instead of guessing a -100 prefix."""
        try:
            new = ((resp.json() or {}).get("parameters") or {}).get("migrate_to_chat_id")
            return str(new) if new is not None else None
        except Exception:
            return None

    @property
    def chat_id(self) -> str:
        return self._chat_id

    def _note_failure(self, what: str, detail: str) -> None:
        """Log a transport failure without flooding.

        `SamplingFilter` never samples WARNING and above — deliberately, since
        a dropped error is an incident you cannot investigate — so the poll
        loop's one WARNING every ~2s during a Telegram outage or a revoked
        token became ~43k identical lines a day, burying the signal it was
        supposed to be. Consecutive identical failures are now logged on the
        1st, 2nd, 4th, 8th... occurrence, so an outage is still visible and
        still timestamped at both ends, at a fraction of the volume.
        """
        key = f"{what}:{detail}"
        if key == self._last_failure:
            self._failure_streak += 1
        else:
            self._last_failure, self._failure_streak = key, 1
        streak = self._failure_streak
        if streak & (streak - 1) == 0:            # 1, 2, 4, 8, 16, ...
            suffix = f" (x{streak})" if streak > 1 else ""
            log.warning("telegram %s failed: %s%s", what, detail, suffix)

    def _note_success(self, what: str) -> None:
        if self._failure_streak:
            log.info("telegram %s recovered after %d failures", what, self._failure_streak)
        self._last_failure, self._failure_streak = "", 0

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
                self._note_failure("poll", f"HTTP {resp.status_code}")
                return []
            body = resp.json()
            self._note_success("poll")
            return list(body.get("result") or []) if body.get("ok") else []
        except Exception as exc:
            self._note_failure("poll", type(exc).__name__)
            return []

    async def delete_messages(self, message_ids: list[int]) -> int:
        """Delete the bot's own (and, in a private chat, the operator's)
        messages, up to 100 per call. Returns how many ids Telegram accepted.
        Messages older than 48 hours cannot be deleted by any bot — that is
        Telegram's rule, and the operator clears those from the chat menu."""
        # Telegram answers a batch as a whole: one id it will not delete — a
        # message past the 48-hour limit, say — refuses the entire call, and
        # the sweep below the newest known id spans days of them. So a refused
        # batch is split in half and retried, down to single ids, which
        # isolates the undeletable ones at a cost of O(k log n) calls instead
        # of one call per id. A hard cap keeps a pathological chat from
        # turning /clear into hundreds of requests.
        deleted = 0
        calls = 0
        try:
            client = await self._http()
            pending = [message_ids[i:i + 100] for i in range(0, len(message_ids), 100)]
            while pending and calls < DELETE_MAX_CALLS:
                chunk = pending.pop()
                calls += 1
                resp = await client.post(
                    f"{TELEGRAM_API}/bot{self._token}/deleteMessages",
                    json={"chat_id": self._chat_id, "message_ids": chunk})
                if resp.status_code == 200 and (resp.json() or {}).get("ok"):
                    deleted += len(chunk)
                elif len(chunk) > 1:
                    half = len(chunk) // 2
                    pending += [chunk[:half], chunk[half:]]
                else:
                    log.debug("telegram deleteMessages refused id %s: %s",
                              chunk[0], self._description(resp))
        except Exception as exc:
            log.warning("telegram deleteMessages failed: %s", type(exc).__name__)
        return deleted

    async def forward_messages(self, to_chat_id: str, message_ids: list[int]) -> int:
        """Copy messages to another chat — the archive — before /clear deletes
        them here. Returns how many were forwarded.

        Telegram answers a forward batch as a whole, exactly as it does a
        delete batch: one id it will not forward — a service message ("X
        created the group"), a message already gone, one whose content is
        protected — refuses the entire call, and the tracked list mixes the
        bot's own messages with everything it saw the operator send. Sending
        all of them in one call therefore archives *nothing* the moment a
        single id is unforwardable, which is how a healthy chat reports
        "0 forwarded". So a refused batch is halved and retried down to single
        ids, isolating the unforwardable ones, under the same call cap as
        delete."""
        forwarded = 0
        calls = 0
        self.last_forward_refusal = None
        self.last_forward_migrated_to = None
        try:
            client = await self._http()
            # Strictly increasing ids are required by forwardMessages, and
            # halving a sorted list keeps every chunk sorted.
            ordered = sorted(set(message_ids))
            pending = [ordered[i:i + 100] for i in range(0, len(ordered), 100)]
            while pending and calls < FORWARD_MAX_CALLS:
                chunk = pending.pop()
                calls += 1
                resp = await client.post(
                    f"{TELEGRAM_API}/bot{self._token}/forwardMessages",
                    json={"chat_id": to_chat_id, "from_chat_id": self._chat_id,
                          "message_ids": chunk, "disable_notification": True})
                if resp.status_code == 200 and (resp.json() or {}).get("ok"):
                    forwarded += len((resp.json() or {}).get("result") or chunk)
                    continue

                # The chat moved: follow it once, put the batch back, and
                # remember the new id so /clear can name it. Archiving to a
                # chat that has merely been upgraded should not need a redeploy
                # to succeed — only to stop needing this hop.
                moved = self._migrate_to(resp)
                if moved and moved != to_chat_id and self.last_forward_migrated_to is None:
                    log.info("archive chat %s migrated to %s", to_chat_id, moved)
                    self.last_forward_migrated_to = moved
                    to_chat_id = moved
                    pending.append(chunk)
                    continue

                if len(chunk) > 1:
                    half = len(chunk) // 2
                    pending += [chunk[:half], chunk[half:]]
                else:
                    # The last one standing explains the whole batch: keep it
                    # for /clear to show, so "0 forwarded" is never mute.
                    self.last_forward_refusal = self._description(resp)
                    log.info("telegram forwardMessages refused id %s: %s",
                             chunk[0], self.last_forward_refusal)
        except Exception as exc:
            self.last_forward_refusal = type(exc).__name__
            log.warning("telegram forwardMessages failed: %s", type(exc).__name__)
        return forwarded

    async def get_chat(self, chat_id: str) -> dict | None:
        """What Telegram knows about a chat id — title and type — or None if
        the bot cannot see it. Used to verify the archive chat."""
        try:
            client = await self._http()
            resp = await client.get(f"{TELEGRAM_API}/bot{self._token}/getChat",
                                    params={"chat_id": chat_id})
            if resp.status_code != 200:
                return None
            return (resp.json() or {}).get("result") or None
        except Exception as exc:
            log.debug("telegram getChat failed: %s", type(exc).__name__)
            return None

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

    async def clear_default_commands(self) -> bool:
        """Withdraw any command menu published at the default scope.

        A chat-scoped list does not replace a default-scoped one, so publishing
        the operator menu to the right chat is not enough on its own — what is
        already live has to be taken down.
        """
        try:
            client = await self._http()
            resp = await client.post(
                f"{TELEGRAM_API}/bot{self._token}/deleteMyCommands",
                json={"scope": {"type": "default"}})
            return resp.status_code == 200
        except Exception as exc:
            log.warning("telegram deleteMyCommands failed: %s",
                        type(exc).__name__)
            return False

    async def set_commands(self, commands=COMMANDS) -> bool:
        """Publish the command menu Telegram shows behind the "/" button.

        Scoped to the operator's chat. Omitting `scope` defaults it to
        `BotCommandScopeDefault`, which covers every private chat, group and
        supergroup — so all 23 entries were what any Telegram user saw behind
        the Menu button on opening the bot, descriptions included: "lever —
        Arm or disarm the free-scan allowance without a redeploy", "subs —
        Every subscription seen: plan, how obtained, renews", "costs — Gemini
        spend: today, 7 and 30 days, per scan, vs MRR".

        No access leaked — the chat gate drops every update from another
        chat — but the shape of the operation did, along with an invitation to
        try. A published menu is documentation.
        """
        try:
            client = await self._http()
            resp = await client.post(
                f"{TELEGRAM_API}/bot{self._token}/setMyCommands",
                json={"commands": [{"command": c, "description": d}
                                   for c, d in commands],
                      "scope": {"type": "chat", "chat_id": self._chat_id}})
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


def configure(cache, notifier: TelegramNotifier | None = None,
              status_provider: Callable[[], dict] | None = None,
              social=None, generator: Callable[..., Awaitable[str]] | None = None,
              scanner: Callable[..., Awaitable[dict]] | None = None,
              device_check_probe: Callable[[], Awaitable[tuple[bool | None, str]]] | None = None,
              welcome: Callable[[], Awaitable[WelcomeSetting]] | None = None) -> None:
    """Wire the notifier from the environment. Called once at startup.

    With the env vars unset this leaves everything disabled and every public
    function a no-op — the feature costs nothing until it is turned on — apart
    from `count_scan` and `scan_completed`'s tallies, which `/trends` reads.
    """
    global _notifier, _cache, _status_provider, _social, _generator, _scanner
    global _device_check_probe, _describe_welcome
    _cache = cache
    _status_provider = status_provider
    _social = social
    _generator = generator
    _scanner = scanner
    _device_check_probe = device_check_probe
    _describe_welcome = welcome

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
    # Take the default-scope menu down before publishing the chat-scoped one:
    # a chat scope does not replace a default scope, so without this the list
    # already live stays live for everyone.
    _spawn(_notifier.clear_default_commands())
    _spawn(_notifier.set_commands())
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


def _spawn(coro) -> asyncio.Task | None:
    """Run `coro` in the background, holding a reference until it finishes.

    Without the reference set, an un-awaited task is garbage-collectable
    mid-flight. Outside a running loop (sync tests, tooling) the coroutine is
    closed unrun rather than raising, and None is returned.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return None
    task = loop.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


# ── Daily counters ───────────────────────────────────────────────────────────

def _date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%d %b %Y")


def _short_date(epoch: int) -> str:
    """`12Sep26` — seven characters, and it keeps the year.

    The tables used `_date(...)[:6]`. `%d` is zero-padded and `%b` is three
    letters, so that slice is always exactly `DD Mon` and always discards the
    year — there is no input for which it keeps any part of it. `renews`
    survived by luck, because a live subscription renews within twelve months,
    but `since` is `original_purchase_at` with no lower bound and `seen` is
    bounded only by the 400-day index TTL. Both could be more than a year old
    and printed identically to today, which is how a 2027 renewal read as
    "23 Jul".
    """
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%d%b%y")


def _renewal_phrase(expires_at: int, auto_renew: bool | None) -> str:
    """The expiry line: `renews or expires 12 Mar 2027`, or just `expires`.

    The hedge is not sloppiness — for most of this bot's life it was the honest
    answer. A signed transaction carries `expiresDate` and nothing about
    whether the period after it is coming, so the date genuinely meant one of
    two things and the line said so.

    Now that `signedRenewalInfo` is read (`appstorenotify._auto_renew_status`),
    the hedge is only correct where the fact is still unknown. Kept for exactly
    that case: rows written before this existed, and every `/auth/entitlement`
    sync, which sees a transaction and no renewal info. Only a definite False
    narrows the wording — an unknown auto-renew must not be reported as a
    cancellation, which would turn "we did not ask" into "they are leaving".
    """
    if auto_renew is False:
        return f"expires {_date(expires_at)}"
    return f"renews or expires {_date(expires_at)}"


def _sub_is_alive(entry: dict, now: float) -> bool:
    """Whether a subscription row is currently entitled.

    One function, because there were three copies and one of them had drifted:
    `/user` tested expiry alone, so a refunded subscription — which keeps its
    expiry date, the period having been paid for and then unpaid — read as
    "renews 12 Mar 2027" there while `/subs` showed the same row as `refund`
    and `/subs`'s summary excluded it from active revenue.
    """
    if entry.get("revoked") is not None:
        return False
    expires = entry.get("expires")
    return expires is None or float(expires) > now


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
    """Tally one successful scan. Fire-and-forget.

    Runs whenever there is a cache, Telegram or not: `/trends` reports this
    count to users ("N scans this week"), and a feature in the app must not
    depend on whether the operator's bot is configured."""
    if _cache is None:
        return
    _spawn(_bump("scans_pro" if tier == "pro" else "scans_free"))


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
    _spawn(_bump("limit_hits"))


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
    _spawn(_bump("scans_failed"))
    _spawn(_bump(f"scans_failed_{kind if kind in SCAN_FAILURE_LABELS else 'other'}"))


async def _failure_breakdown(day: str) -> str:
    """"2 no price · 1 provider", commonest first; "" when nothing is tagged.

    Days recorded before the per-kind counters existed have a total but no
    parts, and read correctly as a plain "3 failed" rather than a wrong zero."""
    counts = []
    for kind, label in SCAN_FAILURE_LABELS.items():
        found = await _read_stat(day, f"scans_failed_{kind}")
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
    _spawn(_bump("scans_blocked"))
    if paused:
        _spawn(_announce_safety_pause(auditlog.pseudonymise(subject), count))


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
                         INDEX_TTL)
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
    """
    if _notifier is None or _cache is None:
        return
    try:
        ent = note.entitlement
        otid = ent.original_transaction_id
        if not otid or not note.is_indexed:
            return
        if _is_bounded(ent):
            # `/apple/notifications` refuses Sandbox and the Sandbox route
            # never calls this, so nothing should reach here. If something
            # does, it is a tester's renewal and not money.
            return

        before = await _index_subscription(None, ent, note.auto_renew,
                                           current=note.is_refund_reversal)
        # None: the index could not be read, so there is no previous row to
        # judge a paid period against. It is then neither a conversion nor a
        # new payer — an ordinary renewal would otherwise be announced, and
        # counted, as "New paying subscriber".
        known = before is not None
        before = before or {}
        was = str(before.get("acq") or "") if before else ""
        now_acq = _acquisition(ent)

        product = html.escape(ent.product_id or "unknown product")
        environment = html.escape(ent.environment)
        detail = f"{product} ({environment})"
        price = getattr(ent, "price", None)
        if isinstance(price, (int, float)) and price > 0:
            detail += f" · {_money(price, getattr(ent, 'currency', None))}"

        lines: list[str] | None = None

        if note.is_paid_period and was and was != "paid":
            # The headline event. We knew this subscription as a trial or a
            # comp; Apple has just charged for it. `was` is what makes this a
            # conversion rather than an ordinary renewal — the notification
            # itself cannot tell those apart, because they are identical.
            label = "Trial converted" if was == "trial" else f"{was.capitalize()} converted"
            lines = [f"🎉 <b>{label} — this is real money</b>", detail]
            await _count_new_subscription(otid)
        elif note.is_paid_period and known and not before:
            # A payer no device ever synced. Before Apple told us directly,
            # this subscription did not exist as far as the bot was concerned.
            lines = ["🎉 <b>New paying subscriber</b> (Apple reported it first)", detail]
            await _count_new_subscription(otid)
        elif note.is_paid_period and not known:
            # Money, with nothing to say which kind. Staying silent lost the
            # conversion alert for good: the device's next sync rewrites the
            # row as paid without a word, and the subscription was already
            # seen as a trial. Not counted, because a renewal must not be.
            lines = ["💵 <b>Paid period</b> (subscription index unreadable: "
                     "a renewal, a conversion or a new payer)", detail,
                     "Not counted in today's new subscribers."]
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
            when = f" · runs until {_date(ent.expires_at)}" if ent.expires_at else ""
            lines = ["⚠️ <b>Auto-renew turned off</b>", f"{detail}{when}"]
        elif note.is_billing_failure:
            lines = ["💳 <b>Renewal payment failed</b>",
                     f"{detail} · Apple is retrying"]

        if lines is None:
            return
        if ent.expires_at and not note.is_loss:
            # `note.auto_renew` is what this notification's own renewal info
            # says; `before` is what we last knew. Preferring the notification
            # matters on exactly the alert where it changed — a
            # DID_CHANGE_RENEWAL_STATUS whose headline is already "auto-renew
            # turned off" must not be followed by a line saying it renews.
            auto_renew = (note.auto_renew if note.auto_renew is not None
                          else before.get("auto_renew"))
            lines.append(_renewal_phrase(ent.expires_at, auto_renew))
        if not await _notifier.send("\n".join(lines), _SUBS_BUTTONS):
            log.warning("subscription notification alert failed to send")
    except Exception:
        # An operator ping must never fail Apple's delivery: a non-2xx makes
        # Apple retry the same notification for hours.
        log.exception("subscription notification handling failed")


async def _count_new_subscription(otid: str) -> None:
    """Count one new paying subscription, once, whichever half found out first.

    There are two ways a subscription first becomes known: the client posts its
    signed transaction to `/auth/entitlement`, or Apple posts a notification to
    `/apple/notifications`. Only the first incremented `new_subs`, so a payer
    whose device never synced before Apple told us — the case
    `subscription_event` alerts on by name, "New paying subscriber (Apple
    reported it first)" — never appeared in the number the operator reads as
    "how many people paid me today". Trial conversions arriving by notification
    were missing from it too, which is the one figure the whole trial
    experiment is judged on.

    `opsseen:subcount:{otid}` is the guard, and it is deliberately the *same*
    key both callers use: whichever path learns of a subscription first counts
    it, and the other finds the key already set and counts nothing. So the fix
    cannot double-count the common case where Apple reports a conversion and
    the client syncs the same transaction minutes later.

    Never raises. A counter is not worth failing an alert or a scan over.
    """
    if not otid or _cache is None:
        return
    try:
        if await _cache.add(f"opsseen:subcount:{otid}", "1", SUB_SEEN_TTL):
            await _cache.incr(_stat_key(_day(), "new_subs"), STATS_TTL)
    except Exception as exc:                      # pragma: no cover - defensive
        log.warning("new_subs counter failed for %s: %s", otid, exc)


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
    _spawn(_note_sync(subject, "pro" if ent.tier == "pro" else "free"))
    if _is_bounded(ent):
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
            before = await _index_subscription(subject, ent) or {}
            if not await _cache.add(f"opsseen:sub:{otid}", "1", SUB_SEEN_TTL):
                return
            purchased = getattr(ent, "original_purchase_at", None)
            # Unknown purchase date reads as new: Apple always supplies it, so
            # its absence is a test fixture, not a customer.
            is_new = (purchased is None
                      or time.time() - purchased < NEW_SUBSCRIPTION_WINDOW_SECONDS)
            if is_new:
                # A second key, and this one is never handed back.
                #
                # The guard above gates both the alert and this counter, and
                # the alert's failure path releases it — so a Telegram outage
                # left the increment on the counter and let the next
                # `/auth/entitlement` for the same transaction add another.
                # That path is not rare: the client calls it at cold launch,
                # purchase, restore and every `Transaction.updates`, so
                # re-entry inside the 24-hour `is_new` window is the normal
                # case. One sale could be counted several times, in the figure
                # the operator reads as "how many people paid me today".
                await _count_new_subscription(otid)
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
                lines.append(_renewal_phrase(
                    ent.expires_at, before.get("auto_renew")))
            # The guard above was consumed *before* this send and its result
            # was discarded, so a Telegram failure burned a 400-day marker
            # and the alert for that sale was never seen. `_announce_deploy`
            # already hands its guard back on failure; this now does too.
            #
            # The sale itself was never lost — `_index_subscription` has
            # already run and `/subs` lists them — but the one push that says
            # "someone just paid you" was, silently.
            if not await _notifier.send("\n".join(lines), _SUBS_BUTTONS):
                log.warning("subscription alert failed to send, releasing the guard")
                try:
                    await _cache.delete(f"opsseen:sub:{otid}")
                except Exception:
                    pass
        else:
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
    _spawn(_note_sync(subject, "rejected", reason))


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

def _alert(key: str, text: str, buttons: Buttons | None = None) -> None:
    if _notifier is None:
        return
    now = time.monotonic()
    last = _alert_last_sent.get(key)
    if last is not None and now - last < ALERT_MIN_INTERVAL_SECONDS:
        return
    _alert_last_sent[key] = now
    _alert_awaiting_recovery.add(key)
    # Buttons are passed in rather than built here: these two are sync and hand
    # the send to `_spawn`, so anything awaited would have to move inside the
    # coroutine. Every keyboard an alert wants is static, so there is nothing
    # to await.
    _spawn(_notifier.send(text, buttons))


def _recovered(key: str, text: str, buttons: Buttons | None = None) -> None:
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
    why = await _failure_breakdown(day) if failed else ""
    blocked = await _read_stat(day, "scans_blocked")
    subs = await _read_stat(day, "new_subs")
    users = await _read_stat(day, "active_users")
    limits = await _read_stat(day, "limit_hits")
    lines = [
        f"📊 <b>SnapWorth — {when.strftime('%Y-%m-%d')}</b>",
        f"Active users: {users}",
        f"Scans: {free + pro} ok ({free} free · {pro} Pro) · {failed} failed"
        + (f" ({why})" if why else "")
        + (f" · {blocked} blocked by the safety filter" if blocked else ""),
        # The line the free-scan experiment is read on. Against new_subs it is
        # the first server-side answer to "does hitting the limit move anyone".
        # Omitted entirely on a day with none, so a quiet day stays quiet.
        *([f"Free limit reached: {limits}"
           + (f" · {subs} subscribed" if subs else " · nobody subscribed")]
          if limits else []),
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
    why = await _failure_breakdown(day) if failed else ""
    blocked = await _read_stat(day, "scans_blocked")
    subs = await _read_stat(day, "new_subs")
    limits = await _read_stat(day, "limit_hits")

    lines = [
        "📡 <b>SnapWorth status</b>",
        f"Active users: {active_now} since {_window_start(window):%H:%M} UTC "
        f"· {active_today} today",
        f"Scans today: {free + pro} ok ({free} free · {pro} Pro) · {failed} failed"
        + (f" ({why})" if why else "")
        + (f" · {blocked} blocked" if blocked else ""),
        *([f"Free limit reached: {limits} today"] if limits else []),
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


_HEALTH_BUTTONS: Buttons = [[("\U0001FA7A Checkup", "checkup"),
                             ("\U0001F4B8 Costs", "costs")]]
_SUBS_BUTTONS: Buttons = [[("\U0001F4B3 Subs", "subs"),
                           ("\U0001F465 Users", "users")]]
_COSTS_BUTTONS: Buttons = [[("\U0001F4B8 Costs", "costs")]]
_DEPLOY_BUTTONS: Buttons = [[("\U0001F4E1 Status", "status"),
                             ("\U0001FA7A Checkup", "checkup")]]
_FEED_BUTTONS: Buttons = [[("\U0001F3C6 Finds", "finds"),
                           ("\U0001F515 Feed off", "feed off")]]


async def _buttons() -> Buttons:
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
    if command == "/sub":
        return await _sub_command(rest)
    if command == "/users":
        return await _users_text(), await _buttons()
    if command == "/costs":
        return await _costs_text(), await _buttons()
    if command == "/experiment":
        if argument == "export":
            return await _experiment_export(), [[("🧪 Experiment", "experiment")]]
        current = (await _levers()).get("free_scans_first_day")
        return (await _experiment_text(),
                _lever_buttons(current) + [[("💾 Export CSV", "experiment export")]]
                + await _buttons())
    if command == "/lever":
        return await _lever_command(argument, rest)
    if command == "/minbuild":
        return await _minbuild_command(argument, rest)
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

def _normalise_category(category: str | None) -> str:
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


def _clean_brand(brand: str | None) -> str | None:
    """A brand worth tallying, or None. Model output: trimmed, bounded, and
    stripped of links and handles."""
    value = _without_links(brand)[:40]
    if not brand_is_known(value):
        return None
    return value


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
# keep its own copy of each rule, and every copy was wrong once.

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


async def free_scan_lever() -> int | None:
    """The operator's welcome allowance, or None to use the environment.

    Injected into `ScanQuota` from main.py — quota must not import this module.
    Raises nothing: `_levers` swallows, and a missing key reads as None.
    """
    value = (await _levers()).get("free_scans_first_day")
    return int(value) if isinstance(value, (int, float)) else None


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
    changes.append([_day(), before, value])
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


async def _lever_command(argument: str, rest: str) -> tuple[str, Buttons]:
    """`/lever`, `/lever arm [n]`, `/lever disarm`, and their confirmations.

    Two taps, never one. The first names what is about to change and what it
    currently is; the second does it. A single-tap lever on a phone, in a chat
    that also contains the word "Disarm" one row away, is how an experiment
    gets restarted by accident halfway through.
    """
    parts = (rest or "").split()
    action = parts[0].lower() if parts else ""
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


def _lever_buttons(current: int | None) -> Buttons:
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
# refuses a build below it with `UPDATE_REQUIRED_DETAIL`. Only /scan and
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


async def _minbuild_command(argument: str, rest: str) -> tuple[str, Buttons]:
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
                    f"will see \"Something went wrong\". Sign-in and purchases "
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
    emoji = CATEGORY_EMOJI[_normalise_category(category)]
    name = html.escape(" ".join((item_name or "").split())[:80] or "Unidentified item")
    band = html.escape((confidence or "").strip().lower() or "unknown")
    who = "Pro" if tier == "pro" else "free"
    return (f"{emoji} <b>{name}</b>\n"
            f"{_normalise_category(category)} · ${low:,.0f}–{high:,.0f} · "
            f"{band} confidence · {who}")


async def _tally_top(day: str, category: str, brand: str | None,
                     find: dict | None = None, device: str | None = None) -> None:
    """Read-modify-write of the day's category and brand counts, and its
    handful of most valuable finds.

    One small JSON document rather than a key per brand, because the cache
    interface cannot enumerate keys and the report needs the whole table.
    A lost update between two replicas costs one count, which is fine for a
    tally that exists to say "clothing 5 · Nike ×3". A lost *read* is not:
    see `_read_index_for_update`, which this shares a document shape with.
    Losing this scan's count is the price of not resetting the day's.

    `device` is `_trend_device`'s keyed tag for whoever scanned it, recorded
    beside each category, brand and find (at most TRENDS_DEVICES_KEPT per
    entry) so `/trends` can count devices rather than scans — see
    `TRENDS_MIN_CATEGORY_DEVICES`. It stays as long as the document, STATS_TTL.

    The write that gives a day its device maps also offers that day to
    TRENDS_TAGGED_SINCE_KEY, which keeps the first: see `_tagged_since`.
    """
    key = _stat_key(day, "top")
    doc = await _read_index_for_update(key)
    if doc is None:
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
                     STATS_TTL)
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
    long as the day document (STATS_TTL, 35 days), and used for nothing but
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
    is stripped of links and handles, as `_clean_brand` does.

    Not quite all that is stored: `_tally_top` adds `d`, the keyed tags of the
    devices behind the item (see `_trend_device`), so a find can be held back
    until TRENDS_MIN_FIND_DEVICES have scanned it."""
    return {"n": _without_links(item_name)[:60] or "Unidentified item",
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
                     subject: str | None = None, elapsed_ms: int | None = None,
                     reread: bool = False) -> None:
    try:
        # The count and the tallies feed `/trends` as well as the bot, so they
        # run with or without Telegram. Everything after them is the
        # operator's alone.
        await _bump("scans_pro" if tier == "pro" else "scans_free")
        if not reread:
            await _tally_top(_day(), _normalise_category(category), _clean_brand(brand),
                             _find_record(item_name=item_name, brand=brand, category=category,
                                          low=low, high=high, tier=tier),
                             _trend_device(subject))
        if _notifier is None:
            return
        await _cache.set(LAST_SCAN_KEY, str(int(time.time())), STATS_TTL)
        if elapsed_ms:
            await _cache.incr(_stat_key(_day(), "scan_ms"), STATS_TTL, int(elapsed_ms))
        if subject:
            await _index_user(auditlog.pseudonymise(subject), tier=tier, scanned=True)
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

    What the tallies keep is the item, its price, and `_trend_device`'s keyed
    tag for the device, for STATS_TTL, only so `/trends` can count distinct
    devices. The tag joins to nothing else the cache holds without AUDIT_SALT.
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


async def _read_index_for_update(key: str) -> dict | None:
    """`_read_index` for a caller about to write the whole document back.

    None means the store could not be read, and the caller must not write.
    `_read_index` answers {} for that, which is right for a report and wrong
    here: a plain `get` on a failing Redis falls back to memory and returns
    None rather than raising, so the writer took the document as empty and,
    once Redis answered again, overwrote it with the one row it had just
    added. A 300-row subscription index became 1, and the auto-renew state
    and history in those rows came back from nowhere. `required=True` makes
    that read raise instead. A document that is present but unreadable is
    still replaced, as before.
    """
    try:
        raw = await _cache.get(key, required=True)
    except Exception as exc:
        log.warning("index %s unreadable, not rewriting it: %s", key, type(exc).__name__)
        return None
    try:
        doc = json.loads(raw or "{}")
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


async def _write_index(key: str, doc: dict, cap: int, recency: str) -> None:
    # A row goes once it has been untouched for INDEX_TTL, which is the "up to
    # 400 days" the privacy policy states. The document's own TTL cannot do
    # that: every write renews it, so on a service that is used daily a row
    # written once would otherwise stay until the cap pushed it out.
    cutoff = time.time() - INDEX_TTL
    for stale in [k for k, v in doc.items()
                  if not isinstance(v, dict)
                  or not isinstance(v.get(recency), (int, float))
                  or v[recency] < cutoff]:
        doc.pop(stale, None)
    if len(doc) > cap:
        # Drop the least recently seen until it fits.
        for stale in sorted(doc, key=lambda k: doc[k].get(recency, 0))[:len(doc) - cap]:
            doc.pop(stale, None)
    await _cache.set(key, json.dumps(doc, separators=(",", ":")), INDEX_TTL)


def _same_device(recorded: str, wanted: str) -> bool:
    """Whether an id someone typed names this recorded device.

    Both are prefixes of one sixteen-character pseudonym, so either may be
    the longer: the operator types six characters from /subs, or pastes all
    sixteen from a support mail — and rows written before the full pseudonym
    was stored hold only six."""
    recorded, wanted = recorded.lower(), wanted.lower()
    return bool(recorded and wanted) and (recorded.startswith(wanted)
                                          or wanted.startswith(recorded))


def _with_device(devices: list, who: str) -> list[str]:
    """`devices` with `who` moved to the end (most recent), capped.

    A six-character id kept from an older row is the same device as the full
    pseudonym it begins, and is dropped in its favour."""
    kept = [d for d in devices
            if isinstance(d, str) and d and d != who and not who.startswith(d)]
    return [*kept, who][-SUB_DEVICES_CAP:]


def _row_devices(row: dict) -> list[str]:
    """Every device id a subscription row knows, legacy `who` included."""
    found = [d for d in (row.get("devices") or []) if isinstance(d, str) and d]
    who = row.get("who")
    if isinstance(who, str) and who and who not in found:
        found.append(who)
    return found


def _device_argument(argument: str | None) -> str:
    """What the operator typed, as an id: trimmed, lower-cased, and without
    the "Device" the in-app support form writes in front of it."""
    parts = (argument or "").strip().lower().split()
    if len(parts) == 2 and parts[0] == "device":
        parts = parts[1:]
    return " ".join(parts)


def _is_bounded(ent) -> bool:
    """A Sandbox entitlement production honours on bounded terms: not a customer.

    See `entitlements.SANDBOX_ENTITLEMENTS`. Imported here rather than at the
    top for the same cycle `_sub_text` describes.
    """
    import entitlements
    return entitlements.is_bounded(ent)


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


#: `_acquisition`'s words, shortened to fit a table column.
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


async def _index_subscription(subject: str | None, ent,
                              auto_renew: bool | None = None, *,
                              current: bool = False) -> dict | None:
    """Record what we now know about one subscription. Returns the previous row,
    or None when the index could not be read and nothing was written.

    `subject` is None when App Store Server Notifications told us rather than a
    device checking in. There is no pseudonymised device to attribute it to,
    and — this is the point — the existing `who` must survive: the row may
    already name the device that first synced it, and overwriting that with
    nothing would lose the only link between a payment and a person.

    `auto_renew` is the same shape of argument and for the same reason. It
    lives in Apple's `signedRenewalInfo`, which only two of the three writers
    ever see: a notification carries one, a live status lookup fetches one, and
    `/auth/entitlement` — the highest-volume writer by far — does not, because
    the client presents a signed *transaction* and nothing else. None means
    "this writer cannot see it", so the stored value survives. Without that,
    every app launch would erase a cancellation the moment Apple reported it.

    The previous row is returned because a notification alone cannot say
    whether a paid period is a *conversion*. Only the row it replaces can.

    `current` says `ent` is Apple's word on this term as of now — a live
    status lookup, or a REFUND_REVERSED — rather than a transaction that may
    have been signed before a refund and delivered after it. See the refund
    mark below.

    A bounded Sandbox entitlement is never written, whoever calls. This is the
    one writer every path shares, so the rule lives here as well as at each
    caller: `/subs`, MRR and the digest's subscriber line all read this index,
    and a tester in it is revenue that does not exist.
    """
    if _is_bounded(ent):
        return {}
    doc = await _read_index_for_update(SUBS_INDEX_KEY)
    if doc is None:
        # Nothing written, and nothing known about the row. `/sub` can repair
        # it once Redis is back.
        return None
    otid = str(ent.original_transaction_id)
    before: dict = row if isinstance(row := doc.get(otid), dict) else {}
    entry: dict = dict(before)
    entry.update({
        "product": ent.product_id, "env": ent.environment,
        "first": getattr(ent, "original_purchase_at", None),
        "expires": ent.expires_at,
        "acq": _acquisition(ent),
        "price": getattr(ent, "price", None), "currency": getattr(ent, "currency", None),
        "seen": int(time.time()),
    })
    if subject is not None:
        # The full pseudonym, and every device that has synced this
        # subscription, not just the last. `who` used to be the first six
        # characters of whichever device synced most recently: a support mail
        # carries all sixteen ("Device 3f2a…" from the in-app form, the
        # `support_id` /auth/token hands the app), and a family's second phone
        # was overwritten by the first on every launch — so /sub refused the
        # id the customer sent and /user told every other device that nothing
        # had ever synced from it.
        who = auditlog.pseudonymise(subject)
        entry["devices"] = _with_device(_row_devices(entry), who)
        entry["who"] = who
    if auto_renew is not None:
        entry["auto_renew"] = auto_renew
    # The revocation is a tombstone on a *term*, not on the row.
    #
    # This only ever set `revoked` and never cleared it, and the row is keyed
    # on `originalTransactionId` — which Apple keeps stable across renewals
    # *and* re-subscriptions. So one refund tombstoned the row permanently: a
    # customer who refunded in March and paid again in June stayed out of the
    # active count, out of the paid count and out of MRR for the 400-day life
    # of the index, while `/subs` showed their live subscription as `refund`.
    #
    # `entitlements._is_revoked` had to solve exactly this on the access path
    # and stores the revoked term's own expiry so a later, longer-dated term
    # survives the tombstone. The operator's index gets the same rule, rather
    # than clearing on any non-revoked transaction — Apple can redeliver a
    # pre-refund renewal after the REFUND, and that must not resurrect the row.
    #
    # That rule alone left no way to clear the mark on the *same* term, which
    # is exactly what a reversed refund needs: the row said `refund` for a
    # customer paying for that term again, and `/sub` asking Apple could not
    # fix it. A `current` transaction is not a redelivery, so it may.
    revoked = getattr(ent, "revoked_at", None)
    if revoked is not None:
        entry["revoked"] = revoked
        entry["revoked_expires"] = ent.expires_at
    else:
        tombstoned = entry.get("revoked_expires")
        if (entry.get("revoked") is not None
                and ent.expires_at is not None
                and tombstoned is not None
                and (float(ent.expires_at) >= float(tombstoned) if current
                     else float(ent.expires_at) > float(tombstoned))):
            entry.pop("revoked", None)
            entry.pop("revoked_expires", None)
    doc[otid] = entry
    await _write_index(SUBS_INDEX_KEY, doc, SUBS_INDEX_CAP, "seen")
    return before


async def _index_user(who: str, *, tier: str, scanned: bool = False) -> None:
    doc = await _read_index_for_update(USERS_INDEX_KEY)
    if doc is None:
        return
    now = int(time.time())
    entry: dict = row if isinstance(row := doc.get(who), dict) else {"first": now, "scans": 0}
    entry["last"] = now
    entry["tier"] = "pro" if tier == "pro" else "free"
    if scanned:
        entry["scans"] = int(entry.get("scans", 0)) + 1
    doc[who] = entry
    await _write_index(USERS_INDEX_KEY, doc, USERS_INDEX_CAP, "last")


def _sync_key(who: str) -> str:
    return f"opsstate:sync:{who}"


async def _note_sync(subject: str, outcome: str, detail: str | None = None) -> None:
    """Record what `/auth/entitlement` made of this device's last signed
    transaction: "pro", "free" (verified, not entitled) or "rejected" with
    the reason. Never raises.

    The subscription index only ever hears about a transaction that
    verified, so a refused one left no trace the operator could find: the
    customer who paid and was told free had, as far as the bot knew, never
    tried. `/user` shows this line. A key per device rather than a field in
    the devices index, so it is one plain write that cannot lose, or be
    lost to, the index's read-modify-write."""
    if _cache is None:
        return
    try:
        record = [int(time.time()), outcome] + ([detail[:160]] if detail else [])
        await _cache.set(_sync_key(auditlog.pseudonymise(subject)), json.dumps(record), SYNC_TTL)
    except Exception as exc:
        log.debug("entitlement sync note failed: %s", type(exc).__name__)


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
        # A refund keeps its expiry date — the period was paid for and then
        # unpaid — so expiry alone would leave a refunded subscription counted
        # as active revenue until it happened to lapse. See `_sub_is_alive`.
        alive = _sub_is_alive(e, now)
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
    def _alive(e: dict) -> bool:
        return _sub_is_alive(e, now)

    rows = sorted(doc.values(), key=lambda e: (not _alive(e), float(e.get("expires") or 0)))
    # Literal spaces between every column, not field widths alone — the same
    # reason `_experiment_text` documents for its own table: a value exactly as
    # wide as its field gets no padding and runs into its neighbour.
    header = (f"{'plan':<8} {'via':<5} {'since':<7} "
              f"{'renews':<7} {'↻':<2} {'seen':<7} {'id':<6}")
    body = [header]
    for e in rows[:TABLE_ROWS]:
        renews = _short_date(int(e["expires"])) if e.get("expires") else "never"
        if e.get("revoked") is not None:
            renews = "refund"
        elif not _alive(e):
            renews = "ended"
        body.append(
            f"{_plan(e.get('product')):<8} {_via(e.get('acq')):<5} "
            f"{(_short_date(int(e['first'])) if e.get('first') else '?'):<7} "
            f"{renews:<7} {_renew_mark(e):<2} "
            f"{(_short_date(int(e['seen'])) if e.get('seen') else '?'):<7} "
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
        worth = (" · " + " + ".join(_money(v, c) for c, v in sorted(value.items()))
                 if value else "")
        lines.append(f"Due in 7 days: {len(due)} renew or end ({len(paid_due)} paid{worth})")
    lines.append("↻ renews · ✕ auto-renew off · ? not reported yet")
    lines.append("Apple reports renewals, expiries and refunds directly, so this no "
                 "longer waits for an app launch to notice.")
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
    body = [f"{'id':<7} {'tier':<5} {'scans':<6} {'first':<7} {'last':<7}"]
    for who, e in rows[:TABLE_ROWS]:
        body.append(
            f"{who[:6]:<7} {('Pro' if e.get('tier') == 'pro' else 'free'):<5} "
            f"{int(e.get('scans', 0)):<6} "
            f"{(_short_date(int(e['first'])) if e.get('first') else '?'):<7} "
            f"{(_short_date(int(e['last'])) if e.get('last') else '?'):<7}")
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
    spend = await _spend(days)
    mine = await _operator_spend(days)
    parts = [f"Gemini ≈ {_usd(spend)}"]
    if scans:
        parts.append(f"{_usd_fine(max(spend - mine, 0.0) / scans)}/scan")
        avg_ms = await _sum_stat(days, "scan_ms")
        if avg_ms:
            parts.append(f"avg scan {avg_ms / scans / 1000:.1f}s")
    if mine > 0:
        parts.append(f"{_usd(mine)} mine")
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
        # Per-label tokens, so /costs can separate what users cost from what
        # the operator's own bot usage costs. `calls_{label}` alone could not:
        # it counts calls, and an ideas generation is not the size of a scan.
        if tok_in:
            await _cache.incr(_stat_key(day, f"tok_in_{label}"), STATS_TTL, tok_in)
        if tok_out:
            await _cache.incr(_stat_key(day, f"tok_out_{label}"), STATS_TTL, tok_out)

        budget = GEMINI_DAILY_BUDGET_USD
        if budget > 0:
            spend = await _spend([day])
            if spend > budget and await _cache.add(f"opsseen:budget:{day}", "1", STATS_TTL):
                await _notifier.send(
                    "💸 <b>Gemini spend over budget</b>\n"
                    f"Today ≈ {_usd(spend)} against a {_usd(budget)} daily budget. "
                    "Scans keep working; this is a heads-up, not a cut-off.",
                    _COSTS_BUTTONS)
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


# Model calls the operator makes through the bot: /post ideas, the /checkup
# one-token probe, and a photo sent to the bot as a test scan. They are billed
# like any other call and belong in the total — but not in "$/scan" or in what
# the free tier is "given away", both of which are statements about users.
_OPERATOR_LABELS = ("ideas", "probe", "bot_scan", "bot_scan_with_tag")


async def _operator_spend(days: list[str]) -> float:
    tok_in = sum([await _sum_stat(days, f"tok_in_{label}") for label in _OPERATOR_LABELS])
    tok_out = sum([await _sum_stat(days, f"tok_out_{label}") for label in _OPERATOR_LABELS])
    return _cost_usd(tok_in, tok_out)


async def _costs_text() -> str:
    lines = ["💸 <b>Gemini spend</b>"]
    for label, n in (("Today", 1), ("Last 7 days", 7), ("Last 30 days", 30)):
        days = _days_ending_today(n)
        tok_in = await _sum_stat(days, "tok_in")
        tok_out = await _sum_stat(days, "tok_out")
        calls = await _sum_stat(days, "model_calls")
        scans = await _sum_stat(days, "scans_free") + await _sum_stat(days, "scans_pro")
        spend = _cost_usd(tok_in, tok_out)
        mine = await _operator_spend(days)
        parts = [f"{label}: {_usd(spend)}", f"{calls} calls",
                 f"{_kilo(tok_in)} in / {_kilo(tok_out)} out"]
        if scans:
            # Users' spend, not total spend. This used to divide the whole
            # figure — operator test scans, /post and /checkup probes included
            # — by the user scan count, which at 1-4 scans a day made "$/scan"
            # substantially the operator's own usage.
            parts.append(f"{_usd_fine(max(spend - mine, 0.0) / scans)}/scan")
        if mine > 0:
            parts.append(f"{_usd(mine)} mine")
        lines.append(" · ".join(parts))

    month = _days_ending_today(30)
    free = await _sum_stat(month, "scans_free")
    total = free + await _sum_stat(month, "scans_pro")
    if total:
        users = max((await _spend(month)) - (await _operator_spend(month)), 0.0)
        given = users * free / total
        lines.append(f"Free tier, 30 days: {free} of {total} scans ≈ {_usd(given)} given away")
    mine_month = await _operator_spend(month)
    if mine_month > 0:
        lines.append(f"My own bot usage, 30 days: ≈ {_usd(mine_month)} "
                     f"(/post, /checkup — excluded from the two figures above)")

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
    docs: list[tuple[str, dict]] = []
    for day in days:
        try:
            doc = json.loads(await _cache.get(_stat_key(day, "top")) or "{}")
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
        recorded = await _cache.get(TRENDS_TAGGED_SINCE_KEY)
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
        scans += await _read_stat(day, "scans_free") + await _read_stat(day, "scans_pro")
    finds = [f for _, doc in docs for f in doc.get("finds") or [] if isinstance(f, dict)]
    return (_floored(docs, "cats", "cat_devices", TRENDS_MIN_CATEGORY_DEVICES,
                     _normalise_category, tagged_since),
            _floored(docs, "brands", "brand_devices", TRENDS_MIN_BRAND_DEVICES,
                     _clean_brand, tagged_since),
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
    this_week = [_day(end - timedelta(days=i)) for i in range(7)]
    last_week = [_day(end - timedelta(days=i)) for i in range(7, 14)]
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
            brand = _clean_brand(str(f.get("b") or ""))
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
        out.append(_day(cur))
        cur += timedelta(days=1)
    return out


# The partial day's footnote, shared by the table and the export so the two
# cannot disagree about what that row is.
EXPERIMENT_PARTIAL_NOTE = ("limit hits counted from 18:29 UTC that day only — the "
                           "counter shipped mid-day")

# The counters `/experiment` shows, in its column order. The export's header
# uses these names as they are, so a kept copy can be traced back to the code.
EXPERIMENT_COUNTERS = ("active_users", "scans_free", "limit_hits", "new_subs")


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


async def _lever_changes_in(days: list[str]) -> list[list]:
    """The lever's recorded changes that fall on one of `days`, oldest first."""
    return [c for c in ((await _levers()).get("changes") or [])
            if isinstance(c, list) and len(c) == 3 and c[0] in days]


async def _experiment_text(now: datetime | None = None) -> str:
    """The experiment's server-side half, whole window at once.

    The digest reports one day at a time, which answers "what happened
    yesterday" and not "is this working" — for that the operator would have to
    scroll back through a fortnight of messages and add them up by hand. This is
    the running total, and since issue #125 was closed it is the only automated
    read of the experiment: the client's half is a dashboard someone has to
    remember to open.

    Deliberately not a conversion claim. `new_subs` beside `limit_hits` is a
    coincidence within a window, not an attribution — nothing here knows whether
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

    today = _day(now)
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
    rows = [f"<code>{'day':<6}{'act':>6} {'free':>5} {'hit':>5} {'sub':>5}</code>"]
    hits = subs = free_scans = expired = 0
    partial = False
    for d in shown:
        label = f"{d[4:6]}-{d[6:]}"
        if _stat_expired(d, now):
            expired += 1
            rows.append(
                f"<code>{label:<6}{'—':>6} {'—':>5} {'—':>5} {'—':>5}</code>")
            continue
        act = await _read_stat(d, "active_users")
        fr = await _read_stat(d, "scans_free")
        hi = await _read_stat(d, "limit_hits")
        sb = await _read_stat(d, "new_subs")
        hits += hi
        subs += sb
        free_scans += fr
        mark = ""
        if d == EXPERIMENT_PARTIAL_DAY:
            partial, mark = True, " *"
        rows.append(
            f"<code>{label:<6}{act:>6} {fr:>5} {hi:>5} {sb:>5}</code>{mark}")

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
        total = (f"<b>{hits} limit hit{'s' if hits != 1 else ''} · {subs} "
                 f"new subscription{'s' if subs != 1 else ''} "
                 f"({100.0 * subs / hits:.0f}%)</b>{scope}")
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
    for day_changed, before, after in (await _lever_changes_in(shown))[-4:]:
        notes.append(f"⚠️ lever changed on {day_changed[4:6]}-{day_changed[6:]}: "
                     f"{_lever_label(before)} → {_lever_label(after)}")
    if partial:
        notes.append(f"* {EXPERIMENT_PARTIAL_NOTE}. Every other column is a whole day.")
    if hits:
        notes.append("% is subscriptions ÷ limit hits across the window — "
                     "coincidence, not attribution.")
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


async def _experiment_export(now: datetime | None = None) -> str:
    """`/experiment export`: the window's table as CSV, to keep.

    The counters behind `/experiment` carry STATS_TTL, so the server's record
    of the window deletes itself a day at a time — for the default window,
    20260910's row goes on 2026-10-15 and the rest over the fortnight after —
    and the daily digests that reported it are one day each. This is the one
    form of it that outlives the cache: a block to copy into `docs/`.

    Read `required`, unlike the table. A zero in a kept copy is a claim that
    nothing happened, so an unreadable cache refuses the export rather than
    writing zeros into it. An expired day has empty cells, not zeros, for the
    reason the table prints "—".
    """
    now = now or datetime.now(timezone.utc)
    start, end = _parse_day(EXPERIMENT_START_DAY), _parse_day(EXPERIMENT_END_DAY)
    if start is None or end is None or end < start:
        return ("💾 Nothing exported — the window is misconfigured: "
                "EXPERIMENT_START_DAY and EXPERIMENT_END_DAY must both be "
                "YYYYMMDD, end on or after start.")
    today = _day(now)
    shown = [d for d in _day_span(start, end) if d <= today]
    if not shown:
        return f"💾 Nothing to export — the window opens {start:%d %b}."

    _, head, why = _welcome_summary(await _welcome_setting())
    ttl_days = STATS_TTL // 86400
    lines = [f"# SnapWorth free-scan experiment, {start:%Y-%m-%d} to {end:%Y-%m-%d}; "
             f"exported {now:%Y-%m-%d %H:%M} UTC"
             + ("" if today > EXPERIMENT_END_DAY else " while the window was open"),
             f"# welcome at export: {head} — {why}"]
    for day_changed, before, after in await _lever_changes_in(shown):
        lines.append(f"# lever changed {day_changed[:4]}-{day_changed[4:6]}-"
                     f"{day_changed[6:]}: {_lever_label(before)} -> {_lever_label(after)}")
    lines.append(",".join(["day", *EXPERIMENT_COUNTERS, "note"]))
    try:
        for d in shown:
            iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            if _stat_expired(d, now):
                lines.append(iso + "," * len(EXPERIMENT_COUNTERS)
                             + f",expired: past the {ttl_days}-day counter TTL")
                continue
            values = []
            for name in EXPERIMENT_COUNTERS:
                raw = await _cache.get(_stat_key(d, name), required=True)
                values.append(str(int(raw or 0)))
            note = EXPERIMENT_PARTIAL_NOTE if d == EXPERIMENT_PARTIAL_DAY else ""
            lines.append(",".join([iso, *values, note]))
    except Exception as exc:
        return ("💾 <b>Nothing exported</b> — the counters could not be read "
                f"({html.escape(type(exc).__name__)}), and a copy with zeros in "
                "their place would say nothing happened. Try again in a minute.")

    kept = [d for d in shown if not _stat_expired(d, now)]
    expiry = (f"The {kept[0][4:6]}-{kept[0][6:]} counters expire on "
              f"{_stat_expires_on(kept[0]):%d %b}, the rest a day at a time after."
              if kept else "Every day in it is already past the counter TTL.")
    csv = html.escape("\n".join(lines))
    return ("💾 <b>Free-scan experiment — export</b>\n"
            f"Copy the block into <code>docs/</code> to keep it. {expiry}\n"
            f"<pre>{csv}</pre>")


# ── One subscription, live from Apple ────────────────────────────────────────

async def _resolve_transaction_id(wanted: str) -> tuple[str | None, str | None]:
    """Turn what the operator typed into an originalTransactionId.

    Returns `(transaction_id, error_message)` — exactly one is not None.

    Two kinds of input here, because the operator has two kinds of id to hand
    and only one of them is Apple's (the third, an order id off the customer's
    receipt, is `_apple_order_id`'s):

      * an originalTransactionId, as `/subs` keys its rows and as Apple's
        own console shows it — all digits, and long;
      * a device id: the six-character `id` column from `/subs` and `/users`,
        the eight `/user` prints, or all sixteen from a support mail's
        "Device …" line — any prefix of the pseudonym.

    The device id is **not** a truncated transaction id and cannot be turned
    back into one by itself: it is `sha256(AUDIT_SALT + subject)[:16]`, a
    deliberately one-way pseudonym (`auditlog.pseudonymise`) so the audit log
    is not a device registry. What makes the lookup possible is that the subs
    index is keyed by the *full* transaction id with the devices that synced
    it stored in the row, so this is a scan, not a decode — the same reverse
    lookup `_user_text` does.

    Two consequences worth stating, because both look like bugs otherwise:
    a device whose subscription only ever arrived by notification is not on
    the row at all and is unreachable this way, and a short prefix of a
    sixteen-character hash can collide.
    """
    wanted = _device_argument(wanted)
    if not wanted:
        return None, ("Usage: /sub &lt;id&gt; — an originalTransactionId, the "
                      "order ID from the customer's Apple receipt, or a device "
                      "id: the id column from /subs, or the 16 characters after "
                      "\"Device\" in a support mail.")

    # Apple's transaction ids are long decimal strings. Anything of that shape
    # is passed through untouched: the index may well not have it, which is
    # the whole point of asking Apple directly.
    #
    # Except at sixteen characters, which is also a full pseudonym. It is hex,
    # and about one device in 1,845 has one made only of digits; sent to Apple
    # as a transaction id it came back "a typo", and the index — which does
    # have it — was never asked. So a sixteen-digit id is looked for among the
    # devices first, unless it is a transaction id the index already keys, and
    # is Apple's only when no device matches.
    #
    # Matched whole, not by prefix. Nearly every real transaction id begins
    # 200000 or 100000, so one older row holding a six-character device id of
    # that shape would otherwise catch every unindexed transaction id pasted
    # here and answer with that customer's subscription.
    looks_like_transaction = wanted.isdigit() and len(wanted) >= 10
    if looks_like_transaction and len(wanted) != 16:
        return wanted, None

    doc = await _read_index(SUBS_INDEX_KEY)
    if looks_like_transaction and wanted in doc:
        return wanted, None
    matches: dict[str, dict] = {}
    devices: set[str] = set()
    for otid, row in doc.items():
        if not isinstance(row, dict):
            continue
        hits = [d for d in _row_devices(row)
                if (d.lower() == wanted if looks_like_transaction else _same_device(d, wanted))]
        if hits:
            matches[otid] = row
            devices.update(d.lower() for d in hits)
    if not matches and looks_like_transaction:
        return wanted, None
    if not matches:
        return None, (f"💳 Nothing in the index has a device id starting "
                      f"<code>{html.escape(wanted)}</code>. If you have Apple's "
                      "originalTransactionId, or the order ID from the "
                      "customer's receipt, pass that instead — neither needs "
                      "to be in the index. /user shows whether this device "
                      "ever tried to sync a purchase, and what happened.")

    # Several rows for one device is normal — a resubscribe, or a plan change —
    # and harmless, because Apple returns every subscription belonging to the
    # customer behind whichever id we send. Several *devices* is a genuine
    # collision and the operator has to disambiguate. A six-character id kept
    # from an older row is the same device as the full one it begins.
    distinct = {d for d in devices
                if not any(o != d and o.startswith(d) for o in devices)}
    if len(distinct) > 1:
        return None, (f"💳 {len(distinct)} devices start with "
                      f"<code>{html.escape(wanted)}</code> — give more "
                      "characters: " + ", ".join(
                          html.escape(d[:8]) for d in sorted(distinct)[:6]))
    return max(matches, key=lambda otid: float(matches[otid].get("seen") or 0)), None


# An App Store order ID, as printed on the customer's receipt email ("Order
# ID: MK5TTTV8JH"): upper-case letters and digits. Checked after the other
# shapes, so an all-digit transaction id or an all-hex device id never lands
# here.
_ORDER_ID = re.compile(r"[A-Z0-9]{8,20}")


def _apple_order_id(argument: str | None) -> str | None:
    """The argument as an Apple order ID, or None if it is not shaped like one.

    The one id a customer can always find: it is on the receipt Apple emails
    for every purchase, and it is what they paste when they have nothing
    else. Apple's Look Up Order ID resolves it to their transactions."""
    value = (argument or "").strip().upper()
    if (not _ORDER_ID.fullmatch(value) or value.isdigit()
            or re.fullmatch(r"[0-9A-F]+", value)):
        return None
    return value


def _status_lines(status) -> list[str]:
    """One subscription, as the operator reads it."""
    ent = status.entitlement
    lines = [f"<b>{html.escape(_plan(ent.product_id))}</b> — "
             f"{html.escape(status.state)}"]

    detail = [html.escape(ent.environment), _acquisition(ent)]
    if isinstance(ent.price, (int, float)) and ent.price > 0:
        detail.append(_money(ent.price, ent.currency))
    lines.append(" · ".join(detail))

    if status.offer_identifier:
        # Apple's offerIdentifier is the code the customer typed or the promo
        # offer's id. `_acquisition` above already says which kind it was.
        lines.append(f"Offer: <code>{html.escape(status.offer_identifier)}</code>")

    if ent.expires_at:
        # Not `.capitalize()`: it lowercases everything after the first
        # character, which turns "12 Apr 2027" into "12 apr 2027".
        phrase = _renewal_phrase(ent.expires_at, status.auto_renew)
        lines.append(phrase[0].upper() + phrase[1:])
    if status.auto_renew is False:
        lines.append("Auto-renew is <b>off</b> — this one is leaving.")
    elif status.auto_renew is None:
        lines.append("Auto-renew: Apple sent no renewal info.")
    if status.auto_renew_product_id:
        lines.append("Next period switches to "
                     f"<b>{html.escape(_plan(status.auto_renew_product_id))}</b>.")

    if ent.revoked_at:
        lines.append(f"Revoked {_date(ent.revoked_at)}.")
    if ent.original_purchase_at:
        lines.append(f"First purchased {_date(ent.original_purchase_at)}.")
    if ent.original_transaction_id:
        lines.append(f"<code>{html.escape(ent.original_transaction_id)}</code>")
    return lines


async def _sub_command(rest: str) -> tuple[str, Buttons]:
    """`/sub <id>`, and `/sub <otid> lift [yes]` for a stale refund block."""
    parts = (rest or "").split()
    if len(parts) >= 2 and parts[1].lower() == "lift":
        confirmed = any(token.lower() == "yes" for token in parts[2:])
        return await _lift_refund_block(parts[0], confirmed)
    text, offers = await _sub_text(rest)
    return text, offers + await _buttons()


async def _ask_apple(transaction_id: str) -> tuple[list | None, str | None]:
    """`appstorestatus.lookup`, with each failure in the operator's words.

    Returns `(statuses, error_message)` — exactly one is not None.
    """
    import appstorestatus

    try:
        return await appstorestatus.lookup(transaction_id), None
    except appstorestatus.StatusError as exc:
        return None, _apple_problem(exc)


async def _ask_apple_order(order_id: str) -> tuple[str | None, str | None]:
    """The transaction id behind the Order ID on a customer's App Store
    receipt, through Apple's Look Up Order ID, for `_ask_apple`.

    Returns `(transaction_id, error_message)` — exactly one is not None.
    """
    import appstorestatus

    try:
        return (await appstorestatus.lookup_order(order_id))[0], None
    except appstorestatus.StatusError as exc:
        return None, _apple_problem(exc)


def _apple_problem(exc: Exception) -> str:
    """One of `appstorestatus`'s failures, as the operator reads it."""
    import appstorestatus
    import entitlements

    if isinstance(exc, appstorestatus.OrderNotFound):
        return (f"💳 {html.escape(str(exc))}\n\nRead as an Apple order ID — the "
                "\"Order ID\" on the customer's App Store receipt email. A "
                "transaction id is all digits; a device id is hex.")
    if isinstance(exc, appstorestatus.SubscriberNotFound):
        return (f"💳 {html.escape(str(exc))}\n\nChecked Production and Sandbox. "
                "An id Apple does not recognise is usually a transactionId from "
                "a different app, or a typo.")
    if isinstance(exc, appstorestatus.StatusRetryLater):
        # Right after a purchase — when the support mail is written — Apple
        # answers "not found, retry". Reported as that, not as a typo, and
        # without Sandbox's "never heard of it" standing in for it.
        text = (f"💳 <b>Not yet — retry in a few minutes</b>\n{html.escape(str(exc))}\n\n"
                "Not necessarily a typo: Apple marks this not-found as "
                "retryable, which is what a purchase from the last few minutes "
                "looks like. ")
        if exc.environment == "Production":
            return text + "Sandbox was not asked."
        # Production's not-found was definite, so the lookup went on to
        # Sandbox, and it is Sandbox saying "not yet". That Production has
        # nothing is the useful half: this is a TestFlight or App Review
        # purchase, which this server may refuse whatever Sandbox says next.
        text += (f"Production has nothing under this id; it is "
                 f"{html.escape(exc.environment)} that says not yet.")
        if exc.environment not in entitlements.ALLOWED_ENVIRONMENTS:
            text += (f"\n⚠️ <b>This server refuses {html.escape(exc.environment)} "
                     "purchases</b> (ALLOWED_STOREKIT_ENVIRONMENTS): even once Apple "
                     "has it, the app is told free for this one. TestFlight and "
                     "App Review buy in Sandbox.")
        return text
    if isinstance(exc, appstorestatus.StatusNotConfigured):
        return (f"💳 {html.escape(str(exc))}\n\nThe rest of the bot is "
                "unaffected — /subs still reports what notifications have said.")
    if isinstance(exc, appstorestatus.StatusRateLimited):
        return f"💳 {html.escape(str(exc))}"
    if isinstance(exc, appstorestatus.StatusCredentialsRejected):
        return f"💳 <b>Credentials refused</b>\n{html.escape(str(exc))}"
    # StatusUnavailable and anything added later. Still named, still not
    # silent — this branch exists so a new subclass cannot become a
    # mystery empty reply.
    return f"💳 <b>Could not ask Apple</b>\n{html.escape(str(exc))}"


def _block_denies(tombstone: dict, ent) -> bool:
    """Whether a refund block denies `ent`'s term — `_is_revoked`'s rule."""
    blocked_until = tombstone.get("expires_at")
    if ent.expires_at is None or not isinstance(blocked_until, (int, float)):
        return True
    return ent.expires_at <= blocked_until


async def _refund_block_lines(statuses) -> tuple[list[str], Buttons]:
    """The access path's refund blocks for these subscriptions, if any.

    A REFUND writes `entrevoked:{originalTransactionId}`, and the access path
    denies the term it names for up to 400 days. Nothing showed it: a customer
    whose refund Apple had reversed read as free on every sync, this command
    said they were paying, `/subs` said `refund`, and finding the cause took
    `redis-cli` against production. The block is shown next to what Apple
    says now, and when Apple says the term is not refunded, lifting it is
    offered — as two taps, see `_lift_refund_block`.
    """
    import entitlements

    lines: list[str] = []
    offers: Buttons = []
    latest: dict[str, object] = {}
    for status in statuses:
        otid = status.entitlement.original_transaction_id
        if otid:
            latest.setdefault(otid, status.entitlement)
    for otid, ent in latest.items():
        code = f"<code>{html.escape(otid)}</code>"
        try:
            tombstone = await entitlements.read_revocation(_cache, otid)
        except Exception as exc:
            lines.append(f"Refund block for {code}: could not read the store "
                         f"({html.escape(type(exc).__name__)}).")
            continue
        if tombstone is None:
            continue
        until = tombstone.get("expires_at")
        what = (f"denies terms ending by {_date(int(until))}"
                if isinstance(until, (int, float)) else "denies every term")
        lines.append(f"🚫 <b>Refund block</b> on {code}: {what}.")
        if getattr(ent, "revoked_at", None) is not None:
            lines.append("Apple still shows this term refunded, so the block is right.")
        elif _block_denies(tombstone, ent):
            lines.append("Apple shows this term <b>not</b> refunded, so the block "
                         "is denying Pro to someone paying for it.")
            offers.append([("🔓 Lift refund block", f"sub {otid} lift")])
        else:
            lines.append("It does not cover the current term.")
    return lines, offers


async def _lift_refund_block(otid: str, confirmed: bool) -> tuple[str, Buttons]:
    """Delete one refund block — only once Apple says the refund is gone.

    Two taps, like `/lever`: the first names the block, the second lifts it.
    And the second asks Apple again rather than trusting the first. Lifting a
    block on a term Apple still shows refunded would let the server re-derive
    Pro from the pre-refund proof it holds, which is the bug the block exists
    to stop.
    """
    import entitlements

    otid = otid.strip()
    code = f"<code>{html.escape(otid)}</code>"
    if not (otid.isdigit() and len(otid) >= 10):
        return ("Usage: /sub &lt;originalTransactionId&gt; lift — the full id "
                "from /sub, not the short one.", await _buttons())
    try:
        tombstone = await entitlements.read_revocation(_cache, otid)
    except Exception as exc:
        return (f"🚫 Could not read the refund block for {code} "
                f"({html.escape(type(exc).__name__)}). Nothing was changed.",
                await _buttons())
    if tombstone is None:
        return f"🚫 No refund block is held for {code}.", await _buttons()

    if not confirmed:
        until = tombstone.get("expires_at")
        what = (f"denies terms ending by {_date(int(until))}"
                if isinstance(until, (int, float)) else "denies every term")
        return (f"🔓 <b>Lift the refund block on {code}?</b>\n"
                f"It {what}. Apple is asked again first, and it is lifted only "
                "if Apple no longer shows the term refunded.",
                [[("✅ Yes, lift it", f"sub {otid} lift yes"),
                  ("Cancel", f"sub {otid}")]])

    statuses, problem = await _ask_apple(otid)
    if problem is not None:
        return problem + "\n\nThe refund block was left in place.", await _buttons()
    assert statuses is not None
    mine = [st for st in statuses if st.entitlement.original_transaction_id == otid]
    if not mine:
        return (f"🚫 Apple returned nothing under {code}, so the block was left "
                "in place.", await _buttons())
    if any(getattr(st.entitlement, "revoked_at", None) is not None for st in mine):
        return (f"🚫 Apple still shows {code} refunded. The block was left in "
                "place: lifting it would let the server re-derive Pro from the "
                "proof it holds.", await _buttons())
    try:
        await entitlements.clear_revocation(_cache, otid)
    except Exception as exc:
        return (f"🚫 Could not lift the block on {code} "
                f"({html.escape(type(exc).__name__)}). Try again.", await _buttons())
    return (f"🔓 Refund block lifted on {code}. Pro comes back at the app's "
            "next sync, if not sooner.", await _buttons())


async def _sub_text(argument: str) -> tuple[str, Buttons]:
    """Ask Apple what one subscription is doing, right now.

    Everything else the bot knows about subscriptions is a cache of what it was
    told — by a device at launch, or by a notification that may have arrived
    while the endpoint was down. This is the one command that goes and asks,
    which makes it the one worth trusting when a customer disagrees with the
    index.

    Every failure is reported in the operator's words rather than swallowed:
    "no result" and "the key is wrong" are the two answers that must never look
    alike, because one sends you to App Store Connect and the other to the
    hosting panel.
    """
    # Imported here, not at module scope. `entitlements` imports *this* module
    # (entitlements.py:38) to report what it verifies, and `appstorestatus`
    # imports `entitlements` — so either at the top of this file closes an
    # import cycle. The same reason `appstorenotify`'s header gives for
    # duck-typing the notification it is handed.
    import entitlements

    via: list[str] = []
    order_id = _apple_order_id(argument)
    if order_id is None:
        transaction_id, problem = await _resolve_transaction_id(argument or "")
    else:
        transaction_id, problem = await _ask_apple_order(order_id)
        if transaction_id is not None:
            via = [f"Order <code>{html.escape(order_id)}</code> → "
                   f"<code>{html.escape(transaction_id)}</code>"]
    if problem is not None:
        return problem, []
    assert transaction_id is not None

    statuses, problem = await _ask_apple(transaction_id)
    if problem is not None:
        return problem, []
    assert statuses is not None

    lines = [f"💳 <b>Live from Apple</b> — {len(statuses)} subscription"
             f"{'s' if len(statuses) != 1 else ''}", *via]
    # The lookup asks Production first and moves on to Sandbox only when
    # Production definitely has nothing, so a Sandbox answer means both were
    # asked. Said outright: it used to be one word on the detail line.
    if statuses and all(status.environment == "Sandbox" for status in statuses):
        lines.append("Production: nothing under this id. The answer below is Sandbox's.")
    for status in statuses:
        lines.append("")
        lines.extend(_status_lines(status))
        environment = status.entitlement.environment
        if environment not in entitlements.ALLOWED_ENVIRONMENTS:
            # Apple says "active"; this server says free. `verify_signed_
            # transaction` refuses any environment outside the allowed set —
            # which is what keeps a free Sandbox tester from being production
            # Pro — so a TestFlight or App Review purchase never unlocks Pro
            # here, and "active" alone would send the operator looking for a
            # bug that is a policy.
            lines.append(
                f"⚠️ <b>This server refuses {html.escape(environment)} purchases</b> "
                "(ALLOWED_STOREKIT_ENVIRONMENTS): the app is told free for this "
                "one, and it is not indexed. TestFlight and App Review buy in Sandbox.")

    block_lines, offers = await _refund_block_lines(statuses)
    if block_lines:
        lines.append("")
        lines.extend(block_lines)

    # Fold what Apple just said back into the index. This is the only writer
    # that can correct a row which drifted — a notification that never arrived
    # leaves no trace to repair, and the device path cannot see auto-renew at
    # all.
    #
    # Gated on the environment, exactly as the notification path is: a Sandbox
    # subscription is signed identically to a production one, and writing a
    # TestFlight tester into the index puts their free renewals into /subs's
    # revenue figures.
    indexed = 0
    for status in statuses:
        if status.entitlement.environment not in entitlements.ALLOWED_ENVIRONMENTS:
            continue
        if not status.entitlement.original_transaction_id:
            continue
        try:
            # None: the index could not be read, so nothing was written, and
            # the line below must not say otherwise. `current`: this is
            # Apple's word now, so a refund it no longer shows is cleared.
            if await _index_subscription(
                    None, status.entitlement, status.auto_renew,
                    current=True) is not None:
                indexed += 1
        except Exception:
            # The answer above is the point of the command; failing to cache it
            # must not lose it.
            log.warning("could not index a live status result", exc_info=True)
    if indexed:
        lines.append("")
        lines.append(f"Index updated from this lookup ({indexed} row"
                     f"{'s' if indexed != 1 else ''}).")
    return "\n".join(lines), offers


# ── One device, for a support email ──────────────────────────────────────────

#: How /user words the last `/auth/entitlement` result it recorded.
_SYNC_WORDS = {
    "pro": "verified as Pro",
    "free": "verified, but not Pro — refunded, revoked or expired",
    "rejected": "REJECTED",
}


async def _user_text(argument: str) -> str:
    wanted = _device_argument(argument)
    if not wanted:
        return ("Usage: /user &lt;id&gt; — the id column from /users or /subs, or "
                "the 16 characters after \"Device\" in a support mail.")
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
    try:
        sync = (json.loads(await _cache.get(_sync_key(who)) or "null")
                if _cache is not None else None)
    except Exception:
        sync = None
    if isinstance(sync, list) and len(sync) >= 2:
        # What the server made of the last signed transaction this device
        # sent. When it was refused, this is the only place that says so —
        # nothing is indexed for a transaction that did not verify.
        ago = int(now - float(sync[0]))
        when = (f"{ago // 3600}h ago" if ago < 86400 else f"{ago // 86400}d ago")
        what = _SYNC_WORDS.get(str(sync[1]), html.escape(str(sync[1])))
        if len(sync) > 2 and sync[2]:
            what += f" — {html.escape(str(sync[2]))}"
        lines.append(f"Last purchase sync: {_date(int(sync[0]))} ({when}) — {what}")
    subs = [(otid, s) for otid, s in (await _read_index(SUBS_INDEX_KEY)).items()
            if isinstance(s, dict) and any(_same_device(d, who) for d in _row_devices(s))]
    for otid, s in subs:
        # `_sub_is_alive`, like the two readers of this same index in `/subs`.
        # This one tested expiry alone, so a refunded subscription — which
        # keeps its expiry — read "renews 12 Mar 2027" here while `/subs`
        # showed the same row as `refund`.
        alive = _sub_is_alive(s, now)
        renews = _date(int(s["expires"])) if s.get("expires") else "never"
        if s.get("revoked") is not None:
            state = f"refunded or revoked {_date(int(s['revoked']))}"
        elif alive and s.get("auto_renew") is False:
            # Same distinction the digest now draws, for the same reason: this
            # subscription is paid up and leaving. Saying "renews" here is the
            # single most misleading thing this command could print during the
            # support mail it exists for.
            state = f"ends {renews} (auto-renew off)"
        elif alive:
            state = f"renews {renews}"
        else:
            state = f"ended {renews}"
        # The transaction id is what Apple, App Store Connect and /sub all
        # take, so it is printed where it can be tapped and copied.
        lines.append(f"Subscription: {_plan(s.get('product'))} · "
                     f"{s.get('acq') or '?'} · {state} · <code>{html.escape(str(otid))}</code>")
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
    if not isinstance(not_after, str) or not not_after:
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


def _negative_forms(digits: str) -> list[str]:
    """The negative chat ids a bare positive number could stand for.

    Telegram writes the same chat three ways and web.telegram.org shows the id
    without saying which: a **basic group** is -<id>, a **supergroup or
    channel** is -100<id>, and a positive number is a person or a bot. Offering
    only the -100 form sends an operator with a plain group to an id that can
    never resolve, so offer both and let getChat settle it."""
    forms = [f"-{digits}"]
    if not digits.startswith("100"):
        forms.append(f"-100{digits}")
    return forms


async def _device_check_line(configured: bool) -> str:
    """Whether reinstall protection is actually working, not merely switched on.

    Three non-empty environment variables is what `is_configured` knows, and a
    typo'd key looks identical to a healthy one from here: a wrong key cannot
    recognise a reinstall, so it silently hands every reinstall a fresh
    allowance. The probe asks Apple."""
    if not configured:
        return "DeviceCheck: NOT configured — reinstalls get a fresh allowance"
    if _device_check_probe is None:
        return "DeviceCheck: configured"
    try:
        ok, detail = await asyncio.wait_for(_device_check_probe(), 8)
    except Exception as exc:
        return f"DeviceCheck: configured · probe failed ({type(exc).__name__})"
    if ok:
        return f"DeviceCheck: configured ✅ — {html.escape(detail)}"
    if ok is None:
        # Apple did not answer, so nothing is known about the key. This used
        # to fall through to REJECTED, which is an instruction to go and fix
        # the key. The quota treats the same failure as an outage and grants
        # (`quota.starting_balance`), so the allowance claim holds for as long
        # as the outage does, not until someone changes something.
        return (f"DeviceCheck: configured · Apple unreachable just now "
                f"({html.escape(detail)}) — reinstalls get a fresh allowance "
                "while this lasts; run /checkup again")
    return (f"DeviceCheck: configured but REJECTED — {html.escape(detail)}. "
            "Reinstalls get a fresh allowance until this is fixed.")


async def _archive_chat_line(chat_id: str) -> str:
    """One checkup line about TELEGRAM_ARCHIVE_CHAT_ID.

    Says which chat the id actually names before /clear forwards the whole
    conversation into it — and, when it names nothing, which other form of the
    same number does."""
    shown = html.escape(chat_id)
    if not chat_id.lstrip("-").isdigit():
        return (f"Archive chat: <code>{shown}</code> is not a chat id "
                "(digits only, negative for a group or channel)")

    if not chat_id.startswith("-"):
        forms = " or ".join(f"<code>{f}</code>" for f in _negative_forms(chat_id))
        return (f"Archive chat: <code>{shown}</code> is positive — a user or bot id. "
                f"A group or channel id is negative: try {forms} "
                "(a basic group is <code>-id</code>, a supergroup or channel <code>-100id</code>).")

    info = await _notifier.get_chat(chat_id)
    if not info:
        # The number is probably right and only its form is wrong — the mistake
        # this line used to *cause*. Try the other form before blaming the bot's
        # membership, so the fix is the id in front of the operator.
        digits = chat_id.lstrip("-")
        others = [f for f in _negative_forms(digits) if f != chat_id]
        if digits.startswith("100"):
            others.append(f"-{digits[3:]}")
        for other in others:
            if await _notifier.get_chat(other):
                return (f"Archive chat: <code>{shown}</code> not reachable, but "
                        f"<code>{other}</code> is the same chat — set "
                        f"{ARCHIVE_CHAT_ENV} to that.")
        return (f"Archive chat: <code>{shown}</code> not reachable — the bot is not in it, "
                "or the id is wrong. Add the bot to the chat (as an administrator, "
                "for a channel).")

    title = html.escape(str(info.get("title") or info.get("username") or "untitled"))
    kind = html.escape(str(info.get("type") or "chat"))
    return f"Archive chat: {title} ({kind}) ✅ — /clear forwards here first"


async def _appstore_api_line() -> str:
    """Whether /sub can ask Apple, and whether Apple is failing to reach us.

    The App Store Server API key is optional — a deployment without it boots,
    and only /sub says so — so the operator used to learn it was missing in
    the middle of a support mail. One call to Apple's notification history,
    failures only, answers both: it is refused when the key is wrong, and it
    lists what Apple tried to deliver here and could not. A refund among those
    is a refund whose Pro has not been withdrawn."""
    import appstorestatus
    try:
        failed, more = await asyncio.wait_for(appstorestatus.undelivered_notifications(), 10)
    except appstorestatus.StatusNotConfigured as exc:
        return f"App Store API: NOT configured — /sub cannot ask Apple. {html.escape(str(exc))}"
    except appstorestatus.StatusCredentialsRejected as exc:
        return f"App Store API: key REJECTED — {html.escape(str(exc))}"
    except Exception as exc:
        detail = str(exc) if isinstance(exc, appstorestatus.StatusError) else type(exc).__name__
        return f"App Store API: probe failed — {html.escape(detail)}"
    if not failed:
        return "App Store API: key accepted ✅ · no undelivered notifications in 24h"
    count = f"{failed}{'+' if more else ''}"
    return (f"App Store API: key accepted · ⚠️ Apple could not deliver {count} "
            f"notification{'s' if failed != 1 or more else ''} here in 24h — any refund "
            "among them has not been applied yet")


async def _last_appstore_notification_line() -> str:
    """When `/apple/notifications` last received something that verified."""
    try:
        record = json.loads(await _cache.get(LAST_APPSTORE_NOTIFICATION_KEY) or "null")
    except Exception:
        record = None
    if not (isinstance(record, list) and len(record) >= 3):
        return "Last verified App Store notification: none on record"
    ago = max(0, int(time.time() - float(record[0])))
    when = (f"{ago // 60} min ago" if ago < 7200 else
            f"{ago // 3600}h ago" if ago < 2 * 86400 else f"{ago // 86400}d ago")
    return (f"Last verified App Store notification: {when} "
            f"({html.escape(str(record[1]))}, {html.escape(str(record[2]))})")


# Redis holds state nothing can rebuild (RUNBOOK §9), so how it behaves when
# full and whether it survives a restart are operational facts, not tuning.
# Nothing reported either until this line: the only probe was a PING.
REDIS_MEMORY_WARN_FRACTION = 0.8
REDIS_SNAPSHOT_STALE_SECONDS = 24 * 3600
# How close to the boot time a "last save" must be to be read as the boot
# stamp rather than a snapshot, where INFO has no `rdb_saves` (before Redis 7).
REDIS_BOOT_STAMP_SLACK_SECONDS = 10


def _redis_line(info: dict, now: float) -> str:
    """One checkup line from Redis INFO, with a ⚠️ for each unsafe setting."""
    def num(key: str) -> int:
        try:
            return int(info.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    used, limit = num("used_memory"), num("maxmemory")
    policy = str(info.get("maxmemory_policy") or "unknown")
    evicted = num("evicted_keys")
    aof = num("aof_enabled") == 1
    last_save, save_ok = num("rdb_last_save_time"), info.get("rdb_last_bgsave_status")
    uptime = num("uptime_in_seconds")
    # Redis stamps rdb_last_save_time with its start time at boot ("at startup
    # we consider the DB saved"), with `save ""` and AOF off as much as with
    # persistence on. Read as a snapshot, that hid the restart warning below
    # for a day after every restart or redeploy — the moment the owner runs
    # Checkup after changing Railway's settings. `rdb_saves` counts real
    # snapshots since start; before Redis 7, a last save at boot is the stamp.
    if "rdb_saves" in info:
        saved = num("rdb_saves") > 0
    elif uptime:
        saved = last_save - (now - uptime) > REDIS_BOOT_STAMP_SLACK_SECONDS
    else:
        saved = bool(last_save)

    mb = 1024 * 1024
    memory = (f"{used / mb:.1f} MB of {limit / mb:.0f} MB ({used / limit:.0%})" if limit
              else f"{used / mb:.1f} MB, no limit")
    if saved:
        snapshot = f"last snapshot {int((now - last_save) // 3600)}h ago"
    elif uptime:
        snapshot = f"no snapshot since start {uptime // 3600}h ago"
    else:
        snapshot = "no snapshot"
    line = (f"Redis: {memory} · policy {html.escape(policy)} · evicted {evicted} · "
            f"AOF {'on' if aof else 'off'} · {snapshot}")

    warnings: list[str] = []
    if policy != "noeviction":
        # Any evicting policy drops keys to make room, and the keys here are
        # quota counters, entitlement proofs, refund tombstones and attest
        # state — a silent re-grant or a forced re-attestation.
        warnings.append(f"policy {html.escape(policy)} evicts state nothing can "
                        "rebuild — set noeviction")
    if not limit:
        warnings.append("no maxmemory — Redis grows until the container is killed")
    elif used >= limit * REDIS_MEMORY_WARN_FRACTION:
        warnings.append(f"above {REDIS_MEMORY_WARN_FRACTION:.0%} of maxmemory — "
                        "under noeviction, writes fail and free scans 503 at 100%")
    if evicted:
        warnings.append(f"{evicted} keys evicted since restart")
    if save_ok not in (None, "ok"):
        warnings.append(f"last snapshot failed ({html.escape(str(save_ok))})")
    if not aof and not saved:
        warnings.append("no AOF and no snapshot since Redis started — a restart "
                        "loses everything written since (RUNBOOK §9)")
    elif not aof and now - last_save > REDIS_SNAPSHOT_STALE_SECONDS:
        warnings.append("no AOF and no snapshot in 24h — a restart loses everything")
    return line + "".join(f"\n⚠️ {w}" for w in warnings)


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

    # Redis itself: what it does when full, and whether a restart loses it.
    redis_info = getattr(_cache, "redis_info", None)
    try:
        redis_stats = await redis_info() if redis_info is not None else None
        if redis_stats:
            lines.append(_redis_line(redis_stats, time.time()))
    except Exception as exc:
        lines.append(f"Redis INFO: error ({html.escape(type(exc).__name__)})")

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
            lines.append(await _device_check_line(bool(info["devicecheck"])))
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

    # App Store: can /sub ask Apple, and is Apple reaching the route that
    # withdraws refunds? Probed live, for the reason DeviceCheck is.
    lines.append(await _appstore_api_line())
    lines.append(await _last_appstore_notification_line())

    # The archive chat, if configured: does the id resolve, and to what?
    archive_chat = os.environ.get(ARCHIVE_CHAT_ENV, "").strip()
    if archive_chat:
        lines.append(await _archive_chat_line(archive_chat))

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

def _quiet_window(now: datetime) -> str:
    """Identify the quiet window `now` falls in, not the calendar day.

    QUIET_HOURS_UTC runs 13:00 through 03:59, so one window straddles UTC
    midnight. Keying the once-per-window guard on `_day(now)` therefore let a
    single silence fire the note twice — reproduced at 22:15 (note), 23:45
    (correctly suppressed), 00:15 next day (second note for the same silence).
    Hours after midnight belong to the window that opened the day before.
    """
    start = now - timedelta(days=1) if now.hour < 12 else now
    return _day(start)


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
    prior = [_day(when - timedelta(days=i)) for i in range(1, 8)]
    baseline = (await _sum_stat(prior, "scans_free") + await _sum_stat(prior, "scans_pro")) / 7
    if baseline <= 0 or scans < baseline * SPIKE_FACTOR:
        return ""
    return f"🔥 {scans / baseline:.1f}× the trailing week's daily average ({baseline:.1f}/day)"


# ── Clear: take back the last two days of the chat ──────────────────────────

async def _remember_message(message_id: int, text: str | None = None) -> None:
    """Note a message in the chat: its id (so /clear can delete it) and, for
    the bot's own messages, its text (so /clear can keep a copy)."""
    try:
        now = int(time.time())
        # `required`, so an unreadable list raises into the `except` below and
        # is left alone — read as empty, it was overwritten with one entry
        # and /clear lost every id and archived text before it.
        raw = await _cache.get(MESSAGES_KEY, required=True)
        entries = [e for e in (json.loads(raw) if raw else [])
                   if isinstance(e, list) and len(e) >= 2 and now - int(e[1]) < MESSAGES_TTL]
        entry: list[int | str] = [int(message_id), now]
        if text:
            entry.append(text[:4096])
        entries.append(entry)
        await _cache.set(MESSAGES_KEY, json.dumps(entries[-MESSAGES_CAP:]), MESSAGES_TTL)
    except Exception as exc:
        log.debug("message id note failed: %s", type(exc).__name__)


async def _archive(entries: list[list]) -> int | None:
    """Keep the text of the bot's messages that are about to be deleted.

    The number kept, or None when they could not be kept — which `/clear`
    must not take as "nothing to keep" and delete them anyway."""
    texts = [[int(e[1]), e[2]] for e in entries if len(e) >= 3 and e[2]]
    if not texts:
        return 0
    try:
        # `required` for the reason `_remember_message` gives: an archive read
        # as empty is an archive about to be replaced by this one batch.
        raw = await _cache.get(ARCHIVE_KEY, required=True)
        kept = [a for a in (json.loads(raw) if raw else []) if isinstance(a, list) and len(a) == 2]
        kept += texts
        await _cache.set(ARCHIVE_KEY, json.dumps(kept[-ARCHIVE_CAP:]), ARCHIVE_TTL)
    except Exception as exc:
        log.debug("archive write failed: %s", type(exc).__name__)
        return None
    return len(texts)


async def _tracked_messages() -> list[list]:
    """The chat's tracked messages: `[id, when]`, plus the text for the bot's."""
    try:
        raw = await _cache.get(MESSAGES_KEY) if _cache is not None else None
        return [e for e in (json.loads(raw) if raw else []) if isinstance(e, list) and e]
    except Exception:
        return []


async def _clear_prompt() -> tuple[str, Buttons]:
    """What 🧹 Clear is about to delete and what survives it, before it does.

    The button runs on one tap no longer. It sits beside 🗂 History on every
    keyboard, and it takes up to two days of alerts and /sub answers with it —
    and what it keeps is less than "everything": the text of the bot's own
    messages, of which /history shows the first lines, and nothing of the
    operator's or of any photo unless an archive chat is configured."""
    known = {int(e[0]) for e in await _tracked_messages()}
    if not known:
        return (CLEAR_NOTHING_TRACKED, await _buttons())
    if os.environ.get(ARCHIVE_CHAT_ENV, "").strip():
        kept = ("Kept: everything tracked is forwarded to the archive chat first, "
                "photos included, and the text of the bot's own messages stays in "
                "/history for 30 days.")
    else:
        kept = ("Kept: the text of the bot's own messages, for 30 days; /history "
                "shows the first lines of each. Your messages and photos are not kept.")
    return ("🧹 <b>Clear the chat?</b>\n"
            f"Deletes the {len(known)} message{'s' if len(known) != 1 else ''} the bot "
            "tracked here in the last 48 hours — yours and its own — and any it "
            "lost track of between them.\n" + kept,
            [[("🧹 Yes, clear it", "clear yes"), ("Cancel", "status")]])


_CLEAR_REFUSED = ("🧹 Nothing was cleared: the bot's messages could not be copied "
                  "to 🗂 History first, and deleting them uncopied would lose them. "
                  "Try again in a minute.")


async def _clear_chat() -> None:
    """Delete every message the bot remembers in this chat, then post a fresh
    status so the keyboard is still there. Only the last 48 hours can go —
    Telegram's limit for bots, not ours — and only what was tracked.

    What survives is the text of the bot's own messages, archived for /history
    first, and, when TELEGRAM_ARCHIVE_CHAT_ID names a second chat, everything
    tracked, forwarded there — a real Telegram copy, photos included. The
    operator's own messages and photos are not otherwise kept; `_clear_prompt`
    says so before this runs."""
    if _notifier is None or _cache is None:
        return
    try:
        # `required`: read as empty, an unreadable list was deleted below
        # without anything in it being archived. `_tracked_messages` reads
        # leniently, which is right for the prompt and wrong here.
        raw = await _cache.get(MESSAGES_KEY, required=True)
    except Exception as exc:
        log.warning("message list unreadable, not clearing: %s", type(exc).__name__)
        await _notifier.send(_CLEAR_REFUSED, await _buttons())
        return
    try:
        entries = [e for e in (json.loads(raw) if raw else []) if isinstance(e, list) and e]
    except Exception:
        entries = []
    known = sorted({int(e[0]) for e in entries})
    archived = await _archive(entries)
    if archived is None:
        # Deleting now would lose exactly the texts the archive exists to keep.
        await _notifier.send(_CLEAR_REFUSED, await _buttons())
        return
    archive_chat = os.environ.get(ARCHIVE_CHAT_ENV, "").strip()
    forwarded = await _notifier.forward_messages(archive_chat, known) if archive_chat and known else 0
    # Known ids first, then the gaps between them: private-chat ids are
    # sequential, and Telegram silently skips what it cannot delete. Only
    # *between* the oldest and newest known — see CLEAR_SWEEP_IDS.
    sweep: list[int] = []
    if known:
        oldest, newest = known[0], known[-1]
        tracked = set(known)
        sweep = [i for i in range(max(oldest, newest - CLEAR_SWEEP_IDS), newest + 1)
                 if i not in tracked]
    deleted = await _notifier.delete_messages(known) if known else 0
    try:
        await _cache.delete(MESSAGES_KEY)
    except Exception:
        pass
    swept = await _notifier.delete_messages(sweep) if sweep else 0
    if known:
        # Report the tracked deletions, which are a real count, and describe
        # the sweep as a sweep. These used to be added together and presented
        # as "Cleared N" — but Telegram answers `deleteMessages` with ok for
        # ids it silently skips (a message that never existed, or one past the
        # 48-hour limit), so every id in the blind sweep counted as a deletion
        # and N was really just the sweep's range size. It read as a precise
        # figure and was not one.
        note = f"🧹 Cleared {deleted} tracked message{'s' if deleted != 1 else ''}."
        if swept:
            note += (f" Also swept {swept} untracked id"
                     f"{'s' if swept != 1 else ''} between them — Telegram "
                     "does not say how many of those existed.")
        if archived:
            note += f" {archived} of the bot's kept — 🗂 History shows them."
        if archive_chat:
            note += f" {forwarded} forwarded to the archive chat."
            moved = getattr(_notifier, "last_forward_migrated_to", None)
            if moved:
                note += (f"\n⚠️ That chat is now a supergroup — set "
                         f"{ARCHIVE_CHAT_ENV} to <code>{html.escape(str(moved))}</code>; "
                         "this run followed the move.")
            elif not forwarded:
                why = getattr(_notifier, "last_forward_refusal", None)
                note += (f" (Telegram refused: {html.escape(str(why))})" if why else
                         " (nothing in the tracked list could be forwarded)")
    else:
        note = CLEAR_NOTHING_TRACKED
    note += ("\nTelegram lets a bot delete only the last 48 hours; anything older is "
             "chat menu → Clear History.")
    await _notifier.send(note + "\n\n" + await _status_text(), await _buttons())


def _snippet(text: str, limit: int = HISTORY_SNIPPET_CHARS) -> str:
    """A message as it was sent, shortened. The text is the bot's own Telegram
    HTML, so tags are stripped rather than re-escaped — cutting mid-tag would
    make the whole history message unparseable."""
    plain = re.sub(r"<[^>]+>", "", text)
    plain = html.unescape(plain)
    lines = [line for line in plain.split("\n") if line.strip()]
    clipped = len(lines) > HISTORY_SNIPPET_LINES
    plain = "\n".join(lines[:HISTORY_SNIPPET_LINES])
    if len(plain) > limit:
        plain = plain[:limit].rstrip()
        clipped = True
    return html.escape(plain) + ("…" if clipped else "")


async def _history_text(argument: str) -> str:
    try:
        count = min(HISTORY_MAX, max(1, int(argument))) if argument else HISTORY_DEFAULT
    except ValueError:
        count = HISTORY_DEFAULT
    try:
        raw = await _cache.get(ARCHIVE_KEY)
        entries = [a for a in (json.loads(raw) if raw else []) if isinstance(a, list) and len(a) == 2]
    except Exception:
        entries = []
    if not entries:
        return ("🗂 <b>History</b>\nNothing archived yet. 🧹 Clear keeps a copy of what the bot "
                "said; it shows up here.")
    shown = entries[-count:]
    lines = [f"🗂 <b>History</b> — last {len(shown)} of {len(entries)} kept messages"]
    budget = 3800
    blocks = []
    for at, text in reversed(shown):
        when = datetime.fromtimestamp(int(at), timezone.utc).strftime("%d %b %H:%M")
        block = f"<b>{when} UTC</b>\n{_snippet(str(text))}"
        if budget - len(block) < 0:
            blocks.append("…")
            break
        budget -= len(block)
        blocks.append(block)
    lines.append("\n\n".join(blocks))
    lines.append("/history 25 for more. Kept 30 days.")
    return "\n\n".join(lines)


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
