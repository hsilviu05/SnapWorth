"""Shared key-value cache.

Rate limits, entitlements, free-scan quota, attestation state and replay nonces
all need state that outlives a single process and is shared across replicas.
This is the one abstraction they go through.

The interface is deliberately narrow (get/set/incr/delete/add) so the Redis and
in-memory implementations cannot drift, and so callers can be tested without a
server running.

**Failure policy is per-call, not global.** Some state may safely fall back to
per-process (rate limits degrade to per-replica). Other state must *not* silently
succeed on failure — a quota check that fails open is a free-scan bypass. Callers
declare which they are via `required=`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Protocol

log = logging.getLogger("snapworth.cache")


class CacheUnavailable(Exception):
    """The backing store could not serve a call that requires durability."""


class KeyValueStore(Protocol):
    """The three operations most callers actually need.

    `Cache` describes a full backend (add/incr/ping included) and is the right
    type for something implementing one. Callers that only read and write —
    auth, comps — should depend on this instead: `ResilientCache` deliberately
    does not implement `ping`, so requiring the full protocol rejects the very
    object the application wires in.
    """

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str,
                  ttl: int | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    async def add(self, key: str, value: str, ttl: int | None = None) -> bool: ...
    async def incr(self, key: str, ttl: int | None = None, amount: int = 1) -> int: ...
    async def delete(self, key: str) -> None: ...
    async def ping(self) -> bool: ...


class InMemoryCache:
    """Process-local cache. Correct for one instance; a fallback everywhere else."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}
        self._lock = asyncio.Lock()

    def _live(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires = entry
        if expires is not None and expires < time.time():
            self._data.pop(key, None)
            return None
        return value

    async def get(self, key: str) -> str | None:
        async with self._lock:
            return self._live(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        async with self._lock:
            self._data[key] = (value, time.time() + ttl if ttl else None)

    async def add(self, key: str, value: str, ttl: int | None = None) -> bool:
        """Set only if absent. Returns True when this call created the key."""
        async with self._lock:
            if self._live(key) is not None:
                return False
            self._data[key] = (value, time.time() + ttl if ttl else None)
            return True

    async def incr(self, key: str, ttl: int | None = None, amount: int = 1) -> int:
        async with self._lock:
            current = self._live(key)
            nxt = int(current or 0) + amount
            # Preserve the original expiry so a counter can't be extended
            # indefinitely by continued use.
            existing_expiry = self._data.get(key, (None, None))[1]
            expiry = existing_expiry if current is not None else (
                time.time() + ttl if ttl else None
            )
            self._data[key] = (str(nxt), expiry)
            return nxt

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def ping(self) -> bool:
        return True


class RedisCache:
    """Redis-backed cache with pooling and bounded timeouts."""

    def __init__(self, client) -> None:
        self._redis = client

    async def get(self, key: str) -> str | None:
        return await self._redis.get(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        await self._redis.set(key, value, ex=ttl)

    async def add(self, key: str, value: str, ttl: int | None = None) -> bool:
        return bool(await self._redis.set(key, value, ex=ttl, nx=True))

    async def incr(self, key: str, ttl: int | None = None, amount: int = 1) -> int:
        # Pipeline so INCR and EXPIRE are one round-trip. NX on the expire means
        # the window is anchored to first use, not extended on every hit.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incrby(key, amount)
            if ttl:
                pipe.expire(key, ttl, nx=True)
            results = await pipe.execute()
        return int(results[0])

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def ping(self) -> bool:
        return bool(await self._redis.ping())


class ResilientCache:
    """Prefers Redis; degrades to process memory, and reports health.

    `required=True` calls raise `CacheUnavailable` instead of silently using the
    local fallback. Anything that gates paid resources must pass it — a
    quota check that fails open is worse than one that fails closed.

    **`configured` records operator intent, not reachability.** Those are
    different states and conflating them is a fail-open bug: an operator who ran
    a single instance without Redis has made the in-process store authoritative,
    but an operator who set `REDIS_URL` and hit a transient outage has *not*.
    Only the first may satisfy a `required` call from memory.
    """

    def __init__(self, primary: Cache | None, fallback: InMemoryCache,
                 *, configured: bool = False) -> None:
        self._primary = primary
        self._fallback = fallback
        self._configured = configured or primary is not None
        self._degraded_since: float | None = None
        self._failures = 0

    @property
    def is_degraded(self) -> bool:
        return self._primary is None or self._degraded_since is not None

    @property
    def is_configured(self) -> bool:
        """True when a durable backend was *intended*, reachable or not."""
        return self._configured

    @property
    def backend(self) -> str:
        if self._primary is None:
            return "redis-unavailable" if self._configured else "memory"
        return "redis-degraded" if self._degraded_since else "redis"

    def _mark_down(self, exc: Exception) -> None:
        self._failures += 1
        if self._degraded_since is None:
            self._degraded_since = time.time()
            log.error("cache unavailable, degrading to in-process: %s", exc)

    def _mark_up(self) -> None:
        if self._degraded_since is not None:
            log.info("cache recovered after %.0fs", time.time() - self._degraded_since)
            self._degraded_since = None
            self._failures = 0

    async def _call(self, method: str, *args, required: bool = False, **kwargs) -> Any:
        if self._primary is not None:
            try:
                result = await getattr(self._primary, method)(*args, **kwargs)
                self._mark_up()
                return result
            except Exception as exc:
                self._mark_down(exc)
                # A *configured* backend that is failing means we may be one of
                # several replicas whose local state is not authoritative, so a
                # `required` call must fail closed rather than trust memory.
                if required:
                    raise CacheUnavailable(str(exc)) from exc
        elif self._configured and required:
            # A durable backend was configured but never connected (startup ping
            # failed, or the client could not be built). Process memory is NOT
            # authoritative here — other replicas hold their own copies — so a
            # call that gates a paid resource must fail closed.
            raise CacheUnavailable("durable cache is configured but unavailable")
        # No backend configured at all is a different situation: this is a
        # single-instance deployment and the in-process store *is* the source of
        # truth. `build_cache` warns loudly at startup; failing every quota check
        # here would take the service down instead.
        return await getattr(self._fallback, method)(*args, **kwargs)

    async def get(self, key: str, *, required: bool = False) -> str | None:
        return await self._call("get", key, required=required)

    async def set(self, key: str, value: str, ttl: int | None = None,
                  *, required: bool = False) -> None:
        await self._call("set", key, value, ttl, required=required)

    async def add(self, key: str, value: str, ttl: int | None = None,
                  *, required: bool = False) -> bool:
        return await self._call("add", key, value, ttl, required=required)

    async def incr(self, key: str, ttl: int | None = None, amount: int = 1,
                   *, required: bool = False) -> int:
        return await self._call("incr", key, ttl, amount, required=required)

    async def delete(self, key: str, *, required: bool = False) -> None:
        await self._call("delete", key, required=required)

    async def health(self) -> dict:
        ok = False
        if self._primary is not None:
            try:
                ok = await self._primary.ping()
                self._mark_up()
            except Exception as exc:
                self._mark_down(exc)
        # An unconfigured cache is healthy by definition — memory is the source
        # of truth. A *configured* one is healthy only if it actually answers.
        healthy = ok if self._configured else True
        return {"backend": self.backend, "healthy": healthy,
                "configured": self._configured,
                "degraded": self.is_degraded, "failures": self._failures}


def build_redis_client(url: str):
    """Create a pooled async Redis client, or None if unavailable."""
    try:
        import redis.asyncio as aioredis
    except ImportError:
        log.error("REDIS_URL is set but the redis package is not installed")
        return None

    return aioredis.from_url(
        url,
        encoding="utf-8",
        decode_responses=True,
        # Bounded so a hung Redis can never hold a request open.
        socket_connect_timeout=2,
        socket_timeout=2,
        socket_keepalive=True,
        health_check_interval=30,
        retry_on_timeout=True,
        max_connections=int(os.environ.get("REDIS_MAX_CONNECTIONS", "50")),
    )


async def build_cache() -> ResilientCache:
    fallback = InMemoryCache()
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        log.warning(
            "REDIS_URL not set — entitlements, quota and rate limits are "
            "per-process and reset on deploy. Required before running >1 replica."
        )
        return ResilientCache(None, fallback)

    client = build_redis_client(url)
    if client is None:
        # The package is missing — this cannot recover at runtime, but the
        # operator did ask for durability, so `required` calls must still fail
        # closed rather than silently trusting per-process state.
        return ResilientCache(None, fallback, configured=True)
    try:
        await client.ping()
        log.info("redis cache connected")
    except Exception as exc:
        # Keep the client. redis-py's pool reconnects transparently, so a
        # startup blip must not permanently downgrade the process — the old
        # behaviour discarded the client and never retried, which turned a
        # 5-second outage into a fail-open quota for the life of the replica.
        log.error("redis ping failed at startup, will retry on demand: %s", exc)
    return ResilientCache(RedisCache(client), fallback, configured=True)
