"""`/trials`: how each trial ended, and the index fields it reads."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from billing.entitlements import Entitlement  # noqa: E402
from core.cache import InMemoryCache, ResilientCache  # noqa: E402
from ops import notify, opsindex, opsstats, opstrials  # noqa: E402

H, DAY = 3600, 86400
NOW = time.time()


def day_of(ts: float) -> str:
    return opsstats.day(datetime.fromtimestamp(ts, timezone.utc))


def row(**kw) -> dict:
    base = {"started_as": "trial", "acq": "trial", "product": "com.snapworth.yearly",
            "first": NOW - 2 * DAY, "trial_ends": NOW + DAY, "expires": NOW + DAY,
            "who": "aaaaaaaaaaaaaaaa", "devices": ["aaaaaaaaaaaaaaaa"], "seen": NOW}
    base.update(kw)
    return base


class TestOutcome:
    def test_each_ending(self):
        ended = {"expires": NOW - H, "trial_ends": NOW - H}
        assert opstrials.outcome(row(acq="paid"), NOW) == opstrials.PAID
        assert opstrials.outcome(row(), NOW) == opstrials.ACTIVE
        assert opstrials.outcome(row(auto_renew=False), NOW) == opstrials.CANCELLING
        assert opstrials.outcome(row(**ended, auto_renew=False), NOW) == opstrials.CANCELLED
        assert opstrials.outcome(row(**ended, billing_failed_at=NOW - 2 * H),
                                 NOW) == opstrials.BILLING
        assert opstrials.outcome(row(**ended, billing_failed_at=NOW - 2 * H,
                                     grace_until=NOW + 10 * DAY), NOW) == opstrials.GRACE
        assert opstrials.outcome(row(**ended, auto_renew=True), NOW) == opstrials.ENDED

    def test_hours_to_cancel(self):
        assert opstrials.hours_to_cancel(row(renew_off_at=NOW - 2 * DAY + 5 * H)) == pytest.approx(5)
        assert opstrials.hours_to_cancel(row()) is None


class TestScans:
    def test_counts_only_the_trial_days(self):
        users = {"aaaaaaaaaaaaaaaa": {"pro_days": {
            day_of(NOW - 2 * DAY): 4, day_of(NOW): 3, day_of(NOW - 10 * DAY): 99}}}
        assert opstrials.trial_scans(row(), users) == 7

    def test_unknown_device_is_none(self):
        assert opstrials.trial_scans(row(devices=[], who=None), {}) is None


@pytest.fixture
def cache():
    c = ResilientCache(None, InMemoryCache())
    opsindex.bind(c)
    opsstats.bind(c)
    yield c
    opsindex.bind(None)


class TestText:
    @pytest.mark.asyncio
    async def test_summary_and_table(self, cache):
        subs = {
            "1": row(acq="paid"),
            "2": row(first=NOW - 5 * DAY, expires=NOW - 2 * DAY, trial_ends=NOW - 2 * DAY,
                     auto_renew=False, renew_off_at=NOW - 5 * DAY + 3 * H),
            "3": row(first=NOW - 6 * DAY, expires=NOW - 3 * DAY, trial_ends=NOW - 3 * DAY,
                     billing_failed_at=NOW - 3 * DAY, auto_renew=True),
            "4": row(started_as="paid", acq="paid"),
            "5": row(first=NOW - 60 * DAY),
        }
        await cache.set(opsindex.SUBS_INDEX_KEY, json.dumps(subs))
        text = await opstrials.trials_text(NOW)
        assert "3 started: 1 paid · 1 card failed · 1 cancelled" in text
        assert "Trial → paid: 1 of 3 decided (33%, n=3)" in text
        assert "Cancelled after: median 3h · 1 of 1 within the first day" in text
        assert "1 ended on a declined card" in text

    @pytest.mark.asyncio
    async def test_empty(self, cache):
        assert "No trial started" in await opstrials.trials_text(NOW)

    @pytest.mark.asyncio
    async def test_command_is_routed(self, cache, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        notify.configure(cache)
        try:
            assert (await notify.handle_command("/trials")).startswith("🧪")
        finally:
            await notify.aclose()


class TestIndexFields:
    def _trial(self, otid="t1"):
        return Entitlement("pro", "com.snapworth.yearly", int(NOW) + DAY, otid, "Production",
                           original_purchase_at=int(NOW) - 2 * DAY, offer_type=1,
                           offer_discount_type="FREE_TRIAL")

    @pytest.mark.asyncio
    async def test_cancellation_time_is_kept_and_cleared(self, cache):
        ent = self._trial()
        await opsindex.index_subscription(None, ent, True)
        await opsindex.index_subscription(None, ent, False, event_at=int(NOW) - H)
        doc = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
        assert doc["t1"]["renew_off_at"] == int(NOW) - H
        # A second "off" does not move it.
        await opsindex.index_subscription(None, ent, False, event_at=int(NOW))
        doc = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
        assert doc["t1"]["renew_off_at"] == int(NOW) - H
        await opsindex.index_subscription(None, ent, True)
        doc = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
        assert "renew_off_at" not in doc["t1"]

    @pytest.mark.asyncio
    async def test_billing_failure_and_grace(self, cache):
        await opsindex.index_subscription(None, self._trial(), True, event_at=int(NOW),
                                          billing_failed=True, grace_until=int(NOW) + 16 * DAY)
        doc = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
        assert doc["t1"]["billing_failed_at"] == int(NOW)
        assert doc["t1"]["grace_until"] == int(NOW) + 16 * DAY

    @pytest.mark.asyncio
    async def test_live_cancel_alert_detail(self, cache):
        ent = self._trial()
        await opsindex.index_subscription(None, ent, False, event_at=int(NOW) - 2 * DAY + 4 * H)
        assert await opstrials.cancel_detail("t1") == "🧪 Trial cancelled after 4h · ? Pro scans so far"
