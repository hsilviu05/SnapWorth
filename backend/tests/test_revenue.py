"""`/revenue`: monthly counters from App Store notifications, and daily MRR
snapshots of `/subs`.

Driven through `notify.subscription_event`, the entry point the notification
route calls, so the counters are tested in the order they really run.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops import notify  # noqa: E402
from ops import opsindex  # noqa: E402
from ops import opsformat  # noqa: E402
from ops import opsrevenue  # noqa: E402
from core.cache import InMemoryCache, ResilientCache  # noqa: E402
from billing.entitlements import Entitlement  # noqa: E402

from tests.test_notify import FakeNotification  # noqa: E402


def paid(otid: str = "otid-1", price: float | None = 39.99, currency: str | None = "USD",
         product: str = "com.snapworth.yearly", expires_in: int = 365 * 86_400) -> Entitlement:
    now = int(time.time())
    return Entitlement("pro", product, now + expires_in, otid, "Production",
                       original_purchase_at=now - 60, price=price, currency=currency)


def trial(otid: str = "otid-t", expires_in: int = -60) -> Entitlement:
    now = int(time.time())
    return Entitlement("pro", "com.snapworth.yearly", now + expires_in, otid, "Production",
                       original_purchase_at=now - 3 * 86_400, offer_type=1,
                       offer_discount_type="FREE_TRIAL")


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


async def counter(name: str) -> int:
    return int(await notify._cache.get(opsrevenue.month_key(opsrevenue.month(), name)) or 0)


class TestMonthlyCounters:
    @pytest.mark.asyncio
    async def test_a_paid_period_is_gross_revenue_in_cents(self, configured):
        await notify.subscription_event(FakeNotification(
            paid(), notification_type="SUBSCRIBED", paid_period=True))
        await notify.subscription_event(FakeNotification(
            paid("otid-2", price=4.99, product="com.snapworth.monthly"),
            uuid="uuid-2", paid_period=True))
        assert await counter("gross:USD") == 3999 + 499
        assert await counter("payments") == 2

    @pytest.mark.asyncio
    async def test_currencies_are_kept_apart(self, configured):
        await notify.subscription_event(FakeNotification(paid(), paid_period=True))
        await notify.subscription_event(FakeNotification(
            paid("otid-2", price=44.99, currency="EUR"), uuid="u2", paid_period=True))
        assert await counter("gross:USD") == 3999
        assert await counter("gross:EUR") == 4499
        text = await opsrevenue.revenue_text()
        assert "$39.99" in text and "€44.99" in text

    @pytest.mark.asyncio
    async def test_an_unpriced_or_odd_currency_counts_the_payment_not_the_money(self, configured):
        await notify.subscription_event(FakeNotification(paid(price=None), paid_period=True))
        await notify.subscription_event(FakeNotification(
            paid("otid-2", currency="US$"), uuid="u2", paid_period=True))
        assert await counter("payments") == 2
        raw = await notify._cache.get(opsrevenue.month_key(opsrevenue.month(), "currencies"))
        assert raw is None

    @pytest.mark.asyncio
    async def test_a_refund_is_counted_against_the_month(self, configured):
        await notify.subscription_event(FakeNotification(
            paid(), notification_type="REFUND", refund=True))
        assert await counter("refunds:USD") == 3999
        assert await counter("refund_count") == 1
        assert await counter("gross:USD") == 0

    @pytest.mark.asyncio
    async def test_a_paid_expiry_is_churn_split_by_reason(self, configured):
        await notify.subscription_event(FakeNotification(
            paid(expires_in=-60), notification_type="EXPIRED", subtype="VOLUNTARY",
            expiry=True))
        await notify.subscription_event(FakeNotification(
            paid("otid-2", expires_in=-60), uuid="u2", notification_type="EXPIRED",
            subtype="BILLING_RETRY", expiry=True))
        assert await counter("churned") == 2
        assert await counter("churned_voluntary") == 1
        assert await counter("churned_billing") == 1

    @pytest.mark.asyncio
    async def test_a_trial_that_ends_unconverted_is_not_churn(self, configured):
        await notify.subscription_event(FakeNotification(
            trial(), notification_type="EXPIRED", subtype="VOLUNTARY", expiry=True))
        assert await counter("churned") == 0

    @pytest.mark.asyncio
    async def test_auto_renew_off_on_a_paid_plan_is_a_cancellation(self, configured):
        await notify.subscription_event(FakeNotification(
            paid(), notification_type="DID_CHANGE_RENEWAL_STATUS",
            subtype="AUTO_RENEW_DISABLED", cancellation=True, auto_renew=False))
        assert await counter("cancellations") == 1
        assert await counter("churned") == 0

    @pytest.mark.asyncio
    async def test_a_sandbox_tester_is_never_revenue(self, configured, monkeypatch):
        monkeypatch.setattr(opsindex, "is_bounded", lambda ent: True)
        await notify.subscription_event(FakeNotification(paid(), paid_period=True))
        assert await counter("payments") == 0

    @pytest.mark.asyncio
    async def test_a_failing_cache_never_fails_the_notification(self, configured, monkeypatch):
        async def broken(*a, **k):
            raise RuntimeError("redis down")
        monkeypatch.setattr(notify._cache, "incr", broken)
        await opsrevenue.record_notification(FakeNotification(paid(), paid_period=True))


class TestSnapshots:
    @pytest.mark.asyncio
    async def test_one_snapshot_a_day_first_writer_wins(self, configured):
        await opsindex.index_subscription(None, paid())
        now = datetime.now(timezone.utc)
        assert await opsrevenue.snapshot(now) is True
        await opsindex.index_subscription(None, paid("otid-2"))
        assert await opsrevenue.snapshot(now) is False
        snap = await opsrevenue.read_snapshot(now)
        assert snap is not None
        assert snap["paid"] == 1 and snap["mrr"] == {"USD": round(39.99 / 12, 2)}

    @pytest.mark.asyncio
    async def test_an_empty_index_writes_no_zero_into_the_history(self, configured):
        now = datetime.now(timezone.utc)
        assert await opsrevenue.snapshot(now) is False
        assert await opsrevenue.read_snapshot(now) is None

    @pytest.mark.asyncio
    async def test_revenue_compares_mrr_with_earlier_snapshots(self, configured):
        now = datetime.now(timezone.utc)
        month_ago = now - timedelta(days=30)
        await notify._cache.set(opsrevenue.snap_key(month_ago.strftime("%Y%m%d")),
                                json.dumps({"active": 3, "paid": 2, "comped": 1,
                                            "mrr": {"USD": 6.66}}))
        await opsindex.index_subscription(None, paid())
        text = await opsrevenue.revenue_text(now)
        assert "30 days ago $6.66 · 2 paid" in text
        assert "MRR now ≈ $3.33 · 1 paid" in text

    @pytest.mark.asyncio
    async def test_churn_rate_is_over_paid_subscribers_on_the_first(self, configured):
        now = datetime.now(timezone.utc)
        first = now.replace(day=1)
        await notify._cache.set(opsrevenue.snap_key(first.strftime("%Y%m%d")),
                                json.dumps({"paid": 20, "mrr": {}}))
        await notify.subscription_event(FakeNotification(
            paid(expires_in=-60), notification_type="EXPIRED", subtype="VOLUNTARY",
            expiry=True))
        text = await opsrevenue.revenue_text(now)
        assert "churned 1 of 20 paid (5.0%) — 1 chose to leave" in text

    @pytest.mark.asyncio
    async def test_a_month_without_a_start_snapshot_gives_no_rate(self, configured):
        await notify.subscription_event(FakeNotification(
            paid(expires_in=-60), notification_type="EXPIRED", expiry=True))
        text = await opsrevenue.revenue_text()
        assert "churned 1" in text and "%" not in text.split("churned 1")[1].split("\n")[0]

    @pytest.mark.asyncio
    async def test_the_index_cap_is_called_out(self, configured, monkeypatch):
        monkeypatch.setattr(opsindex, "SUBS_INDEX_CAP", 1)
        await opsindex.index_subscription(None, paid())
        assert "cap" in await opsrevenue.revenue_text()


class TestDollarTotal:
    """One approximate total beside the per-currency amounts (#397)."""

    def test_mixed_currencies_get_a_fixed_rate_dollar_total(self):
        from eval import fx
        table = fx.load()
        on = max(table.rates)
        expected = 9.99 + table.to_usd(29.99, "EUR", on) + table.to_usd(49.99, "RON", on)
        text = opsformat.money_total({"USD": 9.99, "EUR": 29.99, "RON": 49.99})
        assert text.startswith("€29.99 + 49.99 RON + $9.99 (≈ $")
        assert f"≈ ${expected:,.2f})" in text

    def test_dollars_alone_need_no_total(self):
        assert opsformat.money_total({"USD": 9.99}) == "$9.99"
        assert opsformat.money_total({}) == "0"

    def test_a_currency_without_a_rate_is_named_not_guessed(self):
        assert opsformat.money_total({"USD": 9.99, "JPY": 1500.0}) == (
            "1,500.00 JPY + $9.99 (≈ $9.99 excl. JPY)")
        assert opsformat.money_total({"JPY": 1500.0}) == "1,500.00 JPY"

    @pytest.mark.asyncio
    async def test_revenue_shows_the_total_and_its_rate_day(self, configured):
        await notify.subscription_event(FakeNotification(paid(), paid_period=True))
        await notify.subscription_event(FakeNotification(
            paid("otid-2", price=44.99, currency="EUR"), uuid="u2", paid_period=True))
        text = await opsrevenue.revenue_text()
        assert "€44.99 + $39.99 (≈ $" in text
        assert "ECB rates of " in text


class TestChurnRate:
    def test_unknown_or_zero_start_is_no_rate(self):
        assert opsrevenue.churn_rate(3, None) is None
        assert opsrevenue.churn_rate(3, 0) is None

    def test_rate(self):
        assert opsrevenue.churn_rate(1, 4) == 0.25


class TestCommand:
    @pytest.mark.asyncio
    async def test_revenue_is_a_command(self, configured):
        assert "revenue" in dict(notify.COMMANDS)
        reply = await notify.handle_command("/revenue")
        assert reply is not None and reply.startswith("📈 <b>Revenue</b>")


class TestBackfill:
    """`/revenue backfill`: Apple's notification history, replayed."""

    @staticmethod
    def note(uuid: str, kind: str, ent, signed: datetime, subtype=None):
        from billing import appstorenotify
        return appstorenotify.Notification(
            notification_type=kind, subtype=subtype, uuid=uuid, entitlement=ent,
            signed_date=int(signed.timestamp()))

    @pytest.fixture
    def apple(self, monkeypatch):
        """Stands in for Apple: `history` is what Get Notification History
        returns, each payload a key into `notes`."""
        from billing import appstorenotify, appstorestatus
        from billing.entitlements import EntitlementError

        state = {"notes": {}, "history": [], "truncated": False, "configured": True}

        class Creds:
            bundle_id = "eu.snapworth.app"

        monkeypatch.setattr(appstorestatus, "credentials_from_env",
                            lambda: Creds() if state["configured"] else None)

        async def payloads(start_ms, end_ms):
            return list(state["history"]), state["truncated"]
        monkeypatch.setattr(appstorestatus, "notification_payloads", payloads)

        def parse(payload, bundle_id, *a, **k):
            if payload not in state["notes"]:
                raise EntitlementError("does not verify")
            return state["notes"][payload]
        monkeypatch.setattr(appstorenotify, "parse_notification", parse)
        return state

    @pytest.mark.asyncio
    async def test_past_notifications_land_in_their_own_month(self, configured, apple):
        now = datetime.now(timezone.utc)
        two_months_ago = now - timedelta(days=62)
        apple["notes"] = {
            "p1": self.note("n1", "DID_RENEW", paid(), two_months_ago),
            "p2": self.note("n2", "EXPIRED", paid("o2", expires_in=-60), two_months_ago,
                            subtype="VOLUNTARY"),
            "p3": self.note("n3", "TEST", None, two_months_ago),
        }
        apple["history"] = ["p1", "p2", "p3", "bad"]
        text = await opsrevenue.backfill(now)
        assert "4 notifications" in text and "2 counted" in text
        assert "1 not revenue" in text and "1 did not verify" in text
        then = opsrevenue.month(two_months_ago)
        assert int(await notify._cache.get(opsrevenue.month_key(then, "gross:USD")) or 0) == 3999
        assert int(await notify._cache.get(opsrevenue.month_key(then, "churned")) or 0) == 1
        assert await counter("payments") == 0      # not this month

    @pytest.mark.asyncio
    async def test_running_it_twice_counts_nothing_twice(self, configured, apple):
        now = datetime.now(timezone.utc)
        apple["notes"] = {"p1": self.note("n1", "DID_RENEW", paid(), now - timedelta(days=40))}
        apple["history"] = ["p1"]
        await opsrevenue.backfill(now)
        text = await opsrevenue.backfill(now)
        assert "0 counted · 1 already counted" in text
        then = opsrevenue.month(now - timedelta(days=40))
        assert int(await notify._cache.get(opsrevenue.month_key(then, "payments")) or 0) == 1

    @pytest.mark.asyncio
    async def test_a_notification_counted_live_is_not_counted_again(self, configured, apple):
        now = datetime.now(timezone.utc)
        live = self.note("n1", "DID_RENEW", paid(), now)
        await notify.subscription_event(live)
        apple["notes"] = {"p1": live}
        apple["history"] = ["p1"]
        text = await opsrevenue.backfill(now)
        assert "0 counted · 1 already counted" in text
        assert await counter("payments") == 1

    @pytest.mark.asyncio
    async def test_no_credentials_says_so(self, configured, apple):
        apple["configured"] = False
        assert "did not run" in await opsrevenue.backfill()

    @pytest.mark.asyncio
    async def test_a_cut_short_history_says_so(self, configured, apple):
        apple["truncated"] = True
        assert "page limit" in await opsrevenue.backfill()

    @pytest.mark.asyncio
    async def test_the_command_asks_first(self, configured, apple):
        reply = await notify.handle_command_with_buttons("/revenue backfill")
        assert reply is not None
        text, buttons = reply
        assert "Fill in past months" in text
        assert ("✅ Yes, backfill", "revenue backfill yes") in buttons[0]
        reply = await notify.handle_command("/revenue backfill yes")
        assert reply is not None and "Backfill" in reply
