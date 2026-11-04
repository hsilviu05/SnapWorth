"""Paywall views (`POST /events/paywall`) and the funnel they feed.

Pinned here: only a known trigger is accepted, only an attested device is
counted, a device counts once per day overall and once per trigger, and the
counts reach `/week`'s funnel and `/paywall`'s views column.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cache import InMemoryCache, ResilientCache  # noqa: E402
from ops import notify, opsexperiment, opsstats  # noqa: E402
from security import auth  # noqa: E402
from main import app  # noqa: E402

client = TestClient(app)

SUBJECT = "attested-key-paywall"


def bearer(subject: str = SUBJECT) -> dict:
    token, _ = auth.deps.signer.mint(subject)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def seen(monkeypatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "paywall_viewed", lambda s, t: calls.append((s, t)))
    return calls


class TestRoute:
    def test_counts_an_attested_view(self, seen):
        r = client.post("/events/paywall", json={"trigger": "scan_limit"}, headers=bearer())
        assert r.status_code == 204
        assert seen == [(SUBJECT, "scan_limit")]

    def test_refuses_an_unknown_trigger(self, seen):
        r = client.post("/events/paywall", json={"trigger": "made_up"}, headers=bearer())
        assert r.status_code == 422
        assert seen == []

    def test_refuses_extra_fields(self, seen):
        r = client.post("/events/paywall", json={"trigger": "settings", "name": "x"},
                        headers=bearer())
        assert r.status_code == 422
        assert seen == []

    def test_a_legacy_caller_is_not_counted(self, seen):
        r = client.post("/events/paywall", json={"trigger": "settings"},
                        headers={"x-device-id": "someone"})
        assert r.status_code in (204, 401)
        assert seen == []


class TestCounting:
    @pytest.fixture
    def cache(self, monkeypatch) -> ResilientCache:
        cache = ResilientCache(None, InMemoryCache())
        monkeypatch.setattr(notify, "_cache", cache)
        opsstats.bind(cache)
        yield cache
        opsstats.bind(None)

    def test_once_per_device_per_day_overall_and_per_trigger(self, cache):
        async def go():
            for subject, trigger in [("a", "scan_limit"), ("a", "scan_limit"),
                                     ("a", "settings"), ("b", "scan_limit")]:
                await notify._note_paywall_view(subject, trigger)
            day = opsstats.day()
            return (await opsstats.read_stat(day, opsstats.PAYWALL_VIEWERS),
                    await opsstats.read_stat(day, opsstats.paywall_views_counter("scan_limit")),
                    await opsstats.read_stat(day, opsstats.paywall_views_counter("settings")))
        assert asyncio.run(go()) == (2, 2, 1)

    def test_an_unknown_trigger_is_never_a_counter(self, cache):
        notify.paywall_viewed("a", "made_up")
        assert asyncio.run(opsstats.read_stat(
            opsstats.day(), opsstats.paywall_views_counter("made_up"))) == 0


class TestReports:
    def test_funnel_shares_are_of_active_user_days(self):
        lines = notify._funnel_lines(active=100, limit_hits=30, viewers=20, trials=4, paid=1)
        assert lines[1:] == [
            "Active user-days: 100",
            "Free-limit refusals: 30 (requests, not devices)",
            "Saw a paywall: 20 device-days · 20% of active",
            "Started a trial: 4 · 4% of active · 20% of paywall views",
            "Paid: 1",
        ]

    def test_funnel_before_any_view_says_where_views_come_from(self):
        lines = notify._funnel_lines(active=0, limit_hits=0, viewers=0, trials=0, paid=0)
        assert "Saw a paywall: 0 device-days (reported by app 1.7.2+)" in lines
        assert "Started a trial: 0" in lines

    def test_paywall_readout_has_a_views_column(self, monkeypatch):
        cache = ResilientCache(None, InMemoryCache())
        opsstats.bind(cache)
        now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

        async def go():
            day = opsstats.day(now - timedelta(days=1))
            await cache.set(opsstats.stat_key(day, opsstats.PAYWALL_VIEWERS), "7")
            await cache.set(opsstats.stat_key(day, opsstats.paywall_views_counter("settings")), "5")
            await cache.set(opsstats.stat_key(day, "trial_starts"), "1")
            await cache.set(opsstats.stat_key(day, "trial_starts:settings"), "1")
            return await opsexperiment.paywall_text(now)
        try:
            text = asyncio.run(go())
        finally:
            opsstats.bind(None)
        assert "Seen by: 7 device-days" in text
        assert "<code>trigger           views trial direct</code>" in text
        assert "<code>settings              5     1      0</code>" in text
