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
        assert q._first_day == 0
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
        assert await q._cache.get(q._welcome_key("s")) is None

    @pytest.mark.asyncio
    async def test_pro_is_untouched(self):
        q = make(3)
        status = await q.status("p", True)
        assert status.unlimited
        assert await q._cache.get(q._welcome_key("p")) is None
