"""`/checkup`: every dependency on one screen, and the probes behind it.

Split out of notify.py (#230). Everything this reads that notify owns — the
cache, the model, the process facts, Telegram's getChat, the spend alert, the
poll lock and the keys notify writes — comes in per call as a `Wiring`, so
this module neither imports notify nor holds state of its own.
"""

from __future__ import annotations

import asyncio
import dataclasses
import html
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import auditlog
from devicecheck import PROBE_NOT_SENT

if TYPE_CHECKING:
    from cache import ResilientCache

# notify's logger, so log filters written before the split still match.
log = logging.getLogger("snapworth.notify")

# /checkup's model probe. JSON, because the model runs in JSON mode; and room
# to think, because the first version asked for "OK" in 16 tokens and a
# thinking model spent them all thinking — an empty reply, reported as
# "Gemini: FAILED" while every real scan was succeeding.
PROBE_PROMPT = 'Return ONLY this JSON object and nothing else: {"ok": true}'
PROBE_MAX_TOKENS = 1024


@dataclasses.dataclass(frozen=True)
class Wiring:
    """What /checkup needs from notify, read fresh for each run."""
    cache: ResilientCache
    generator: Callable[..., Awaitable[str]] | None
    status_provider: Callable[[], dict] | None
    device_check_probe: Callable[[], Awaitable[tuple[bool | None, str]]] | None
    get_chat: Callable[[str], Awaitable[dict | None]]
    budget_line: Callable[[], Awaitable[str]]
    referral_line: Callable[[], Awaitable[str]]
    replica_label: Callable[[dict], str]
    read_int: Callable[[str], Awaitable[int]]
    poll_token: str
    poll_lock_key: str
    last_scan_key: str
    last_appstore_notification_key: str
    archive_chat_env: str


# The SPKI pins the iOS app holds (`Config.pinnedSPKIHashes`), so /checkup can
# say whether the chain this host serves would pass them. A copy, because the
# app is Swift: `test_notify.py` reads Config.swift and fails when the two
# differ, and backend.yml runs it on a pull request that changes that file.
#
# The app only *reports* a mismatch today (`pinningEnforced` is false), and
# its telemetry can only see chains that are actually served — which is how
# the set came to name an intermediate the host had stopped using and to miss
# the ECDSA chain's root entirely. Hashing the live chain here is the check
# that does not wait for a phone.
PINNED_SPKI_HASHES: dict[str, str] = {
    "fk6IOKit1ild5647BH06ujSIq5XbCgqlbYl6ANhhi88=": "ISRG Root YR",
    "sCkq5UWXjg+7mKu9lMhhYF5bGLsy7VI/UNW3tccdR7w=": "ISRG Root YE",
    "C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=": "ISRG Root X1",
    "diGVwiVYbubAI3RW4hB9xU8e/CH2GnkuvVFZE8zmgzI=": "ISRG Root X2",
}


def _tls_chain_keys(host: str, timeout: float = 5.0) -> list[tuple[str, str]]:
    """(common name, SPKI pin) for each certificate in the host's chain.

    The *verified* chain: what the host serves plus the trust anchor it
    resolves to here, since a phone matches pins against its evaluated chain
    and that always includes the anchor."""
    import socket
    import ssl
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            chain = tls.get_verified_chain()
    return [_spki_pin(der) for der in chain]


def _spki_pin(der: bytes) -> tuple[str, str]:
    """A DER certificate's common name and pin: the base64 SHA-256 of its DER
    SubjectPublicKeyInfo — the value the app's `spkiHash(of:)` and
    `openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64`
    produce."""
    import base64
    import hashlib

    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from cryptography.x509.oid import NameOID

    cert = x509.load_der_x509_certificate(der)
    spki = cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    name = str(names[0].value) if names else cert.subject.rfc4514_string()
    return name, base64.b64encode(hashlib.sha256(spki).digest()).decode()


def _pin_line(host: str, keys: list[tuple[str, str]]) -> str:
    """Whether any certificate the host serves carries a key the app pins."""
    matched = [name for name, key in keys if key in PINNED_SPKI_HASHES]
    if matched:
        return (f"TLS pins: the app's pins match {html.escape(', '.join(matched))} "
                f"in the {html.escape(host)} chain ✅")
    chain = " → ".join(name for name, _ in keys) or "an empty chain"
    return (f"⚠️ TLS pins: nothing in the {html.escape(host)} chain is pinned by the app "
            f"({html.escape(chain)}). Harmless while the app only reports "
            "mismatches; once it enforces pins, every request fails until an update ships.")


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


async def _device_check_line(configured: bool,
                             probe: Callable[[], Awaitable[tuple[bool | None, str]]] | None) -> str:
    """Whether reinstall protection is actually working, not merely switched on.

    Three non-empty environment variables is what `is_configured` knows, and a
    typo'd key looks identical to a healthy one from here: a wrong key cannot
    recognise a reinstall, so it silently hands every reinstall a fresh
    allowance. The probe asks Apple."""
    if not configured:
        return "DeviceCheck: NOT configured — reinstalls get a fresh allowance"
    if probe is None:
        return "DeviceCheck: configured"
    try:
        ok, detail = await asyncio.wait_for(probe(), 8)
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
    if detail.startswith(PROBE_NOT_SENT):
        # The request failed before any answer from Apple was read, so
        # nothing was rejected, and REJECTED sends the operator to the
        # developer portal. It is a False all the same — waiting will not cure
        # it — and scans make the same request through the same client, so
        # the allowance half holds as it does for a refused key.
        return (f"DeviceCheck: configured · {html.escape(detail)} — not a verdict "
                "on the key; the server log has the traceback. Scans send the "
                "same request, so reinstalls get a fresh allowance until it is "
                "fixed.")
    return (f"DeviceCheck: configured but REJECTED — {html.escape(detail)}. "
            "Reinstalls get a fresh allowance until this is fixed.")


async def _archive_chat_line(chat_id: str, get_chat: Callable[[str], Awaitable[dict | None]],
                             env: str) -> str:
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

    info = await get_chat(chat_id)
    if not info:
        # The number is probably right and only its form is wrong — the mistake
        # this line used to *cause*. Try the other form before blaming the bot's
        # membership, so the fix is the id in front of the operator.
        digits = chat_id.lstrip("-")
        others = [f for f in _negative_forms(digits) if f != chat_id]
        if digits.startswith("100"):
            others.append(f"-{digits[3:]}")
        for other in others:
            if await get_chat(other):
                return (f"Archive chat: <code>{shown}</code> not reachable, but "
                        f"<code>{other}</code> is the same chat — set "
                        f"{env} to that.")
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


async def _last_appstore_notification_line(cache: ResilientCache, key: str) -> str:
    """When `/apple/notifications` last received something that verified."""
    try:
        record = json.loads(await cache.get(key) or "null")
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


def _audit_salt_line() -> str:
    """Whether AUDIT_SALT is a secret, and never what it is.

    It keys the audit pseudonyms — the `/users` and `/subs` ids, the support id
    in the app's support mail — and, since #190, the device tags /trends keeps
    beside what was scanned. Both are unlinkable only while the salt is secret, and unset
    it falls back to a literal in this public repository. Nothing said so: the
    process booted on the default as quietly as on a real value. `main` logs
    an ERROR at startup in production too, and like it this reports the
    verdict alone, since a hash, prefix or length of a secret narrows it."""
    if auditlog.salt_is_placeholder():
        return ("Audit salt: ⚠️ placeholder — pseudonyms and trends tags can be "
                "recomputed (RUNBOOK §8)")
    return "Audit salt: set ✅"


async def text(w: Wiring) -> str:
    cache = w.cache
    lines = ["🩺 <b>Checkup</b>"]

    # Cache: reachable, and how fast.
    t0 = time.monotonic()
    try:
        health = await cache.health()
        ms = (time.monotonic() - t0) * 1000
        backend = html.escape(str(health.get("backend") or getattr(cache, "backend", "cache")))
        state = "ok" if health.get("healthy", True) else "NOT answering"
        # "Degraded" only means something when Redis is configured; an
        # unconfigured cache is memory by design, not by failure.
        if health.get("configured") and health.get("degraded"):
            state += ", degraded"
        lines.append(f"Cache ({backend}): {state} · {ms:.0f} ms")
    except Exception as exc:
        lines.append(f"Cache: error ({html.escape(type(exc).__name__)})")

    # Redis itself: what it does when full, and whether a restart loses it.
    redis_info = getattr(cache, "redis_info", None)
    try:
        redis_stats = await redis_info() if redis_info is not None else None
        if redis_stats:
            lines.append(_redis_line(redis_stats, time.time()))
    except Exception as exc:
        lines.append(f"Redis INFO: error ({html.escape(type(exc).__name__)})")

    # Model: a one-token round trip, billed like everything else.
    if w.generator is None:
        lines.append("Gemini: not wired for the bot in this process")
    else:
        t0 = time.monotonic()
        try:
            text = await w.generator(PROBE_PROMPT, PROBE_MAX_TOKENS, probe=True)
            ms = (time.monotonic() - t0) * 1000
            answered = '"ok"' in (text or "").lower()
            lines.append(f"Gemini: {'ok' if answered else 'answered oddly'} · {ms:.0f} ms")
        except Exception as exc:
            lines.append(f"Gemini: FAILED — {html.escape(_probe_reason(exc))} · a probe, "
                         "not counted against provider health")
    lines.append(await w.budget_line())

    # What the process itself knows.
    info: dict = {}
    if w.status_provider is not None:
        try:
            info = w.status_provider() or {}
        except Exception as exc:
            log.warning("status provider failed: %s", type(exc).__name__)
    if info:
        model = "healthy" if info.get("model_healthy", True) else \
            f"degraded ({html.escape(str(info.get('model_failure_kind') or 'unknown'))})"
        lines.append(f"Provider health as seen by /scan: {model}")
        if "devicecheck" in info:
            lines.append(await _device_check_line(bool(info["devicecheck"]), w.device_check_probe))
        lines.append(f"Auth: {'enforcing' if info.get('auth_enforcing') else 'NOT enforcing'} · "
                     f"build <code>{html.escape(str(info.get('commit', '?')))}</code>"
                     f"{w.replica_label(info)}")
    lines.append(_audit_salt_line())

    # TLS on the public host.
    host = _public_host()
    try:
        days = await asyncio.wait_for(asyncio.to_thread(_tls_days_left, host), 8)
        if days is None:
            lines.append(f"TLS {html.escape(host)}: certificate unreadable")
        else:
            flag = " ⚠️" if days < 14 else ""
            lines.append(f"TLS {html.escape(host)}: leaf expires in {days} days{flag} "
                         "(Let's Encrypt renews at 30)")
    except Exception as exc:
        lines.append(f"TLS {html.escape(host)}: unreachable ({html.escape(type(exc).__name__)})")
    # A second handshake rather than a wider `_tls_days_left`: that function
    # is what every checkup test stubs, and one that also carried the chain
    # would have to change all of them. Unreadable is its own line, not a ⚠️ —
    # a chain nobody could fetch says nothing about the pins.
    try:
        keys = await asyncio.wait_for(asyncio.to_thread(_tls_chain_keys, host), 8)
        lines.append(_pin_line(host, keys))
    except Exception as exc:
        lines.append(f"TLS pins: chain unreadable ({html.escape(type(exc).__name__)})")

    # App Store: can /sub ask Apple, and is Apple reaching the route that
    # withdraws refunds? Probed live, for the reason DeviceCheck is.
    lines.append(await _appstore_api_line())
    lines.append(await _last_appstore_notification_line(
        cache, w.last_appstore_notification_key))
    lines.append(await w.referral_line())

    # The archive chat, if configured: does the id resolve, and to what?
    archive_chat = os.environ.get(w.archive_chat_env, "").strip()
    if archive_chat:
        lines.append(await _archive_chat_line(archive_chat, w.get_chat,
                                              w.archive_chat_env))

    # Poll lock: is it this replica answering?
    try:
        holder = await cache.get(w.poll_lock_key)
        lines.append("Telegram poller: this replica" if holder == w.poll_token
                     else ("Telegram poller: another replica" if holder else "Telegram poller: nobody holds the lock"))
    except Exception:
        pass

    last = await w.read_int(w.last_scan_key)
    if last:
        ago = int(time.time() - last)
        lines.append(f"Last successful scan: {ago // 60} min ago" if ago < 7200 else
                     f"Last successful scan: {ago // 3600}h ago")
    return "\n".join(lines)
