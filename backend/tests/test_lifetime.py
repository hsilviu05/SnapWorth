"""`/lifetime`: totals since launch, folded from the expiring day counters."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.cache import InMemoryCache, ResilientCache  # noqa: E402
from ops import notify, opslifetime, opsrevenue, opsstats  # noqa: E402

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def cache() -> ResilientCache:
    return ResilientCache(None, InMemoryCache())


@pytest_asyncio.fixture
async def configured(cache, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    notify.configure(cache)
    yield cache
    await notify.aclose()


async def put(cache, days_ago: int, name: str, value: int) -> str:
    day = opsstats.day(NOW - timedelta(days=days_ago))
    await cache.set(opsstats.stat_key(day, name), str(value))
    return day


class TestRollUp:
    @pytest.mark.asyncio
    async def test_folds_each_complete_day_once(self, configured):
        await put(configured, 1, "scans_pro", 4)
        await put(configured, 10, "scans_pro", 6)
        assert await opslifetime.roll_up(NOW) == 2
        # Another replica, or the next digest: nothing is folded twice.
        assert await opslifetime.roll_up(NOW) == 0
        assert await configured.get(opslifetime.life_key("scans_pro")) == "10"

    @pytest.mark.asyncio
    async def test_today_is_read_live_never_folded(self, configured):
        await put(configured, 0, "scans_free", 3)
        await put(configured, 2, "scans_free", 5)
        totals, _ = await opslifetime.totals(NOW)
        assert totals["scans_free"] == 8
        await put(configured, 0, "scans_free", 7)        # today keeps counting
        totals, _ = await opslifetime.totals(NOW)
        assert totals["scans_free"] == 12

    @pytest.mark.asyncio
    async def test_totals_outlive_the_day_counters(self, configured):
        day = await put(configured, 3, "tok_out", 1000)
        await opslifetime.roll_up(NOW)
        await configured.delete(opsstats.stat_key(day, "tok_out"))   # expired
        totals, _ = await opslifetime.totals(NOW)
        assert totals["tok_out"] == 1000

    @pytest.mark.asyncio
    async def test_since_is_the_oldest_day_folded(self, configured):
        await put(configured, 2, "scans_pro", 1)
        oldest = await put(configured, 30, "scans_pro", 1)
        _, since = await opslifetime.totals(NOW)
        assert since == oldest

    @pytest.mark.asyncio
    async def test_days_past_the_window_are_not_read(self, configured):
        await put(configured, opslifetime.WINDOW_DAYS + 1, "scans_pro", 99)
        totals, since = await opslifetime.totals(NOW)
        assert totals["scans_pro"] == 0 and since is None

    @pytest.mark.asyncio
    async def test_the_daily_digest_folds_yesterday(self, cache):
        import httpx

        from ops import telegram
        from tests.test_notify import FAKE_CHAT, FAKE_TOKEN, Recorder
        notifier = telegram.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(Recorder().handler)))
        notify.configure(cache, notifier=notifier)
        try:
            await put(cache, 1, "scans_pro", 2)
            await notify.send_digest(NOW)
            assert await cache.get(opslifetime.life_key("scans_pro")) == "2"
        finally:
            await notify.aclose()


class TestText:
    @pytest.mark.asyncio
    async def test_the_block_reports_every_total(self, configured):
        await put(configured, 1, "scans_free", 90)
        await put(configured, 1, "scans_pro", 76)
        await put(configured, 1, "active_users", 99)
        await put(configured, 1, "trial_starts", 3)
        await put(configured, 1, "trial_conversions", 1)
        await put(configured, 1, "tok_in", 1_000_000)
        await put(configured, 1, "tok_out", 1_000_000)
        await put(configured, 1, "model_calls", 170)
        month = opsrevenue.month(NOW)
        await configured.set(opsrevenue.month_key(month, "gross:USD"), "7998")
        await configured.set(opsrevenue.month_key(month, "currencies"), '["USD"]')
        text = await opslifetime.lifetime_text(NOW)
        assert text.startswith("🏁 <b>Lifetime</b> (since 02 Nov 2026)")
        assert "Scans: 166 (90 free · 76 Pro) · failed 0" in text
        assert "Active user-days: 99" in text
        assert "Trial starts: 3 · paid: 1 (1 converted trial · 0 direct)" in text
        assert "Gemini spend: $2.80 · 170 calls" in text
        assert "Revenue: $79.98" in text
        assert "After 15% Apple: ≈ $67.98 · minus Gemini ≈ $65.18" in text

    @pytest.mark.asyncio
    async def test_revenue_sums_every_month_kept(self, configured):
        for months_back, cents in ((0, 499), (3, 3999), (12, 3999)):
            at = NOW
            for _ in range(months_back):
                at = opsrevenue._previous_month(at)
            month = opsrevenue.month(at)
            await configured.set(opsrevenue.month_key(month, "gross:USD"), str(cents))
            await configured.set(opsrevenue.month_key(month, "currencies"), '["USD"]')
        gross, refunds = await opsrevenue.lifetime_money(NOW)
        assert gross == {"USD": 84.97} and refunds == {}

    @pytest.mark.asyncio
    async def test_nothing_counted_yet(self, configured):
        text = await opslifetime.lifetime_text(NOW)
        assert "nothing counted yet" in text
        assert "Scans: 0" in text

    @pytest.mark.asyncio
    async def test_command_and_weekly_report(self, configured):
        assert (await notify.handle_command("/lifetime")).startswith("🏁")
        assert "lifetime" in dict(notify.COMMANDS)
        assert "🏁 <b>Lifetime</b>" in await notify._weekly_text(NOW)
