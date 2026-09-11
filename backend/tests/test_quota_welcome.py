"""First-day welcome allowance (FREE_SCANS_FIRST_DAY).

Off by default and exactly the old behaviour when off. On, a genuinely new
subject gets the larger allowance for the day it was granted, once, and the
daily limit thereafter. A reinstall DeviceCheck recognises gets nothing.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quota as quota_module  # noqa: E402
from cache import InMemoryCache, ResilientCache  # noqa: E402
from quota import QuotaExceeded, ScanQuota  # noqa: E402


class _NoDeviceCheck:
    is_configured = False


class _Reinstalled:
    is_configured = True

    async def query_bits(self, token):
        return {"bit0": True, "bit1": False}


def make(first_day: int, dc=None) -> ScanQuota:
    return ScanQuota(ResilientCache(None, InMemoryCache()), dc or _NoDeviceCheck(),
                     limit=1, first_day_limit=first_day)


class TestOff:
    @pytest.mark.asyncio
    async def test_default_is_exactly_the_old_behaviour(self):
        q = make(0)
        assert await q.starting_balance("s", None) == 1
        assert (await q.status("s", False)).limit == 1
        await q.consume("s", False)
        with pytest.raises(QuotaExceeded) as exc:
            await q.check("s", False)
        assert exc.value.message == "You've used your free scan for today."

    @pytest.mark.asyncio
    async def test_a_first_day_limit_not_above_daily_is_off(self):
        q = make(1)
        # Resolved per call now, not captured at construction — the value is
        # overridable at runtime from the ops bot.
        assert await q._first_day_limit() == 0
        assert await q.starting_balance("s", None) == 1


class TestOn:
    @pytest.mark.asyncio
    async def test_new_subject_gets_the_welcome_today(self):
        q = make(3)
        assert await q.starting_balance("s", None) == 3
        assert (await q.status("s", False)).limit == 3
        for _ in range(3):
            await q.check("s", False)
            await q.consume("s", False)
        with pytest.raises(QuotaExceeded) as exc:
            await q.check("s", False)
        assert exc.value.message == "You've used all 3 free scans today."

    @pytest.mark.asyncio
    async def test_back_to_the_daily_limit_tomorrow(self, monkeypatch):
        q = make(3)
        monkeypatch.setattr(quota_module, "_utc_day", lambda: "2026-09-03")
        assert await q.starting_balance("s", None) == 3
        monkeypatch.setattr(quota_module, "_utc_day", lambda: "2026-09-04")
        assert (await q.status("s", False)).limit == 1
        assert await q.starting_balance("s", None) == 1, "not welcomed twice"

    @pytest.mark.asyncio
    async def test_welcomed_once_even_when_seen_marker_lapses(self):
        """`quota:seen` expires with the counters; the welcome must not."""
        q = make(3)
        assert await q.starting_balance("s", None) == 3
        await q._cache.delete(q._seen_key("s"))        # a day away
        assert await q.starting_balance("s", None) == 3, "same day: still the welcome limit"
        # But no second grant was written — the key still holds the first day.
        assert await q._cache.get(q._welcome_key("s")) == quota_module._utc_day()

    @pytest.mark.asyncio
    async def test_reinstall_gets_no_welcome(self):
        q = make(3, dc=_Reinstalled())
        assert await q.starting_balance("s", "device-token") == 0
        assert (await q.status("s", False)).limit == 1
        # The refusal is *recorded*. This assertion used to read `is None`,
        # which locked in the bug below: nothing marked the welcome as spent,
        # so the same reinstall could come back and claim it.
        assert await q._cache.get(q._welcome_key("s")) == "denied"

    @pytest.mark.asyncio
    async def test_a_refused_reinstall_cannot_claim_the_welcome_later(self):
        """B-7. The counter and the `seen` marker both expire with the day;
        the welcome marker does not. Without writing it on the refusal path,
        the same reinstall looked new again ~30h later and was handed the
        welcome allowance it had just been denied."""
        q = make(3, dc=_Reinstalled())
        assert await q.starting_balance("s", "device-token") == 0

        # A day passes: the counter and `seen` marker lapse, the welcome
        # marker (400-day TTL) does not.
        await q._cache.delete(q._counter_key("s"))
        await q._cache.delete(q._seen_key("s"))

        assert await q.starting_balance("s", "device-token") == 0
        assert (await q.status("s", False)).limit == 1

    @pytest.mark.asyncio
    async def test_pro_is_untouched(self):
        q = make(3)
        status = await q.status("p", True)
        assert status.unlimited
        assert await q._cache.get(q._welcome_key("p")) is None


class TestRuntimeOverride:
    """The welcome allowance is settable from the ops bot at runtime.

    The measurement half of the free-scan experiment lives in the bot
    (`limit_hits`, `/experiment`); the control half was a Railway variable and
    a redeploy. These pin the properties that make a lever in a chat window
    safe to have: it is clamped, it cannot fail open, and an unreadable
    override falls back to the environment rather than to a guess.
    """

    def _quota(self, override, *, env_first_day=0, limit=1):
        from cache import InMemoryCache, ResilientCache
        return ScanQuota(ResilientCache(None, InMemoryCache()), None,
                         limit=limit, first_day_limit=env_first_day,
                         welcome_override=override)

    @pytest.mark.asyncio
    async def test_the_override_arms_a_welcome_the_environment_never_set(self):
        async def lever():
            return 3
        q = self._quota(lever, env_first_day=0)
        assert await q._first_day_limit() == 3
        assert await q.starting_balance("s", None) == 3

    @pytest.mark.asyncio
    async def test_the_override_can_disarm_one_the_environment_set(self):
        async def lever():
            return 0
        q = self._quota(lever, env_first_day=3)
        assert await q._first_day_limit() == 0
        assert await q.starting_balance("s", None) == 1

    @pytest.mark.asyncio
    async def test_none_means_use_the_environment(self):
        async def lever():
            return None
        q = self._quota(lever, env_first_day=3)
        assert await q._first_day_limit() == 3

    @pytest.mark.asyncio
    async def test_an_unreadable_override_falls_back_and_never_fails_open(self):
        async def lever():
            raise RuntimeError("cache down")
        q = self._quota(lever, env_first_day=3)
        assert await q._first_day_limit() == 3, "must not fail the scan"

        async def lever_off():
            raise RuntimeError("cache down")
        off = self._quota(lever_off, env_first_day=0)
        assert await off._first_day_limit() == 0, \
            "an unreadable lever must not grant an allowance nobody configured"

    @pytest.mark.asyncio
    async def test_a_fat_fingered_value_is_clamped(self):
        """Every scan past the daily limit is real money on the Gemini bill."""
        async def huge():
            return 9_999
        q = self._quota(huge)
        assert await q._first_day_limit() == ScanQuota.MAX_FIRST_DAY_SCANS

        async def negative():
            return -5
        assert await self._quota(negative)._first_day_limit() == 0

        async def nonsense():
            return "three"
        assert await self._quota(nonsense, env_first_day=3)._first_day_limit() == 3

    @pytest.mark.asyncio
    async def test_an_override_no_larger_than_the_daily_limit_is_not_a_welcome(self):
        async def same():
            return 1
        assert await self._quota(same, limit=1)._first_day_limit() == 0
