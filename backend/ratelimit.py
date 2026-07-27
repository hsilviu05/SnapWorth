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
import time
from collections import defaultdict
from typing import Protocol

log = logging.getLogger("snapworth.ratelimit")

RATE_WINDOW_SECS = 3600

# Per client-supplied device id. Best-effort only — the header is trivially
# rotated, so this shapes honest traffic rather than stopping abuse.
RATE_MAX_REQUESTS = int(os.environ.get("RATE_MAX_REQUESTS", "20"))

# Per source IP — the real backstop. Set higher than the device cap so shared
# egress (carrier NAT, office wifi) doesn't punish legitimate users.
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

    async def check(self, key: str, limit: int, window: int = RATE_WINDOW_SECS) -> None:
        now_ms = int(time.time() * 1000)
        # Unique member per request; the counter disambiguates same-millisecond
        # calls within a process, which a bare timestamp would collapse.
        self._counter += 1
        member = f"{now_ms}-{os.getpid()}-{self._counter}"
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
        await client.ping()
        log.info("redis rate limiter connected")
        return ResilientRateLimiter(RedisRateLimiter(client), fallback), fallback
    except Exception as exc:
        log.error("redis connection failed at startup, using in-process limits: %s", exc)
        return ResilientRateLimiter(None, fallback), fallback
