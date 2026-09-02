"""Telegram operator alerts.

The contract under test is mostly *restraint*: the notifier must be silent when
unconfigured, silent on re-syncs of a subscription it has already announced,
throttled during a flapping incident, and incapable of failing the request that
triggered it. The happy path is one HTTP POST; everything else is the point.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify  # noqa: E402
import observability  # noqa: E402
from cache import InMemoryCache, ResilientCache  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from entitlements import FREE, Entitlement  # noqa: E402

# Shaped like a real BotFather token; used to prove it never reaches the logs.
FAKE_TOKEN = "123456789:AAtest-token-abcdefghijklmnopqrstuvwx"
FAKE_CHAT = "424242"

SUBJECT = "a" * 64


def pro_entitlement(otid: str = "otid-1", product: str = "com.snapworth.yearly") -> Entitlement:
    return Entitlement("pro", product, int(time.time()) + 86_400, otid, "Production")


class Recorder:
    """Captures every sendMessage payload the notifier posts."""

    def __init__(self, status_code: int = 200) -> None:
        self.requests: list[dict] = []
        self.status_code = status_code

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {"url": str(request.url), "body": json.loads(request.content)})
        return httpx.Response(self.status_code, json={"ok": self.status_code == 200})

    @property
    def texts(self) -> list[str]:
        return [r["body"]["text"] for r in self.requests]


@pytest.fixture
def cache() -> ResilientCache:
    return ResilientCache(None, InMemoryCache())


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest_asyncio.fixture
async def enabled_notify(cache, recorder):
    """notify configured against an in-memory cache and a mock transport."""
    notifier = notify.TelegramNotifier(
        FAKE_TOKEN, FAKE_CHAT,
        client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)))
    notify.configure(cache, notifier=notifier)
    yield recorder
    await notify.aclose()


async def drain() -> None:
    """Let fire-and-forget tasks run to completion."""
    pending = [t for t in notify._tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ── Disabled by default ──────────────────────────────────────────────────────

class TestDisabled:
    @pytest.mark.asyncio
    async def test_unset_env_disables_everything(self, cache, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        notify.configure(cache)
        try:
            assert not notify.enabled()
            # Every public entry point must be a harmless no-op.
            notify.count_scan("pro")
            notify.count_scan_failure()
            notify.model_unhealthy("exhausted")
            notify.model_recovered()
            await notify.entitlement_recorded(SUBJECT, pro_entitlement())
            assert await notify.send_digest() is False
            assert notify._tasks == set()
        finally:
            await notify.aclose()

    @pytest.mark.asyncio
    async def test_token_without_chat_id_stays_disabled(self, cache, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        notify.configure(cache)
        try:
            assert not notify.enabled()
        finally:
            await notify.aclose()


# ── Transport ────────────────────────────────────────────────────────────────

class TestSend:
    @pytest.mark.asyncio
    async def test_posts_to_the_bot_api_with_the_chat_id(self, enabled_notify):
        assert await notify._notifier.send("hello") is True
        (req,) = enabled_notify.requests
        assert req["url"].endswith("/sendMessage")
        assert FAKE_TOKEN in req["url"]          # that is the Bot API's shape
        assert req["body"]["chat_id"] == FAKE_CHAT
        assert req["body"]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_http_error_returns_false_and_never_raises(self, cache, caplog):
        recorder = Recorder(status_code=500)
        notifier = notify.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)))
        notify.configure(cache, notifier=notifier)
        try:
            with caplog.at_level("WARNING"):
                assert await notify._notifier.send("x") is False
            assert FAKE_TOKEN not in caplog.text
        finally:
            await notify.aclose()

    @pytest.mark.asyncio
    async def test_transport_error_logs_class_name_not_the_token(self, cache, caplog):
        def explode(request: httpx.Request) -> httpx.Response:
            # httpx errors quote the request URL, which carries the bot token —
            # exactly what must never reach a log line.
            raise httpx.ConnectError("boom", request=request)

        notifier = notify.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(explode)))
        notify.configure(cache, notifier=notifier)
        try:
            with caplog.at_level("WARNING"):
                assert await notify._notifier.send("x") is False
            assert FAKE_TOKEN not in caplog.text
            assert "ConnectError" in caplog.text
        finally:
            await notify.aclose()

    def test_log_redaction_catches_a_leaked_bot_token(self):
        # Backstop for any future code path that logs an httpx error verbatim.
        leaked = f"ConnectError for url https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"
        assert FAKE_TOKEN not in observability.redact(leaked)


# ── Subscription events ──────────────────────────────────────────────────────

class TestSubscriptionEvents:
    @pytest.mark.asyncio
    async def test_first_sighting_announces_once(self, enabled_notify):
        await notify.entitlement_recorded(SUBJECT, pro_entitlement())
        # Re-syncs and renewals share the originalTransactionId: cold launch,
        # restore and Transaction.updates all re-POST the same subscription.
        await notify.entitlement_recorded(SUBJECT, pro_entitlement())
        await notify.entitlement_recorded("b" * 64, pro_entitlement())

        assert len(enabled_notify.texts) == 1
        assert "New Pro subscription" in enabled_notify.texts[0]
        assert "com.snapworth.yearly" in enabled_notify.texts[0]

    @pytest.mark.asyncio
    async def test_a_different_subscription_announces_again(self, enabled_notify):
        await notify.entitlement_recorded(SUBJECT, pro_entitlement("otid-1"))
        await notify.entitlement_recorded(SUBJECT, pro_entitlement("otid-2"))
        assert len(enabled_notify.texts) == 2

    @pytest.mark.asyncio
    async def test_pro_without_a_transaction_id_stays_silent(self, enabled_notify):
        ent = Entitlement("pro", "com.snapworth.yearly", None, None, "Production")
        await notify.entitlement_recorded(SUBJECT, ent)
        assert enabled_notify.texts == []

    @pytest.mark.asyncio
    async def test_downgrade_pings_once_per_subject_per_day(self, enabled_notify):
        await notify.entitlement_recorded(SUBJECT, FREE)
        await notify.entitlement_recorded(SUBJECT, FREE)
        assert len(enabled_notify.texts) == 1
        assert "Subscription ended" in enabled_notify.texts[0]
        # Pseudonymised, never the raw App Attest subject.
        assert SUBJECT not in enabled_notify.texts[0]

    @pytest.mark.asyncio
    async def test_counts_toward_the_digest(self, enabled_notify, cache):
        await notify.entitlement_recorded(SUBJECT, pro_entitlement())
        day = notify._day()
        assert await cache.get(notify._stat_key(day, "new_subs")) == "1"


# ── Operational alerts ───────────────────────────────────────────────────────

class TestModelHealthAlerts:
    @pytest.mark.asyncio
    async def test_degraded_alert_is_throttled(self, enabled_notify):
        notify.model_unhealthy("exhausted")
        notify.model_unhealthy("exhausted")       # flapping upstream
        await drain()
        assert len(enabled_notify.texts) == 1
        assert "degraded" in enabled_notify.texts[0]

    @pytest.mark.asyncio
    async def test_quota_exhaustion_names_the_remedy(self, enabled_notify):
        notify.model_unhealthy("quota_exhausted")
        await drain()
        assert "top up" in enabled_notify.texts[0]

    @pytest.mark.asyncio
    async def test_recovery_only_follows_an_alert(self, enabled_notify):
        notify.model_recovered()                   # no incident announced
        await drain()
        assert enabled_notify.texts == []

        notify.model_unhealthy("exhausted")
        notify.model_recovered()
        notify.model_recovered()                   # once per incident
        await drain()
        assert len(enabled_notify.texts) == 2
        assert "recovered" in enabled_notify.texts[1]

    @pytest.mark.asyncio
    async def test_a_relapse_after_recovery_alerts_immediately(self, enabled_notify):
        notify.model_unhealthy("exhausted")
        notify.model_recovered()
        # Within the throttle window, but a *new* incident: recovery cleared
        # the throttle precisely so this is not mistaken for a repeat.
        notify.model_unhealthy("exhausted")
        await drain()
        assert len(enabled_notify.texts) == 3


# ── Deploy ping ──────────────────────────────────────────────────────────────

class TestDeployPing:
    @pytest.mark.asyncio
    async def test_announces_a_commit_once(self, enabled_notify):
        notify.deployed("51c74bb58048", cache_backend="redis", auth_enforcing=True)
        # Second replica, or Railway restarting the same build.
        notify.deployed("51c74bb58048", cache_backend="redis", auth_enforcing=True)
        await drain()
        assert len(enabled_notify.texts) == 1
        text = enabled_notify.texts[0]
        assert "deployed" in text
        assert "51c74bb58048" in text
        assert "redis" in text
        assert "auth enforcing" in text

    @pytest.mark.asyncio
    async def test_a_new_commit_announces_again(self, enabled_notify):
        notify.deployed("aaaaaaaaaaaa", cache_backend="redis", auth_enforcing=True)
        notify.deployed("bbbbbbbbbbbb", cache_backend="redis", auth_enforcing=True)
        await drain()
        assert len(enabled_notify.texts) == 2

    @pytest.mark.asyncio
    async def test_calls_out_enforcement_being_off(self, enabled_notify):
        # The one deploy-time misconfiguration worth shouting about.
        notify.deployed("cccccccccccc", cache_backend="memory", auth_enforcing=False)
        await drain()
        assert "NOT enforcing" in enabled_notify.texts[0]

    @pytest.mark.asyncio
    async def test_disabled_is_a_no_op(self, cache, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        notify.configure(cache)
        try:
            notify.deployed("dddddddddddd", cache_backend="redis", auth_enforcing=True)
            assert notify._tasks == set()
        finally:
            await notify.aclose()


# ── Daily digest ─────────────────────────────────────────────────────────────

class TestDigest:
    @pytest.mark.asyncio
    async def test_reports_yesterdays_counters(self, enabled_notify, cache):
        now = datetime.now(timezone.utc)
        notify.count_scan("free")
        notify.count_scan("pro")
        notify.count_scan("pro")
        notify.count_scan_failure()
        await drain()

        # The digest reads *yesterday*; today's counters were just written, so
        # ask for it as if it were tomorrow morning.
        from datetime import timedelta
        sent = await notify.send_digest(now=now + timedelta(days=1))
        assert sent is True
        digest = enabled_notify.texts[-1]
        assert "3 ok" in digest
        assert "1 free · 2 Pro" in digest
        assert "1 failed" in digest
        assert "New subscriptions: 0" in digest

    @pytest.mark.asyncio
    async def test_only_one_replica_sends(self, enabled_notify):
        assert await notify.send_digest() is True
        # Same day, second replica: the NX guard already belongs to the first.
        assert await notify.send_digest() is False
        assert len(enabled_notify.texts) == 1

    @pytest.mark.asyncio
    async def test_a_quiet_day_still_reports(self, enabled_notify):
        # Silence would be indistinguishable from a broken notifier.
        assert await notify.send_digest() is True
        assert "0 ok" in enabled_notify.texts[0]

    def test_schedule_math(self):
        hour6 = datetime(2026, 9, 2, 4, 30, tzinfo=timezone.utc)
        assert notify._seconds_until_next(6, hour6) == 90 * 60
        # At or past the hour, the next firing is tomorrow.
        at6 = datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc)
        assert notify._seconds_until_next(6, at6) == 24 * 60 * 60

    def test_digest_hour_is_clamped_and_defaulted(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_DIGEST_UTC_HOUR", "99")
        assert notify._digest_hour() == 23
        monkeypatch.setenv("TELEGRAM_DIGEST_UTC_HOUR", "not-a-number")
        assert notify._digest_hour() == notify.DEFAULT_DIGEST_UTC_HOUR


# ── Subscription sharing signal ──────────────────────────────────────────────

class TestSharingSignal:
    @pytest.mark.asyncio
    async def test_recent_eviction_alerts_once_per_subscription_per_day(self, enabled_notify):
        notify.subscription_over_cap("otid-1", "com.snapworth.yearly",
                                     idle_seconds=2 * 3600, max_devices=6)
        notify.subscription_over_cap("otid-1", "com.snapworth.yearly",
                                     idle_seconds=3600, max_devices=6)   # steady churn
        await drain()
        assert len(enabled_notify.texts) == 1
        text = enabled_notify.texts[0]
        assert "device cap" in text
        assert "com.snapworth.yearly" in text
        assert "more than 6 devices" in text

    @pytest.mark.asyncio
    async def test_a_long_idle_eviction_is_a_replaced_phone_not_sharing(self, enabled_notify):
        notify.subscription_over_cap("otid-1", "com.snapworth.yearly",
                                     idle_seconds=notify.SHARING_RECENT_SECONDS + 1,
                                     max_devices=6)
        await drain()
        assert enabled_notify.texts == []

    @pytest.mark.asyncio
    async def test_different_subscriptions_alert_independently(self, enabled_notify):
        notify.subscription_over_cap("otid-1", None, idle_seconds=60, max_devices=6)
        notify.subscription_over_cap("otid-2", None, idle_seconds=60, max_devices=6)
        await drain()
        assert len(enabled_notify.texts) == 2

    @pytest.mark.asyncio
    async def test_disabled_is_a_no_op(self, cache, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        notify.configure(cache)
        try:
            notify.subscription_over_cap("otid-1", None, idle_seconds=60, max_devices=6)
            assert notify._tasks == set()
        finally:
            await notify.aclose()


# ── New customer vs existing subscriber ──────────────────────────────────────
# Every existing subscriber is announced exactly once after the notifier
# deploys. Calling that a "new subscription" misreports sales; the original
# purchase date is what tells the two apart.

class TestNewVersusExisting:
    @pytest.mark.asyncio
    async def test_bought_today_is_new(self, enabled_notify, cache):
        ent = Entitlement("pro", "com.snapworth.monthly", int(time.time()) + 30 * 86_400,
                          "otid-new", "Production",
                          original_purchase_at=int(time.time()) - 600)
        await notify.entitlement_recorded(SUBJECT, ent)
        text = enabled_notify.texts[0]
        assert "New Pro subscription" in text
        assert "first purchased" in text
        assert "renews or expires" in text
        assert await cache.get(notify._stat_key(notify._day(), "new_subs")) == "1"

    @pytest.mark.asyncio
    async def test_bought_weeks_ago_is_an_existing_subscriber(self, enabled_notify, cache):
        ent = Entitlement("pro", "com.snapworth.monthly", int(time.time()) + 5 * 86_400,
                          "otid-old", "Production",
                          original_purchase_at=int(time.time()) - 40 * 86_400)
        await notify.entitlement_recorded(SUBJECT, ent)
        text = enabled_notify.texts[0]
        assert "Existing Pro subscriber" in text
        assert "New Pro subscription" not in text
        assert "first purchased" in text
        # Not a sale: must not inflate the digest.
        assert await cache.get(notify._stat_key(notify._day(), "new_subs")) is None

    @pytest.mark.asyncio
    async def test_existing_subscriber_is_still_announced_only_once(self, enabled_notify):
        ent = Entitlement("pro", "com.snapworth.monthly", int(time.time()) + 5 * 86_400,
                          "otid-old", "Production",
                          original_purchase_at=int(time.time()) - 40 * 86_400)
        await notify.entitlement_recorded(SUBJECT, ent)
        await notify.entitlement_recorded(SUBJECT, ent)
        assert len(enabled_notify.texts) == 1

    def test_dates_render_unambiguously(self):
        assert notify._date(1_788_220_800) == "01 Sep 2026"
