"""First-day welcome allowance (FREE_SCANS_FIRST_DAY).

Off by default and exactly the old behaviour when off. On, a genuinely new
subject gets the larger allowance for the day it was granted, once, and the
daily limit thereafter. A reinstall DeviceCheck recognises gets nothing.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quota as quota_module  # noqa: E402
from cache import InMemoryCache, ResilientCache  # noqa: E402
from quota import QuotaExceeded, ScanQuota  # noqa: E402


class _NoDeviceCheck:
    is_configured = False


def _this_month() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m")


class _Reinstalled:
    """Exhausted *this month*, which is the only period DeviceCheck can say.

    The `last_update_time` stamp is part of Apple's real answer and the stub
    used to omit it — which is exactly why bit0 read as permanent.
    """
    is_configured = True

    _UNSET = object()

    def __init__(self, month=_UNSET):
        # `_UNSET` rather than a falsy default, so a test can ask for a stamp
        # that is genuinely absent — `month=None` — without `or` turning it
        # back into this month.
        self._month = _this_month() if month is self._UNSET else month

    async def query_bits(self, token):
        bits = {"bit0": True, "bit1": False}
        if self._month is not None:
            bits["last_update_time"] = self._month
        return bits

    async def update_bits(self, token, bit0, bit1):
        self._month = _this_month()


def make(first_day: int, dc=None) -> ScanQuota:
    return ScanQuota(ResilientCache(None, InMemoryCache()), dc or _NoDeviceCheck(),
                     limit=1, first_day_limit=first_day)


class TestDeviceCheckMonth:
    """bit0 means "exhausted this month", not "exhausted, ever"."""

    @pytest.mark.asyncio
    async def test_a_mark_from_an_earlier_month_does_not_deny_a_new_install(self):
        """The defect: nothing ever cleared bit0, and nearly everyone had it.

        `note_exhausted` is the only writer of bit0 in the repo and no reset
        existed anywhere, while the docstring promised one. With
        FREE_SCANS_PER_DAY = 1 the bit is set on the second scan attempt of any
        day, so it was set for essentially every engaged free user within their
        first day — it did not distinguish abusers from users, it flagged the
        free base. Months later a legitimate new phone, or simply
        re-downloading the app, met `free_scans_remaining: 0` and the paywall
        before producing a single valuation, and was permanently disqualified
        from the first-day welcome.
        """
        q = make(3, dc=_Reinstalled(month="2025-01"))
        assert await q.starting_balance("fresh-subject", "device-token") == 3

    @pytest.mark.asyncio
    async def test_a_mark_from_this_month_still_denies(self):
        """The anti-abuse floor the bit exists for is intact."""
        q = make(3, dc=_Reinstalled())
        assert await q.starting_balance("fresh-subject", "device-token") == 0

    @pytest.mark.asyncio
    async def test_a_stale_mark_is_cleared_so_the_next_lookup_is_unambiguous(self):
        dc = _Reinstalled(month="2025-01")
        q = make(3, dc=dc)
        await q.starting_balance("fresh-subject", "device-token")
        assert dc._month == _this_month(), "the stale mark was reset, not just ignored"

    @pytest.mark.asyncio
    async def test_an_unreadable_stamp_grants_rather_than_denies(self):
        """Recency not established is the same standing as Apple unreachable.

        The branch above this one already grants when `query_bits` raises —
        "availability of Apple's API must not gate our own service" — and a
        hardening signal must not outweigh a real user on a new phone. With a
        real Apple this shape cannot occur: a stored bit state always carries
        its stamp.
        """
        q = make(3, dc=_Reinstalled(month=None))
        assert await q.starting_balance("fresh-subject", "device-token") == 3


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


class TestArmingTheLeverLater:
    """The case that was already live: turning the welcome on afterwards.

    `raise FREE_SCANS_FIRST_DAY from 0` — as a Railway variable or from the ops
    bot — used to hand the welcome allowance to the *entire existing free user
    base*, one subject at a time, over the following ~30 hours.
    """

    @staticmethod
    def _armable(cache, lever):
        async def override():
            return lever["value"]
        return ScanQuota(cache, _NoDeviceCheck(), limit=1, first_day_limit=0,
                         welcome_override=override)

    @pytest.mark.asyncio
    async def test_an_existing_subject_is_not_welcomed_when_the_lever_is_armed(self):
        cache = ResilientCache(None, InMemoryCache())
        lever = {"value": 0}
        quota = self._armable(cache, lever)

        # Seen while the welcome was off, and today's single scan is spent.
        assert await quota.starting_balance("existing", None) == 1
        await quota.consume("existing", False)

        # The seen marker lapses (it used to, every ~30 hours, for everyone),
        # and the operator arms the lever.
        await cache.delete(quota._seen_key("existing"))
        lever["value"] = 3

        assert await quota.starting_balance("existing", None) == 1, (
            "an existing user who already spent today's scan must not be "
            "handed extra paid scans by a lever aimed at new users"
        )
        assert (await quota.status("existing", False)).limit == 1
        with pytest.raises(QuotaExceeded):
            await quota.check("existing", False)

    @pytest.mark.asyncio
    async def test_a_genuinely_new_subject_is_still_welcomed_after_arming(self):
        # The other half: the refusal marker must not be so broad that it also
        # denies the users the lever exists for.
        cache = ResilientCache(None, InMemoryCache())
        lever = {"value": 0}
        quota = self._armable(cache, lever)
        await quota.starting_balance("existing", None)

        lever["value"] = 3
        assert await quota.starting_balance("newcomer", None) == 3
        assert (await quota.status("newcomer", False)).limit == 3

    @pytest.mark.asyncio
    async def test_the_refusal_is_recorded_while_the_lever_is_off(self):
        # The mechanism, asserted directly: this path wrote no marker at all,
        # which is what left every existing subject eligible.
        cache = ResilientCache(None, InMemoryCache())
        quota = ScanQuota(cache, _NoDeviceCheck(), limit=1, first_day_limit=0)
        await quota.starting_balance("s", None)
        assert await cache.get(quota._welcome_key("s")) == quota_module._DENIED

    @pytest.mark.asyncio
    async def test_the_seen_marker_outlives_the_counters(self):
        # It used to carry the 30-hour counter TTL, and `add` never refreshes
        # an existing key — `InMemoryCache.add` returns False without touching
        # the expiry, `RedisCache.add` is `SET … NX` — so it expired 30 hours
        # after the subject was first minted rather than after it was last
        # used. Every active subject therefore looked new again on that
        # cadence, which is what made the welcome reachable at all and what
        # re-ran the DeviceCheck query for the whole base every 30 hours.
        #
        # Asserted on the stored expiry rather than on the constants: the
        # constants can be right while the call site passes the wrong one, and
        # a mutation run confirmed a constants-only assertion misses exactly
        # that.
        memory = InMemoryCache()
        quota = ScanQuota(ResilientCache(None, memory), _NoDeviceCheck(),
                          limit=1, first_day_limit=0)
        await quota.starting_balance("s", None)

        _, expires = memory._data[quota._seen_key("s")]
        assert expires is not None
        remaining = expires - time.time()
        assert remaining > quota_module._COUNTER_TTL, (
            f"the seen marker expires in {remaining / 3600:.0f}h, within the "
            "counter horizon — so the subject becomes 'first seen' again"
        )
        assert remaining == pytest.approx(quota_module._WELCOME_TTL, abs=5)


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
