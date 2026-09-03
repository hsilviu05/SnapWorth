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
        path = request.url.path
        if path.endswith("/getUpdates"):
            # The command loop polls continuously; an empty inbox is the
            # normal answer and is not a message the tests care about.
            return httpx.Response(200, json={"ok": True, "result": []})
        body = json.loads(request.content) if request.content else {}
        self.requests.append({"url": str(request.url), "path": path, "body": body})
        return httpx.Response(self.status_code, json={"ok": self.status_code == 200})

    @property
    def sends(self) -> list[dict]:
        return [r for r in self.requests if r["path"].endswith("/sendMessage")]

    @property
    def texts(self) -> list[str]:
        return [r["body"]["text"] for r in self.sends]


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
        (req,) = enabled_notify.sends
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


# ── Activity and the /status command ─────────────────────────────────────────
# "Online" does not exist for a request/response API; what the bot reports is
# distinct devices seen in the current 15-minute window and today.

class TestActivity:
    @pytest.mark.asyncio
    async def test_counts_distinct_devices_not_requests(self, enabled_notify, cache):
        for _ in range(5):
            notify.saw_user("a" * 64)
        notify.saw_user("b" * 64)
        await drain()
        assert await cache.get(f"opsact:w:{notify._window()}") == "2"
        assert await cache.get(notify._stat_key(notify._day(), "active_users")) == "2"

    @pytest.mark.asyncio
    async def test_stores_pseudonyms_not_subjects(self, enabled_notify, cache):
        notify.saw_user("a" * 64)
        await drain()
        keys = list(cache._fallback._data)
        assert not any(("a" * 64) in k for k in keys)

    @pytest.mark.asyncio
    async def test_disabled_is_a_no_op(self, cache, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        notify.configure(cache)
        try:
            notify.saw_user("a" * 64)
            assert notify._tasks == set()
        finally:
            await notify.aclose()


class TestCommands:
    @pytest.mark.asyncio
    async def test_status_reports_activity_scans_and_process_facts(self, cache, recorder):
        notifier = notify.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)))
        notify.configure(cache, notifier=notifier, status_provider=lambda: {
            "commit": "abc123def456", "cache": "redis", "auth_enforcing": True,
            "model_healthy": False, "model_failure_kind": "quota_exhausted"})
        try:
            notify.saw_user("a" * 64)
            notify.count_scan("pro")
            await drain()
            text = await notify.handle_command("/status")
            assert "Active users: 1 since" in text
            assert "1 today" in text
            assert "Scans today: 1 ok (0 free · 1 Pro)" in text
            assert "degraded (quota_exhausted)" in text
            assert "abc123def456" in text
            assert "auth enforcing" in text
        finally:
            await notify.aclose()

    @pytest.mark.asyncio
    async def test_status_without_a_provider_still_answers(self, enabled_notify):
        text = await notify.handle_command("/status@SnapWorthBot")
        assert text.startswith("📡")
        assert "Build" not in text

    @pytest.mark.asyncio
    async def test_digest_on_demand_and_help(self, enabled_notify):
        assert (await notify.handle_command("/digest")).startswith("📊")
        assert "/status" in await notify.handle_command("/help")
        assert "/status" in await notify.handle_command("/start")
        assert "/status" in await notify.handle_command("/nonsense")
        assert await notify.handle_command("hello there") is None


class TestPolling:
    class Bot:
        """Mock Telegram: serves one batch of updates, records replies."""

        def __init__(self, updates: list[dict]) -> None:
            self.updates = updates
            self.replies: list[str] = []
            self.markups: list[dict | None] = []
            self.polls: list[dict] = []
            self.command_menus: list[list] = []
            self.answered: list[str] = []

        def handler(self, request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/getUpdates"):
                self.polls.append(dict(request.url.params))
                batch, self.updates = self.updates, []
                return httpx.Response(200, json={"ok": True, "result": batch})
            if path.endswith("/sendMessage"):
                body = json.loads(request.content)
                self.replies.append(body["text"])
                self.markups.append(body.get("reply_markup"))
                return httpx.Response(200, json={"ok": True})
            if path.endswith("/setMyCommands"):
                self.command_menus.append(json.loads(request.content)["commands"])
                return httpx.Response(200, json={"ok": True})
            if path.endswith("/answerCallbackQuery"):
                self.answered.append(json.loads(request.content)["callback_query_id"])
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404)

    @staticmethod
    def update(update_id: int, chat_id: str, text: str) -> dict:
        return {"update_id": update_id,
                "message": {"chat": {"id": int(chat_id)}, "text": text}}

    @pytest.mark.asyncio
    async def test_answers_the_operator_and_nobody_else(self, cache):
        bot = self.Bot([
            self.update(100, "999999", "/status"),      # a stranger
            self.update(101, FAKE_CHAT, "/status"),     # the operator
            self.update(102, FAKE_CHAT, "thanks"),      # not a command
        ])
        notifier = notify.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(bot.handler)))
        notify.configure(cache, notifier=notifier)
        try:
            offset, handled = await notify.poll_once(None)
            assert offset == 103, "the next poll must acknowledge everything seen"
            assert handled == 1
            assert len(bot.replies) == 1
            assert bot.replies[0].startswith("📡")
            # Second round sends the offset so Telegram drops the acknowledged
            # updates, and finds nothing new.
            offset, handled = await notify.poll_once(offset)
            assert bot.polls[-1]["offset"] == "103"
            assert (offset, handled) == (103, 0)
        finally:
            await notify.aclose()

    @pytest.mark.asyncio
    async def test_command_menu_is_published_on_configure(self, cache):
        bot = self.Bot([])
        notifier = notify.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(bot.handler)))
        notify.configure(cache, notifier=notifier)
        try:
            await drain()
            (menu,) = bot.command_menus
            assert [c["command"] for c in menu] == [
                "status", "subs", "users", "costs", "feed", "digest", "week", "help"]
        finally:
            await notify.aclose()

    @pytest.mark.asyncio
    async def test_only_one_replica_polls(self, enabled_notify, cache):
        assert await notify._hold_poll_lock() is True
        assert await notify._hold_poll_lock() is True, "the holder renews its own lock"
        await cache.set(notify.POLL_LOCK_KEY, "another-replica", 60)
        assert await notify._hold_poll_lock() is False

    @pytest.mark.asyncio
    async def test_poll_failure_is_quiet(self, cache, caplog):
        def explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        notifier = notify.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(explode)))
        notify.configure(cache, notifier=notifier)
        try:
            with caplog.at_level("WARNING"):
                assert await notify.poll_once(None) == (None, 0)
            assert FAKE_TOKEN not in caplog.text
        finally:
            await notify.aclose()


# ── Live scan feed, top categories/brands, weekly report, buttons ────────────

def scan(**overrides):
    kw = dict(tier="pro", item_name="Patagonia Better Sweater 1/4-Zip", brand="Patagonia",
              category="clothing", low=35.0, high=60.0, confidence="High")
    kw.update(overrides)
    notify.scan_completed(**kw)


class TestScanFeed:
    @pytest.mark.asyncio
    async def test_feed_message_is_item_and_price_only(self, enabled_notify):
        scan()
        await drain()
        (text,) = enabled_notify.texts
        assert text.startswith("🧥 <b>Patagonia Better Sweater 1/4-Zip</b>")
        assert "clothing · $35–60 · high confidence · Pro" in text

    @pytest.mark.asyncio
    async def test_model_output_is_escaped(self, enabled_notify):
        scan(item_name="<b>Nike</b> & <script>x</script>")
        await drain()
        text = enabled_notify.texts[0]
        assert "<script>" not in text
        assert "&lt;script&gt;" in text

    @pytest.mark.asyncio
    async def test_unknown_category_falls_back(self, enabled_notify):
        scan(category="Weird Stuff", confidence="")
        await drain()
        assert enabled_notify.texts[0].startswith("📦")
        assert "other ·" in enabled_notify.texts[0]
        assert "unknown confidence" in enabled_notify.texts[0]

    @pytest.mark.asyncio
    async def test_feed_off_still_counts(self, enabled_notify, cache):
        assert "off" in await notify.handle_command("/feed off")
        scan()
        await drain()
        assert [t for t in enabled_notify.texts if t.startswith("🧥")] == []
        assert await cache.get(notify._stat_key(notify._day(), "scans_pro")) == "1"
        assert "on" in await notify.handle_command("/feed on")
        scan()
        await drain()
        assert any(t.startswith("🧥") for t in enabled_notify.texts)

    @pytest.mark.asyncio
    async def test_feed_toggle_and_state(self, enabled_notify):
        assert "<b>on</b>" in await notify.handle_command("/feed")
        assert "<b>off</b>" in await notify.handle_command("/feed toggle")
        assert "<b>off</b>" in await notify.handle_command("/feed")


class TestTopCategoriesAndBrands:
    @pytest.mark.asyncio
    async def test_status_and_digest_show_the_days_top(self, enabled_notify):
        scan(category="clothing", brand="Nike")
        scan(category="clothing", brand="Nike")
        scan(category="shoes", brand="Nike")
        scan(category="clothing", brand="Zara")
        scan(category="toys", brand="Unknown")
        await drain()
        status = await notify.handle_command("/status")
        assert "Top: clothing 3 · shoes 1 · toys 1 — Nike ×3, Zara ×1" in status
        from datetime import timedelta
        digest = await notify.handle_command_with_buttons("/digest")
        assert digest is not None
        # Yesterday has no tallies, so the digest omits the line rather than
        # printing an empty one.
        assert "Top:" not in digest[0]
        assert "Top:" in await notify._digest_text(datetime.now(timezone.utc))
        del timedelta

    @pytest.mark.asyncio
    async def test_brand_table_is_capped(self, enabled_notify, cache):
        for i in range(notify.TOP_BRANDS_CAP + 5):
            scan(brand=f"Brand{i}")
        await drain()
        doc = json.loads(await cache.get(notify._stat_key(notify._day(), "top")))
        assert len(doc["brands"]) == notify.TOP_BRANDS_CAP
        assert doc["cats"]["clothing"] == notify.TOP_BRANDS_CAP + 5


class TestWeeklyReport:
    async def seed(self, cache, now, name, this_week, last_week):
        from datetime import timedelta
        end = (now - timedelta(days=1)).date()
        for i in range(7):
            day = notify._day(datetime.combine(end - timedelta(days=i),
                                               datetime.min.time(), tzinfo=timezone.utc))
            await cache.set(notify._stat_key(day, name), str(this_week[i]))
        for i in range(7):
            day = notify._day(datetime.combine(end - timedelta(days=7 + i),
                                               datetime.min.time(), tzinfo=timezone.utc))
            await cache.set(notify._stat_key(day, name), str(last_week[i]))

    @pytest.mark.asyncio
    async def test_compares_the_last_seven_days_to_the_seven_before(self, enabled_notify, cache):
        now = datetime(2026, 9, 7, 6, 0, tzinfo=timezone.utc)   # a Monday
        await self.seed(cache, now, "scans_pro", [3] * 7, [2] * 7)      # 21 vs 14
        await self.seed(cache, now, "scans_free", [1] * 7, [2] * 7)     # 7 vs 14
        await self.seed(cache, now, "active_users", [2] * 7, [2] * 7)   # 14 vs 14
        await self.seed(cache, now, "new_subs", [0] * 6 + [1], [0] * 7) # 1 vs 0
        text = await notify._weekly_text(now)
        assert text.startswith("📈 <b>Week 31 Aug – 06 Sep</b>")
        assert "Scans: 28 (7 free · 21 Pro) ＝" in text          # 28 vs 28
        assert "Active user-days: 14 ＝" in text
        assert "New subscriptions: 1 new" in text
        assert "vs 28 scans · 14 user-days · 0 subs · $0.00 the week before" in text

    def test_trend_arrows(self):
        assert notify._trend(15, 10) == "▲ 50%"
        assert notify._trend(5, 10) == "▼ 50%"
        assert notify._trend(10, 10) == "＝"
        assert notify._trend(3, 0) == "new"
        assert notify._trend(0, 0) == "—"

    @pytest.mark.asyncio
    async def test_sent_once_per_monday(self, enabled_notify):
        now = datetime(2026, 9, 7, 6, 0, tzinfo=timezone.utc)
        assert await notify.send_weekly(now) is True
        assert await notify.send_weekly(now) is False
        assert len([t for t in enabled_notify.texts if t.startswith("📈")]) == 1

    @pytest.mark.asyncio
    async def test_week_command(self, enabled_notify):
        assert (await notify.handle_command("/week")).startswith("📈")


class TestButtons:
    @pytest.mark.asyncio
    async def test_replies_carry_the_keyboard(self, enabled_notify):
        text, buttons = await notify.handle_command_with_buttons("/status")
        labels = [label for row in buttons for label, _ in row]
        assert labels == ["🔄 Refresh", "📊 Digest", "📈 Week",
                          "💳 Subs", "👥 Users", "💸 Costs", "🔕 Feed off"]
        await notify.handle_command("/feed off")
        _, buttons = await notify.handle_command_with_buttons("/status")
        assert buttons[2][0][0] == "🔔 Feed on"

    @pytest.mark.asyncio
    async def test_button_press_is_answered_and_acted_on(self, cache):
        bot = TestPolling.Bot([
            {"update_id": 7, "callback_query": {
                "id": "cb-1", "data": "week",
                "message": {"chat": {"id": int(FAKE_CHAT)}}}},
            {"update_id": 8, "callback_query": {
                "id": "cb-2", "data": "status",
                "message": {"chat": {"id": 999999}}}},         # a stranger's press
        ])
        notifier = notify.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(bot.handler)))
        notify.configure(cache, notifier=notifier)
        try:
            offset, handled = await notify.poll_once(None)
            assert (offset, handled) == (9, 1)
            assert bot.answered == ["cb-1"], "the spinner stops; the stranger gets nothing"
            assert bot.replies[0].startswith("📈")
            assert bot.markups[0]["inline_keyboard"][0][0]["callback_data"] == "status"
        finally:
            await notify.aclose()

    @pytest.mark.asyncio
    async def test_feed_button_toggles(self, cache):
        bot = TestPolling.Bot([
            {"update_id": 1, "callback_query": {
                "id": "cb", "data": "feed toggle",
                "message": {"chat": {"id": int(FAKE_CHAT)}}}},
        ])
        notifier = notify.TelegramNotifier(
            FAKE_TOKEN, FAKE_CHAT,
            client=httpx.AsyncClient(transport=httpx.MockTransport(bot.handler)))
        notify.configure(cache, notifier=notifier)
        try:
            await notify.poll_once(None)
            assert "<b>off</b>" in bot.replies[0]
            assert await notify._feed_enabled() is False
        finally:
            await notify.aclose()


# ── Operator tables: /subs and /users ────────────────────────────────────────

def sub(otid, product="com.snapworth.monthly", *, offer_type=None, discount=None,
        price=4.99, currency="USD", first_days_ago=0, expires_in_days=30):
    now = int(time.time())
    return Entitlement("pro", product, now + expires_in_days * 86_400, otid, "Production",
                       original_purchase_at=now - first_days_ago * 86_400,
                       offer_type=offer_type, offer_discount_type=discount,
                       price=price, currency=currency)


class TestSubscriptionsTable:
    @pytest.mark.asyncio
    async def test_separates_paid_from_comped_and_computes_mrr(self, enabled_notify):
        await notify.entitlement_recorded("a" * 64, sub("paid-1"))
        await notify.entitlement_recorded("b" * 64, sub(
            "code-1", "com.snapworth.yearly", offer_type=3, price=0, first_days_ago=40,
            expires_in_days=320))
        await notify.entitlement_recorded("c" * 64, sub(
            "trial-1", "com.snapworth.yearly", offer_type=1, discount="FREE_TRIAL",
            price=0, expires_in_days=3))
        await notify.entitlement_recorded("d" * 64, sub(
            "old-1", expires_in_days=-5, first_days_ago=60))          # lapsed

        text = await notify.handle_command("/subs")
        assert "4 active" not in text
        assert "3 active · 1 paid · 2 comped/trial · 1 expired" in text
        assert "MRR ≈ $4.99" in text
        assert "offer code" in text and "trial" in text and "paid" in text
        assert "ended" in text
        # The digest and status carry the one-line summary.
        status = await notify.handle_command("/status")
        assert "Subscribers: 3 active · 1 paid · 2 comped/trial" in status

    @pytest.mark.asyncio
    async def test_yearly_paid_counts_a_twelfth_toward_mrr(self, enabled_notify):
        await notify.entitlement_recorded("a" * 64, sub(
            "y-1", "com.snapworth.yearly", price=39.99, expires_in_days=300))
        assert "MRR ≈ $3.33" in await notify.handle_command("/subs")

    @pytest.mark.asyncio
    async def test_resync_updates_the_row_without_reannouncing(self, enabled_notify, cache):
        await notify.entitlement_recorded("a" * 64, sub("m-1", expires_in_days=30))
        await notify.entitlement_recorded("a" * 64, sub("m-1", expires_in_days=60))
        assert len([t for t in enabled_notify.texts if "New Pro subscription" in t]) == 1
        doc = json.loads(await cache.get(notify.SUBS_INDEX_KEY))
        assert doc["m-1"]["expires"] > int(time.time()) + 59 * 86_400

    @pytest.mark.asyncio
    async def test_announcement_says_how_it_was_obtained(self, enabled_notify):
        await notify.entitlement_recorded("a" * 64, sub("code-2", offer_type=3, price=0))
        assert "offer code" in enabled_notify.texts[0]

    @pytest.mark.asyncio
    async def test_empty_table_says_so(self, enabled_notify):
        text = await notify.handle_command("/subs")
        assert "0 active" in text and "No subscription has synced" in text

    def test_acquisition_wording(self):
        assert notify._acquisition(sub("x")) == "paid"
        assert notify._acquisition(sub("x", offer_type=3)) == "offer code"
        assert notify._acquisition(sub("x", offer_type=2)) == "promo offer"
        assert notify._acquisition(sub("x", offer_type=1, discount="FREE_TRIAL")) == "trial"
        assert notify._acquisition(sub("x", offer_type=1, discount="PAY_AS_YOU_GO")) == "intro offer"


class TestUsersTable:
    @pytest.mark.asyncio
    async def test_counts_devices_and_ranks_by_scans(self, enabled_notify):
        notify.saw_user("a" * 64, tier="pro")
        notify.saw_user("b" * 64)
        await drain()
        for _ in range(3):
            scan(tier="pro", **{})
        await drain()
        text = await notify.handle_command("/users")
        assert "2 seen · 2 last 30d · 2 last 7d · 2 today · 1 Pro" in text
        assert "<pre>" in text
        assert ("a" * 64) not in text, "pseudonyms only"

    @pytest.mark.asyncio
    async def test_scans_are_attributed_to_the_device(self, enabled_notify, cache):
        notify.scan_completed(tier="free", item_name="x", brand=None, category="toys",
                              low=1, high=2, confidence="Low", subject="q" * 64)
        notify.scan_completed(tier="free", item_name="y", brand=None, category="toys",
                              low=1, high=2, confidence="Low", subject="q" * 64)
        await drain()
        doc = json.loads(await cache.get(notify.USERS_INDEX_KEY))
        (entry,) = doc.values()
        assert entry["scans"] == 2 and entry["tier"] == "free"

    @pytest.mark.asyncio
    async def test_index_is_capped_by_recency(self, enabled_notify, cache):
        for i in range(notify.USERS_INDEX_CAP + 3):
            await notify._index_user(f"dev{i:05d}", tier="free")
        doc = json.loads(await cache.get(notify.USERS_INDEX_KEY))
        assert len(doc) == notify.USERS_INDEX_CAP

    @pytest.mark.asyncio
    async def test_empty_table_says_so(self, enabled_notify):
        assert "No device has been seen" in await notify.handle_command("/users")


# ── Gemini spend, latency, renewals ──────────────────────────────────────────

class TestSpend:
    @pytest.fixture(autouse=True)
    def prices(self, monkeypatch):
        monkeypatch.setattr(notify, "GEMINI_PRICE_INPUT_PER_M", 0.30)
        monkeypatch.setattr(notify, "GEMINI_PRICE_OUTPUT_PER_M", 2.50)
        monkeypatch.setattr(notify, "GEMINI_DAILY_BUDGET_USD", 0.0)

    def test_cost_arithmetic(self):
        # 10K in at $0.30/M = $0.003; 6K out at $2.50/M = $0.015.
        assert abs(notify._cost_usd(10_000, 6_000) - 0.018) < 1e-9

    @pytest.mark.asyncio
    async def test_tokens_accumulate_and_costs_reports_them(self, enabled_notify, cache):
        notify.model_usage("scan", {"prompt_tokens": 10_000, "output_tokens": 5_000,
                                    "thoughts_tokens": 1_000})
        notify.model_usage("listing", {"prompt_tokens": 2_000, "output_tokens": 500})
        scan(elapsed_ms=5_800)
        await drain()
        day = notify._day()
        assert await cache.get(notify._stat_key(day, "tok_in")) == "12000"
        assert await cache.get(notify._stat_key(day, "tok_out")) == "6500"
        assert await cache.get(notify._stat_key(day, "model_calls")) == "2"
        assert await cache.get(notify._stat_key(day, "calls_listing")) == "1"

        text = await notify.handle_command("/costs")
        # 12K × 0.30 + 6.5K × 2.50 per million = 0.0036 + 0.01625 = $0.01985
        assert "Today: $0.02 · 2 calls · 12.0K in / 6.5K out · $0.020/scan" in text
        assert "Prices: $0.30/M in · $2.50/M out" in text
        assert "vs MRR ≈ n/a" in text

    @pytest.mark.asyncio
    async def test_status_and_digest_carry_spend_and_latency(self, enabled_notify):
        notify.model_usage("scan", {"prompt_tokens": 100_000, "output_tokens": 20_000})
        scan(elapsed_ms=4_000)
        scan(elapsed_ms=8_000)
        await drain()
        status = await notify.handle_command("/status")
        assert "Gemini ≈ $0.08 · $0.040/scan · avg scan 6.0s" in status
        digest = await notify._digest_text(datetime.now(timezone.utc))
        assert "Gemini ≈ $0.08" in digest

    @pytest.mark.asyncio
    async def test_free_tier_share_of_spend(self, enabled_notify):
        notify.model_usage("scan", {"prompt_tokens": 1_000_000, "output_tokens": 0})  # $0.30
        scan(tier="free")
        scan(tier="pro")
        scan(tier="pro")
        await drain()
        assert "Free tier, 30 days: 1 of 3 scans ≈ $0.10 given away" in \
            await notify.handle_command("/costs")

    @pytest.mark.asyncio
    async def test_budget_alerts_once_per_day(self, enabled_notify, monkeypatch):
        monkeypatch.setattr(notify, "GEMINI_DAILY_BUDGET_USD", 0.01)
        notify.model_usage("scan", {"prompt_tokens": 0, "output_tokens": 10_000})  # $0.025
        await drain()
        notify.model_usage("scan", {"prompt_tokens": 0, "output_tokens": 10_000})
        await drain()
        alerts = [t for t in enabled_notify.texts if "over budget" in t]
        assert len(alerts) == 1
        assert "$0.03 against a $0.01 daily budget" in alerts[0]
        assert "budget $0.01/day" in await notify.handle_command("/costs")

    @pytest.mark.asyncio
    async def test_weekly_carries_spend_with_trend(self, enabled_notify):
        notify.model_usage("scan", {"prompt_tokens": 1_000_000, "output_tokens": 0})
        await drain()
        from datetime import timedelta
        text = await notify._weekly_text(datetime.now(timezone.utc) + timedelta(days=1))
        assert "Gemini spend: $0.30 new" in text

    @pytest.mark.asyncio
    async def test_disabled_is_a_no_op(self, cache, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        notify.configure(cache)
        try:
            notify.model_usage("scan", {"prompt_tokens": 5})
            assert notify._tasks == set()
        finally:
            await notify.aclose()


class TestRenewalsDue:
    @pytest.mark.asyncio
    async def test_subs_lists_what_renews_this_week(self, enabled_notify):
        await notify.entitlement_recorded("a" * 64, sub("m-1", expires_in_days=3))
        await notify.entitlement_recorded("b" * 64, sub("y-1", "com.snapworth.yearly",
                                                        offer_type=3, price=0, expires_in_days=5))
        await notify.entitlement_recorded("c" * 64, sub("m-2", expires_in_days=20))
        text = await notify.handle_command("/subs")
        assert "Due in 7 days: 2 renew or end (1 paid · $4.99)" in text


class TestCacheIncrAmount:
    @pytest.mark.asyncio
    async def test_in_memory_and_resilient_increment_by_amount(self, cache):
        assert await cache.incr("k", 60, 5) == 5
        assert await cache.incr("k", 60, 7) == 12
        assert await cache.incr("k", 60) == 13


# ── Deploy message details ───────────────────────────────────────────────────

class TestDeployMessage:
    def test_merge_commit_leads_with_the_pr_and_links_it(self):
        info = {"message": "Merge pull request #80 from hsilviu05/claude/x\n\n"
                           "Telegram /subs and /users: who still has a subscription",
                "files": 5, "repository": "hsilviu05/SnapWorth"}
        text = notify._deploy_text("6092732abcde", "redis", True, info)
        assert text.splitlines()[0] == "🚀 <b>Backend deployed</b>"
        assert ('<a href="https://github.com/hsilviu05/SnapWorth/pull/80">#80</a> '
                "Telegram /subs and /users: who still has a subscription") in text
        assert "5 files · commit <code>6092732abcde</code> · cache redis · auth enforcing" in text

    def test_direct_commit_carries_its_first_paragraph(self):
        info = {"message": "fix(backend): sharing alert ignores ghosts\n\n"
                           "First live message after the migration read as sharing.\n"
                           "It was one phone.\n\nSecond paragraph is not shown.",
                "files": 1, "repository": "hsilviu05/SnapWorth"}
        text = notify._deploy_text("abc", "redis", False, info)
        assert "<b>fix(backend): sharing alert ignores ghosts</b>" in text
        assert "First live message after the migration read as sharing. It was one phone." in text
        assert "Second paragraph" not in text
        assert "1 file · commit" in text and "auth NOT enforcing" in text

    def test_no_info_is_the_old_message(self):
        text = notify._deploy_text("abc", "memory", True, None)
        assert text == ("🚀 <b>Backend deployed</b>\n"
                        "commit <code>abc</code> · cache memory · auth enforcing")

    def test_long_bodies_are_truncated_and_escaped(self):
        info = {"message": "feat: <b>bold</b>\n\n" + "x" * 1000, "files": 0}
        text = notify._deploy_text("abc", "redis", True, info)
        assert "&lt;b&gt;bold&lt;/b&gt;" in text
        assert "…" in text and len(text) < 900

    @pytest.mark.asyncio
    async def test_deployed_sends_the_detailed_message(self, enabled_notify):
        notify.deployed("abc123", cache_backend="redis", auth_enforcing=True,
                        info={"message": "chore: bump", "files": 2, "repository": "o/r"})
        await drain()
        assert "<b>chore: bump</b>" in enabled_notify.texts[0]
        assert "2 files" in enabled_notify.texts[0]
