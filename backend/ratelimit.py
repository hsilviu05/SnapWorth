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
from typing import Any, NamedTuple, Protocol


def client_ip(request: Any) -> str:
    """Best-effort source IP used as the rate-limit backstop.

    The first `X-Forwarded-For` entry, because Railway's edge writes the
    whole header: it drops whatever a client sent and writes "client" or
    "client, edge". An IPv4 address keys as itself, an IPv6 one as its /64
    (`_bucket_of`), and a first entry that is not an address as the one
    fixed key `_UNPARSEABLE`. A header with more entries than Railway writes
    (`_RAILWAY_MAX_HOPS`) means that stopped being true, and is keyed by the
    walk from the right instead (`_nearest_client_hop`), with a warning.

    Until 2026-09-27 this took the rightmost entry, and then, from #250, the
    nearest entry that is not a Fastly or internal address. Both keyed on
    Railway's edge. Once #250 was deployed, a probe of POST /auth/challenge
    logged "carried 2 hop(s), skipped 0" with the key's hop in
    95.173.0.0/16, and uvicorn's access log, which prints the leftmost
    entry, showed the prober's own address. So the second entry is
    Railway's edge or POP, not Fastly, and every user behind it shared one
    60/h bucket, which a launch would trip. 95.173.0.0/16 is split among
    many holders (CDN77 and Speedbone among them), so it cannot be listed
    as a proxy either. Forged probes that day, a single
    "X-Forwarded-For: 203.0.113.7" and "198.51.100.9, 203.0.113.8" sent
    with "Forwarded: for=192.0.2.60" and "X-Real-IP: 192.0.2.61", all
    arrived with the prober's real address leftmost, and every production
    request, from the app, Apple and bots, carried exactly two entries. The
    service's *.up.railway.app domain answers 404, so no public path skips
    the edge. Railway staff give the same rule, "use X-Forwarded-For and
    take the first IP":
    https://station.railway.com/questions/which-header-should-i-rely-on-for-real-c-d78a6f96
    Not `X-Real-IP`: on the CDN path it holds the CDN's address.

    The residual risk: the first entry is the caller only while Railway
    strips. If it ever stops while still sending exactly two entries, the
    first is whatever the caller wrote, so a caller chooses its key and gets
    a fresh 60/h bucket for every value, and nothing in the header tells
    that request from a real one. App Attest where it is required, the
    per-device buckets and the daily spend alert once it is set still apply
    then; this limit does not. Stripping was
    seen for callers connecting directly, and a request arriving from
    another CDN's addresses rests on the same assumption. RUNBOOK §5.8 has
    the probe that checks it, to run after any Railway networking change
    and monthly.

    Three or more entries: stripping failed or the topology changed, and the
    walk is the conservative answer. It skips Fastly's published ranges and
    internal ones from the right and keys on the first hop that is neither,
    so it never reaches an entry left of the first address it does not
    know. Railway's edge is on neither list, so on Railway's current shape
    that is the edge: one shared bucket again, but not a key any caller
    chooses. `_warn_extra_hops` says so, once per process.

    Every line of the header is read, joined in order; two lines of one
    entry each are two entries. `request.client.host` only when the header
    is absent or holds no entry, where uvicorn leaves the socket peer in
    place; else "unknown". With a header present uvicorn
    (`--forwarded-allow-ips='*'`) has rewritten that host to the leftmost
    entry verbatim: not normalised, and not the walk's answer on three.
    Truncated because the value reaches a cache key and is
    attacker-influenced.

    It lives here, rather than in `main`, because `auth`'s unauthenticated
    routes and `referral` need the same answer and cannot import `main`.
    `auth` was once keyed on `request.client.host` while the routes in
    `main` used a helper `auth` could not reach. Two implementations was
    the whole bug.
    """
    # Every line, joined in order: a header sent as several lines means their
    # join (RFC 9110 §5.3), and uvicorn's proxy-header middleware reads it so.
    # `headers.get` returns the first line alone, which would hide the entries
    # a proxy added as a line of its own, and with them a third entry.
    xff = ",".join(request.headers.getlist("x-forwarded-for"))
    hops = [hop.strip() for hop in xff.split(",") if hop.strip()]
    if not hops:
        # No header, or one with no entry in it: nothing a caller wrote, and
        # uvicorn leaves the socket peer in place for an empty header.
        host = request.client.host if request.client else ""
        return host[:64] or "unknown"
    if len(hops) > _RAILWAY_MAX_HOPS:
        walk = _nearest_client_hop(hops)
        _warn_extra_hops(len(hops), walk)
        return walk.key[:64]
    first = _parse_hop(hops[0])
    _note_hop_count(len(hops), first is not None)
    return (_UNPARSEABLE if first is None else _bucket_of(first))[:64]


# The most entries Railway's edge writes: "client", or "client, edge". It
# drops whatever the client sent, so a caller cannot add one while that
# holds; a third means it no longer does, or something new sits in the path.
_RAILWAY_MAX_HOPS = 2

# Fastly's published edge ranges. Fetched 2026-09-27 from
# https://api.fastly.com/public-ip-list (`addresses`, then `ipv6_addresses`,
# verbatim). #250 took these for the second entry on Railway's CDN path,
# which production showed is Railway's own edge (95.173.0.0/16) instead. Read
# only by the walk, for a header of three or more entries, where a Fastly
# hop on the right is still a proxy rather than a caller.
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


# The key for every request whose deciding entry is not an address. One
# value, so that a caller who can put such an entry there shares a bucket
# with every other one instead of choosing a fresh one.
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


class _Walk(NamedTuple):
    key: str
    # How many known proxies were skipped.
    skipped: int
    # Where the key came from, in words with no address in them, for the
    # warning.
    source: str


def _nearest_client_hop(hops: list[str]) -> _Walk:
    """Walk `hops`, the header's non-empty entries, stripped, from the right.

    The fallback for a header with more entries than Railway writes. It
    passes only hops it recognises as proxies: one that is not an address
    stops it, and is keyed as `_UNPARSEABLE`, never skipped, since
    everything to its left is further from us and so no more trustworthy."""
    skipped = 0
    addrs: list[_Address] = []
    for hop in reversed(hops):
        addr = _parse_hop(hop)
        if addr is None:
            return _Walk(_UNPARSEABLE, skipped,
                         "a fixed one, as the nearest hop that is not a known "
                         "proxy is not an address")
        if not _is_known_proxy(addr):
            return _Walk(_bucket_of(addr), skipped,
                         "the nearest hop that is not a known proxy")
        skipped += 1
        addrs.append(addr)
    return _Walk(_bucket_of(addrs[-1]), skipped,
                 "the leftmost address, as every hop is a known proxy")


# (entry count, whether the first entry is an address) pairs already noted.
# Only headers of one or two entries are noted, so it holds at most four.
_HOP_COUNTS_SEEN: set[tuple[int, bool]] = set()

# Whether this process has warned about a header of three or more entries.
_EXTRA_HOPS_WARNED = False


def _note_hop_count(hops: int, first_is_address: bool) -> None:
    """Log how many entries the header carried and what the key is.

    The evidence for `client_ip`, from production rather than from a probe.
    Every request on 2026-09-27 carried two entries, so after a deploy the
    line to find is "x-forwarded-for carried 2 hop(s); the per-IP key is the
    first entry (Railway strips client values)" (RUNBOOK §5.8). Once per
    distinct (entry count, first entry an address) pair, for the first
    request that has it, so at most four lines per process. Counts and fixed
    words, never an address. Three or more entries is `_warn_extra_hops`.
    """
    pair = (hops, first_is_address)
    if pair in _HOP_COUNTS_SEEN:
        return
    _HOP_COUNTS_SEEN.add(pair)
    key = ("the first entry (Railway strips client values)" if first_is_address
           else "a fixed one, as the first entry is not an address")
    log.info("x-forwarded-for carried %d hop(s); the per-IP key is %s", hops, key)


def _warn_extra_hops(hops: int, walk: _Walk) -> None:
    """Warn, once per process, about a header with more than two entries.

    While Railway strips a client's header no caller can send one, so the
    warning is never a scanner's noise: stripping failed, or a proxy now
    sits in front of or behind Railway's edge. Once per process, so the
    first such request is the only sample; the owner probe in RUNBOOK §5.8
    finds out which. Counts and fixed words only, never an address.
    """
    global _EXTRA_HOPS_WARNED
    if _EXTRA_HOPS_WARNED:
        return
    _EXTRA_HOPS_WARNED = True
    log.warning(
        "x-forwarded-for carried %d hop(s), more than the %d Railway writes: "
        "its stripping failed or the topology changed. Such requests are "
        "keyed by the walk from the right, which skipped %d known proxy "
        "hop(s) (Fastly edge or internal); the per-IP key is %s. Once per "
        "process (RUNBOOK §5.8)",
        hops, _RAILWAY_MAX_HOPS, walk.skipped, walk.source)


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
        # then corrected to say that hop was the client. Neither held: the
        # rightmost hop was Railway's edge, one key for everyone routed
        # through it, until `client_ip` began keying on the first entry,
        # which Railway writes, on 2026-09-27.)
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
