"""Rate limiting.

The limiter is the only thing standing between an unauthenticated caller and an
unbounded third-party AI bill, so it has two properties that matter more than
raw throughput:

  * **It survives a restart.** The previous in-process implementation reset
    every counter on deploy — and this service deploys on every push to main.
  * **It survives horizontal scale.** Per-process state silently multiplies the
    effective limit by the replica count.

Redis provides both. When Redis is unreachable the limiter degrades to the
in-process behaviour rather than failing open *or* failing closed: a cache
outage should not take the product down, and it should not remove all limits
either. The degraded mode is announced loudly in the logs.

The sliding window is evaluated in a Lua script so the check-and-increment is
atomic; a naive GET/INCR pair races under concurrency and lets callers exceed
the limit by roughly the number of in-flight requests.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from collections import defaultdict
from typing import Any, Protocol


def client_ip(request: Any) -> str:
    """Best-effort source IP used as the rate-limit backstop.

    Always the **rightmost** `X-Forwarded-For` hop when the header is present.

    The container runs uvicorn with `--forwarded-allow-ips='*'`, which makes
    `request.client.host` the *leftmost* — i.e. entirely client-supplied — hop.
    Keying a limiter on that does not collapse everyone into one bucket, as is
    the usual worry; it hands the caller a fresh bucket per request, which is
    no limit at all.

    The rightmost entry is the one appended by the proxy nearest to us, the
    only hop a caller cannot forge by sending their own header. Truncated
    because the value reaches a cache key and is attacker-influenced.

    That proxy is Railway's edge, and the address it appends is the client's
    own: there is no CDN in front of it (checked 2026-09: the domain is a
    plain CNAME to `*.up.railway.app`, and responses carry Railway's `server`
    and `x-railway-edge` headers and nobody else's). So an ordinary request
    carries exactly one hop, and it is the caller. Put a CDN or any other
    proxy in front and the rightmost hop becomes *that proxy's* address — one
    60/h bucket for every user. Before doing so, take the hop a configured
    number of places from the right instead; RUNBOOK §5.8 says the same.

    It lives here, rather than in `main`, because `auth`'s unauthenticated
    routes need the same answer and cannot import `main`. They were keyed on
    `request.client.host` — the forgeable value — so `/challenge`, `/attest`
    and `/assert` had a limiter that any caller could step around by rotating
    one header, while `/scan`, `/trends` and `/listing` were keyed correctly.
    Two implementations was the whole bug.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        hops = xff.split(",")
        _note_hop_count(len(hops))
        return hops[-1].strip()[:64] or "unknown"
    return request.client.host if request.client else "unknown"


_HOP_COUNTS_SEEN: set[int] = set()


def _note_hop_count(hops: int) -> None:
    """Log how many forwarded hops a request carried, once per count.

    The evidence for the paragraph above, from production rather than from a
    header check: ordinary app traffic should read 1. Once per distinct count
    rather than once, so a caller forging its own header on the first request
    cannot be the only sample; bucketed at 4 so that is at most four lines per
    process. The count only — never an address.
    """
    bucket = min(hops, 4)
    if bucket not in _HOP_COUNTS_SEEN:
        _HOP_COUNTS_SEEN.add(bucket)
        log.info("x-forwarded-for carried %s hop(s); the rightmost is the "
                 "per-IP rate-limit key", f"{bucket}+" if bucket == 4 else bucket)

log = logging.getLogger("snapworth.ratelimit")

RATE_WINDOW_SECS = 3600

# Per client-supplied device id. Best-effort only — the header is trivially
# rotated, so this shapes honest traffic rather than stopping abuse.
#
# One bucket per route, not one shared by all three. /scan, /listing and
# /trends used to spend the same 20 an hour, so a reseller who scanned an item
# and drafted its listing was stopped after about ten items — on a plan sold as
# "Unlimited scans" — and every visit to My Finds that fetched /trends spent a
# scan. `main._enforce_limits` names the bucket; these are the sizes.
#
# The scan bucket, for the free tier. Its daily allowance (quota.py) is what
# actually bounds a free user; this bounds the requests around it.
RATE_MAX_REQUESTS = int(os.environ.get("RATE_MAX_REQUESTS", "20"))

# The scan bucket for Pro — the fair-use ceiling on "Unlimited scans". Sized
# against cost rather than usage: at ~$0.006 a scan (RUNBOOK §10) a full hour
# is ~$0.36, while $39.99 a year is about $0.11 a day before Apple's cut. So
# this does not make a heavy day pay for itself — the daily spend alert is
# what watches that — it stops one device, or one leaked token, from running
# up an unbounded bill in an hour. It is not above the per-IP cap below, which
# every route shares: from one address, scans, drafts and the trends card
# together stop at the IP cap first.
PRO_SCAN_RATE_MAX_REQUESTS = int(os.environ.get("PRO_SCAN_RATE_MAX_REQUESTS", "60"))

# /listing, which is Pro-only. Sized at the per-IP cap below, not under it,
# so that from one address the IP bucket refuses first, and it refuses scans
# and drafts together. Every build of the app assumes that. It says "You've hit
# the scan limit." for a draft's 429 as for a scan's, and Haul pauses both of
# its queues on either one's (HaulSession.swift). At 20, the size /listing had
# while it shared the scan bucket, a Pro user's 21st draft of the hour was
# refused with scans to spare. The app then said "scan limit" and stopped
# Haul's scanning. So keep this at or above IP_RATE_MAX_REQUESTS. It adds
# nothing to what one address can spend: the IP cap bounds that at 60
# requests an hour, whatever the mix of scans and drafts. A device that moves
# between addresses can reach 60 scans and 60 drafts in an hour.
LISTING_RATE_MAX_REQUESTS = int(os.environ.get("LISTING_RATE_MAX_REQUESTS", "60"))

# /trends costs no model call: it reads tallies the server caches for 15
# minutes, and the app caches the answer for 30. So the per-device cap here is
# only a loop-breaker for a misbehaving build, far above the two or so fetches
# an hour the app makes. The IP cap still applies to it.
TRENDS_RATE_MAX_REQUESTS = int(os.environ.get("TRENDS_RATE_MAX_REQUESTS", "60"))

# Per source IP — the real backstop. Set higher than the free scan cap so
# shared egress (carrier NAT, office wifi) doesn't punish legitimate users.
# One bucket for every route that has a limit, authenticated or not.
IP_RATE_MAX_REQUESTS = int(os.environ.get("IP_RATE_MAX_REQUESTS", "60"))


class RateLimitExceeded(Exception):
    """Raised when a caller is over its limit. Carries a user-safe message."""

    def __init__(self, message: str, retry_after: int = 60) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class RateLimiter(Protocol):
    """Allows one request against `key`, or raises `RateLimitExceeded`."""

    async def check(self, key: str, limit: int, window: int = RATE_WINDOW_SECS) -> None: ...


# ── In-memory (fallback / single-instance) ───────────────────────────────────

class InMemoryRateLimiter:
    """Sliding window in process memory.

    Correct for a single instance; wrong for several. Retained as the fallback
    path and for tests, which need synchronous introspection of the store.
    """

    def __init__(self) -> None:
        self.store: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()

    def _prune(self, now: float) -> None:
        # Bound memory growth: drop keys whose newest entry is outside the window.
        if now - self._last_cleanup <= 600:
            return
        stale = [k for k, v in self.store.items() if not v or now - max(v) > RATE_WINDOW_SECS]
        for k in stale:
            del self.store[k]
        self._last_cleanup = now

    def check_sync(self, key: str, limit: int, window: int = RATE_WINDOW_SECS) -> None:
        now = time.time()
        self._prune(now)
        timestamps = self.store[key]
        timestamps[:] = [t for t in timestamps if now - t < window]
        if len(timestamps) >= limit:
            oldest = min(timestamps) if timestamps else now
            raise RateLimitExceeded(
                f"Rate limit: {limit} requests/hour.",
                retry_after=max(1, int(window - (now - oldest))),
            )
        timestamps.append(now)

    async def check(self, key: str, limit: int, window: int = RATE_WINDOW_SECS) -> None:
        self.check_sync(key, limit, window)


# ── Redis (distributed) ──────────────────────────────────────────────────────

# Atomic sliding window over a sorted set. Returns 1 when allowed, else the
# number of seconds until the oldest entry falls out of the window.
_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local now_ms = tonumber(ARGV[1])
local win_ms = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - win_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = 1
  if oldest[2] then
    retry = math.ceil((tonumber(oldest[2]) + win_ms - now_ms) / 1000)
    if retry < 1 then retry = 1 end
  end
  return -retry
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, win_ms)
return 1
"""


class RedisRateLimiter:
    """Sliding-window limiter backed by Redis sorted sets."""

    def __init__(self, client) -> None:
        self._redis = client
        self._script = client.register_script(_SLIDING_WINDOW_LUA)
        self._counter = 0
        # Per-instance, random, and the only part of the member that is
        # actually distinct between replicas. `os.getpid()` was doing this job
        # and could not: the container runs `uvicorn --workers 1`, so uvicorn
        # is PID 1 in *every* replica and `os.getpid()` returns 1 everywhere.
        # `_counter` starts at 0 in each replica too, so two replicas walk
        # through identical member strings.
        #
        # ZADD of a member that already exists updates its score instead of
        # adding a second entry, so ZCARD stays flat and one request goes
        # uncounted — the limit quietly rises. This module's docstring names
        # surviving horizontal scale as one of its two reasons to exist, and
        # this was the one shared-state detail that did not. Latent at one
        # replica; live the moment there are two.
        #
        # The most exposed key is the busiest `rl:ip:<addr>`, a carrier NAT's
        # shared egress. (This used to say every user shared one IP key
        # because the rightmost forwarded hop was Railway's proxy. It is the
        # client's own address — see `client_ip`.)
        self._nonce = secrets.token_hex(4)

    async def check(self, key: str, limit: int, window: int = RATE_WINDOW_SECS) -> None:
        now_ms = int(time.time() * 1000)
        # Unique member per request. The member's only requirement is
        # uniqueness — nothing reads it back — so the nonce carries
        # cross-replica distinctness and the counter same-millisecond ordering
        # within one.
        self._counter += 1
        member = f"{now_ms}-{self._nonce}-{self._counter}"
        result = await self._script(
            keys=[f"rl:{key}"],
            args=[now_ms, window * 1000, limit, member],
        )
        if int(result) < 0:
            raise RateLimitExceeded(
                f"Rate limit: {limit} requests/hour.",
                retry_after=abs(int(result)),
            )


# ── Resilient facade ─────────────────────────────────────────────────────────

class ResilientRateLimiter:
    """Uses Redis when healthy; degrades to in-process on connection failure.

    A Redis outage must not take the product down (fail-open on *availability*)
    but must not silently remove limits either — so we fall back to the local
    limiter, which still caps a single instance, and log at ERROR so the
    degradation is visible in monitoring.
    """

    def __init__(self, primary: RateLimiter | None, fallback: InMemoryRateLimiter) -> None:
        self._primary = primary
        self._fallback = fallback
        self._degraded_since: float | None = None

    @property
    def is_degraded(self) -> bool:
        return self._primary is None or self._degraded_since is not None

    async def check(self, key: str, limit: int, window: int = RATE_WINDOW_SECS) -> None:
        if self._primary is not None:
            try:
                await self._primary.check(key, limit, window)
                if self._degraded_since is not None:
                    log.info("redis rate limiter recovered")
                    self._degraded_since = None
                return
            except RateLimitExceeded:
                raise                      # a real limit hit, not an outage
            except Exception as exc:
                if self._degraded_since is None:
                    self._degraded_since = time.time()
                    log.error(
                        "redis unavailable, DEGRADING to in-process rate limits "
                        "(limits are now per-replica): %s", exc
                    )
        await self._fallback.check(key, limit, window)


async def build_limiter() -> tuple[ResilientRateLimiter, InMemoryRateLimiter]:
    """Construct the limiter from the environment.

    Returns the facade plus the in-memory instance, so callers (and tests) can
    inspect local state directly.
    """
    fallback = InMemoryRateLimiter()
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        log.warning(
            "REDIS_URL not set — rate limits are per-process and reset on deploy. "
            "Set REDIS_URL before running more than one replica."
        )
        return ResilientRateLimiter(None, fallback), fallback

    try:
        import redis.asyncio as aioredis
    except ImportError:
        log.error("REDIS_URL is set but the redis package is not installed")
        return ResilientRateLimiter(None, fallback), fallback

    try:
        client = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    except Exception as exc:
        log.error("redis client could not be constructed, using in-process limits: %s", exc)
        return ResilientRateLimiter(None, fallback), fallback

    try:
        await client.ping()
        log.info("redis rate limiter connected")
    except Exception as exc:
        # Keep the client. redis-py's pool reconnects transparently, so a
        # startup blip must not permanently downgrade the process — discarding
        # it here turned a five-second outage into per-process limits for the
        # life of the replica, resetting on every restart. `cache.py:289-298`
        # deliberately does exactly this for the same situation; these two
        # now agree.
        log.error("redis rate limiter ping failed at startup, will retry on demand: %s", exc)
    return ResilientRateLimiter(RedisRateLimiter(client), fallback), fallback
