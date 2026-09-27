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

import ipaddress
import logging
import os
import secrets
import time
from collections import defaultdict
from typing import Any, Protocol


def client_ip(request: Any) -> str:
    """Best-effort source IP used as the rate-limit backstop.

    The nearest `X-Forwarded-For` hop that is not a known proxy: walk the
    header from the right, skip Fastly's edge addresses and internal ones
    (`_KNOWN_PROXIES`), and key on the first hop that is neither — an IPv4
    address as itself, an IPv6 one as its /64 (`_bucket_of`).

    The container runs uvicorn with `--forwarded-allow-ips='*'`, which makes
    `request.client.host` the *leftmost* — i.e. entirely client-supplied — hop.
    Keying a limiter on that does not collapse everyone into one bucket, as is
    the usual worry; it hands the caller a fresh bucket per request, which is
    no limit at all.

    Each proxy appends on the right, so a caller can write only the left of
    the header, and Railway appends the address it saw to the right of
    whatever the caller sent. Walking from the right and stopping at the first
    hop that is not a proxy therefore stops at that address and never reaches
    a forged one.

    This took the rightmost hop until 2026-09-27, on the reading that an
    ordinary request carries one hop and it is the caller. That had stopped
    being true. Since Railway's CDN rollout (~Feb 2026), a request it routes
    through Fastly arrives as "client, Fastly edge", and one it does not
    arrives as "client"; Railway staff say both paths occur, and to take the
    first entry:
    https://station.railway.com/questions/which-header-should-i-rely-on-for-real-c-d78a6f96
    Production agreed: `_note_hop_count` logged "carried 2 hop(s)" for app
    traffic on /auth, /scan and /trends, for Apple's notification POSTs and
    for a scanner, and never 1. Two probes of POST /auth/challenge on
    2026-09-27 both arrived with two hops whose leftmost was the prober's own
    address, and one of them had sent `X-Forwarded-For: 203.0.113.7`, which
    Railway's edge dropped. So the key was a Fastly edge address, and every
    user routed through one Fastly POP shared one 60/h bucket.

    Not the leftmost hop, although that is what Railway suggests. It is the
    caller only while Railway's edge strips a client-supplied header, which
    was seen on the one path probed; were it ever passed through, the key
    would be the caller's choice again (the fresh-bucket bug above). The walk
    gives the same answer as the leftmost while stripping holds and the
    Fastly list is current, and fails safe if either stops: a forged hop is
    never reached, and an unlisted edge makes the limit stricter, not looser.
    Not a fixed count from the right either: the two paths carry different
    counts. Not `X-Real-IP`: on the CDN path it holds Fastly's address (a
    Railway bug, per the same thread).

    The walk passes only hops it recognises as proxies. The nearest hop that
    is not one but is not an address either stops it, and every such request
    shares the one key `_UNPARSEABLE`. Skipping it instead, as this did at
    first, walked on into the caller's end of the header. If every hop is a
    known proxy, the leftmost. If the header is absent or holds no entry,
    `request.client.host`, else "unknown" — never with a header present:
    uvicorn rewrites that host to the leftmost entry, the caller's own.
    Truncated because the value reaches a cache key and is
    attacker-influenced.

    It lives here, rather than in `main`, because `auth`'s unauthenticated
    routes need the same answer and cannot import `main`. They were keyed on
    `request.client.host` — the forgeable value — so `/challenge`, `/attest`
    and `/assert` had a limiter that any caller could step around by rotating
    one header, while `/scan`, `/trends` and `/listing` were keyed correctly.
    Two implementations was the whole bug.
    """
    # Every line, joined in order: a header sent as several lines means their
    # join (RFC 9110 §5.3), and uvicorn's proxy-header middleware reads it so.
    # `headers.get` returns the first line alone, which let a caller's own
    # line stand in for the whole header wherever a proxy adds its hop as a
    # separate line.
    xff = ",".join(request.headers.getlist("x-forwarded-for"))
    hops = [hop.strip() for hop in xff.split(",") if hop.strip()]
    if hops:
        key, skipped, source = _nearest_client_hop(hops)
        _note_hop_count(len(hops), skipped, source)
        return key[:64]
    # No header, or one with no entry in it: nothing a caller wrote, and
    # uvicorn leaves the socket peer in place for an empty header.
    host = request.client.host if request.client else ""
    return host[:64] or "unknown"


# Fastly's published edge ranges, which Railway's CDN appends after the
# client. Fetched 2026-09-27 from https://api.fastly.com/public-ip-list
# (`addresses`, then `ipv6_addresses`, verbatim). Fastly changes them rarely.
# A range missing here is keyed on as a client, which puts the users behind
# that edge in one bucket again: too strict, never too loose. Two-hop traffic
# then logs "skipped 0"; RUNBOOK §5.8 has the refresh.
_FASTLY_EDGE_RANGES = (
    "23.235.32.0/20", "43.249.72.0/22", "103.244.50.0/24", "103.245.222.0/23",
    "103.245.224.0/24", "104.156.80.0/20", "140.248.64.0/18", "140.248.128.0/17",
    "146.75.0.0/17", "151.101.0.0/16", "157.52.64.0/18", "167.82.0.0/17",
    "167.82.128.0/20", "167.82.160.0/20", "167.82.224.0/20", "172.111.64.0/18",
    "185.31.16.0/22", "199.27.72.0/21", "199.232.0.0/16",
    "2a04:4e40::/32", "2a04:4e42::/32",
)

# Addresses no caller on the public internet connects from, so a hop holding
# one was written by infrastructure: RFC 1918, CGNAT (RFC 6598), loopback and
# link-local, and IPv6's loopback, unique-local and link-local. Listed rather
# than read off `is_global`, which is false for the documentation ranges too
# and whose exact set has changed between Python releases.
_INTERNAL_RANGES = (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
    "127.0.0.0/8", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10",
)

_KNOWN_PROXIES = tuple(ipaddress.ip_network(r)
                       for r in _FASTLY_EDGE_RANGES + _INTERNAL_RANGES)

_Address = ipaddress.IPv4Address | ipaddress.IPv6Address


# The key for every request whose nearest hop that is not a known proxy is
# not an address. One value, so that a caller who can put such a hop there
# shares a bucket with every other one instead of choosing a fresh one.
_UNPARSEABLE = "unparseable"


def _parse_hop(hop: str) -> _Address | None:
    try:
        return ipaddress.ip_address(hop)
    except ValueError:
        return None


def _is_known_proxy(addr: _Address) -> bool:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return any(addr in network for network in _KNOWN_PROXIES)


def _bucket_of(addr: _Address) -> str:
    """The key an address is limited under: IPv4 per address, IPv6 per /64.

    A /64 is what one line is given — a home router's LAN, a phone on
    cellular, a VPS — and any address in it is the holder's to use; iOS
    rotates temporary addresses inside it on its own. Keyed per address, one
    IPv6 line was 2^64 fresh buckets. Keying on the network also drops a scope
    id, which `ipaddress` accepts at any length and which rotated the key too,
    and an IPv4-mapped address keys with the IPv4 one it carries.
    """
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network((int(addr), 64), strict=False))
    return str(addr)


def _nearest_client_hop(hops: list[str]) -> tuple[str, int, str]:
    """The key, how many known proxies were skipped to reach it, and where it
    came from — the last in words with no address in them, for the note.

    `hops` are the header's non-empty entries, stripped. The walk passes only
    hops it recognises as proxies: one that is not an address stops it, and
    is keyed as `_UNPARSEABLE`, never skipped, since everything to its left
    is further from us and so no more trustworthy."""
    skipped = 0
    addrs: list[_Address] = []
    for hop in reversed(hops):
        addr = _parse_hop(hop)
        if addr is None:
            return (_UNPARSEABLE, skipped,
                    "a fixed one, as the nearest hop that is not one is not an address")
        if not _is_known_proxy(addr):
            return _bucket_of(addr), skipped, "the nearest hop that is not one"
        skipped += 1
        addrs.append(addr)
    return _bucket_of(addrs[-1]), skipped, "the leftmost address, as every address is one"


_HOP_COUNTS_SEEN: set[tuple[int, int]] = set()
_HOP_NOTE_LIMIT = 6


def _note_hop_count(hops: int, skipped: int, source: str) -> None:
    """Log what the header carried and what the walk skipped, once per pair.

    The evidence for `client_ip`, from production rather than from a probe.
    CDN traffic should read "carried 2 hop(s), skipped 1" and the rest
    "carried 1 hop(s), skipped 0". "carried 2 hop(s), skipped 0"
    means the rightmost hop was not recognised, most likely a new Fastly
    range. Once per distinct (hops, skipped) pair, each bucketed at 4, rather
    than once, so a first request with an odd header is not the only sample;
    at most `_HOP_NOTE_LIMIT` lines per process. Counts and fixed words only
    — never an address.
    """
    pair = (min(hops, 4), min(skipped, 4))
    if pair in _HOP_COUNTS_SEEN or len(_HOP_COUNTS_SEEN) >= _HOP_NOTE_LIMIT:
        return
    _HOP_COUNTS_SEEN.add(pair)
    hops_seen, skipped_seen = (f"{n}+" if n == 4 else str(n) for n in pair)
    log.info("x-forwarded-for carried %s hop(s), skipped %s known proxy "
             "hop(s) (Fastly edge or internal); the per-IP key is %s",
             hops_seen, skipped_seen, source)

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
        # because the rightmost forwarded hop was Railway's proxy, and was
        # then corrected to say that hop was the client. Neither held: on
        # Railway's CDN path the rightmost hop was a Fastly edge, one key for
        # everyone routed through it, until `client_ip` began skipping known
        # proxies on 2026-09-27.)
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
