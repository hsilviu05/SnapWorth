"""Startup resilience of the cache layer.

The service must boot. A misconfigured `REDIS_URL` is an operator mistake to
shout about, not a reason for a container to crash-loop — and `cache.py` had
already decided that, for the one unrecoverable misconfiguration it handled
(the redis package missing). A malformed URL took the identical situation and
killed the process instead, because `redis.asyncio.from_url` parses eagerly and
raises `ValueError` straight through `build_cache` into the lifespan hook.

The safety property that makes degrading acceptable is asserted here too: a
cache that was *configured* and could not be built keeps `required` operations
failing closed, so a typo cannot hand anyone free Pro.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache as cache_module  # noqa: E402
from cache import CacheUnavailable  # noqa: E402


# ── Client construction ──────────────────────────────────────────────────────

class TestBuildRedisClient:
    @pytest.mark.parametrize("url", [
        "http://cache.internal:6379",        # wrong scheme
        "not-a-url",                         # no scheme at all
        "redis://cache.internal:notaport/0",  # port is not an integer
        "",                                  # substituted-empty by a deploy tool
    ])
    def test_a_malformed_url_returns_none_rather_than_raising(self, url):
        assert cache_module.build_redis_client(url) is None

    def test_a_garbage_pool_size_does_not_cost_us_redis(self, monkeypatch):
        """The distinction the `from_url` guard alone does not make.

        With the guard in place, an unguarded `int(os.environ[...])` no longer
        crashes — the ValueError is simply caught — so the *crash* property
        cannot tell the two apart, and a mutation run confirmed that. But
        catching it discards the whole client and runs the replica degraded on
        a valid Redis, over a tuning knob. Parsing the pool size separately is
        what keeps a typo in `REDIS_MAX_CONNECTIONS` from costing durability,
        and this is the test that says so.
        """
        monkeypatch.setenv("REDIS_MAX_CONNECTIONS", "fifty")
        client = cache_module.build_redis_client("redis://localhost:6379/0")
        assert client is not None, (
            "a bad pool size must fall back to the default, not throw away a "
            "perfectly good Redis URL")
        assert client.connection_pool.max_connections == \
            cache_module.DEFAULT_REDIS_MAX_CONNECTIONS

    def test_a_usable_url_still_builds_a_client(self):
        """The guard must not swallow the working case.

        No connection is attempted by `from_url`, so this needs no Redis.
        """
        client = cache_module.build_redis_client("redis://localhost:6379/0")
        assert client is not None


# ── Pool sizing ──────────────────────────────────────────────────────────────

class TestMaxConnections:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("REDIS_MAX_CONNECTIONS", raising=False)

    def test_default_when_unset(self):
        assert cache_module._max_connections() == \
            cache_module.DEFAULT_REDIS_MAX_CONNECTIONS

    def test_a_valid_override_is_honoured(self, monkeypatch):
        monkeypatch.setenv("REDIS_MAX_CONNECTIONS", "12")
        assert cache_module._max_connections() == 12

    def test_surrounding_whitespace_is_tolerated(self, monkeypatch):
        monkeypatch.setenv("REDIS_MAX_CONNECTIONS", " 12 ")
        assert cache_module._max_connections() == 12

    @pytest.mark.parametrize("raw", ["", "   ", "fifty", "12.5", "1e3"])
    def test_an_unusable_value_falls_back_instead_of_raising(self, raw, monkeypatch):
        """A pool size is the least important thing in this module.

        `int(os.environ[...])` raised inside the startup path, so a typo in a
        tuning knob took the whole service down.
        """
        monkeypatch.setenv("REDIS_MAX_CONNECTIONS", raw)
        assert cache_module._max_connections() == \
            cache_module.DEFAULT_REDIS_MAX_CONNECTIONS

    @pytest.mark.parametrize("raw", ["0", "-1"])
    def test_a_pool_that_could_never_serve_a_request_is_refused(self, raw, monkeypatch):
        monkeypatch.setenv("REDIS_MAX_CONNECTIONS", raw)
        assert cache_module._max_connections() == \
            cache_module.DEFAULT_REDIS_MAX_CONNECTIONS


# ── build_cache ──────────────────────────────────────────────────────────────

class TestBuildCache:
    @pytest.mark.asyncio
    async def test_a_malformed_url_starts_degraded_not_dead(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "http://cache.internal:6379")
        built = await cache_module.build_cache()
        assert built.is_degraded
        assert built.backend == "redis-unavailable"

    @pytest.mark.asyncio
    async def test_a_malformed_url_still_fails_required_calls_closed(self, monkeypatch):
        """The reason degrading is safe rather than convenient.

        Durability was *asked for*, so process memory is not authoritative —
        other replicas hold their own copies. Anything gating a paid resource
        must refuse to answer from memory, or a mistyped environment variable
        becomes free Pro for everyone.
        """
        monkeypatch.setenv("REDIS_URL", "redis://cache.internal:notaport/0")
        built = await cache_module.build_cache()
        assert built.is_configured, "operator intent survives an unusable URL"
        with pytest.raises(CacheUnavailable):
            await built.get("entitlement:someone", required=True)

    @pytest.mark.asyncio
    async def test_a_malformed_url_leaves_unrequired_calls_working(self, monkeypatch):
        """/scan must keep working. Quota goes per-process; that is the trade."""
        monkeypatch.setenv("REDIS_URL", "not-a-url")
        built = await cache_module.build_cache()
        await built.set("k", "v")
        assert await built.get("k") == "v"

    @pytest.mark.asyncio
    async def test_no_url_is_a_different_state_and_answers_required_calls(
            self, monkeypatch):
        """Single-instance deployment: memory *is* the source of truth.

        Included so the two states cannot be collapsed into one by a later
        simplification — that collapse is a fail-open bug in one direction and
        a total outage in the other.
        """
        monkeypatch.delenv("REDIS_URL", raising=False)
        built = await cache_module.build_cache()
        assert not built.is_configured
        assert await built.get("nothing", required=True) is None
