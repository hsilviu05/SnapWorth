"""`/revenue rcimport`: existing subscriptions replayed to RevenueCat.

Apple and RevenueCat are both stood in for; the index is the real one.
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops import notify  # noqa: E402
from ops import opsindex  # noqa: E402
from ops import opsrevenue  # noqa: E402
from ops import opsrevenuecat  # noqa: E402
from core.cache import InMemoryCache, ResilientCache  # noqa: E402
from billing.entitlements import Entitlement  # noqa: E402


@pytest_asyncio.fixture
async def configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv(opsrevenuecat.KEY_ENV, "appl_test")
    monkeypatch.setattr(opsrevenuecat, "PAUSE_SECONDS", 0)
    cache = ResilientCache(None, InMemoryCache())
    notify.configure(cache)
    yield cache
    await notify.aclose()


async def put_index(cache, rows: dict) -> None:
    await cache.set(opsindex.SUBS_INDEX_KEY, json.dumps(rows))


@pytest.fixture
def apple(monkeypatch):
    """Stands in for Apple: `subs` maps an otid to (environment, jws)."""
    from billing import appstorestatus

    state = {"subs": {}, "rate_limited": False}

    monkeypatch.setattr(appstorestatus, "credentials_from_env", lambda: object())

    async def lookup(otid):
        if state["rate_limited"]:
            raise appstorestatus.StatusRateLimited("429")
        if otid not in state["subs"]:
            raise appstorestatus.SubscriberNotFound("none")
        env, jws = state["subs"][otid]
        ent = Entitlement("pro", "com.snapworth.yearly", int(time.time()) + 86_400, otid, env)
        return [appstorestatus.SubscriptionStatus(
            entitlement=ent, state="active", auto_renew=True, signed_transaction=jws)]

    monkeypatch.setattr(appstorestatus, "lookup", lookup)
    return state


def revenuecat(status: int = 200):
    """A RevenueCat that records what it was sent."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "headers": dict(request.headers),
                     "body": json.loads(request.content)})
        if 200 <= status < 300:
            return httpx.Response(status, json={})
        return httpx.Response(status, json={"code": 7225, "message": "Invalid <JWS>"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


class TestImport:
    @pytest.mark.asyncio
    async def test_sends_each_production_subscription_once(self, configured, apple):
        await put_index(configured, {
            "1001": {"env": "Production", "first": 2},
            "1000": {"env": "Production", "first": 1},
            "2000": {"env": "Sandbox", "first": 0},
        })
        apple["subs"] = {"1000": ("Production", "jws-a"), "1001": ("Production", "jws-b"),
                         "2000": ("Sandbox", "jws-s")}
        client, seen = revenuecat()

        text = await opsrevenuecat.import_subscriptions(client=client)

        assert [s["body"]["fetch_token"] for s in seen] == ["jws-a", "jws-b"]
        assert seen[0]["url"] == opsrevenuecat.RECEIPTS_URL
        assert seen[0]["headers"]["authorization"] == "Bearer appl_test"
        assert seen[0]["headers"]["x-platform"] == "ios"
        assert seen[0]["body"]["product_id"] == "com.snapworth.yearly"
        assert "2 sent" in text

    @pytest.mark.asyncio
    async def test_app_user_id_is_anonymous_stable_and_not_the_transaction_id(
            self, configured, apple):
        await put_index(configured, {"1000": {"env": "Production"}})
        apple["subs"] = {"1000": ("Production", "jws-a")}
        first, seen_first = revenuecat()
        second, seen_second = revenuecat()

        await opsrevenuecat.import_subscriptions(client=first)
        await opsrevenuecat.import_subscriptions(client=second)

        user = seen_first[0]["body"]["app_user_id"]
        assert user.startswith("$RCAnonymousID:")
        assert "1000" not in user
        assert seen_second[0]["body"]["app_user_id"] == user

    @pytest.mark.asyncio
    async def test_limit_sends_only_the_oldest(self, configured, apple):
        await put_index(configured, {"b": {"env": "Production", "first": 5},
                                     "a": {"env": "Production", "first": 1}})
        apple["subs"] = {"a": ("Production", "jws-a"), "b": ("Production", "jws-b")}
        client, seen = revenuecat()

        await opsrevenuecat.import_subscriptions(limit=1, client=client)

        assert [s["body"]["fetch_token"] for s in seen] == ["jws-a"]

    @pytest.mark.asyncio
    async def test_unknown_and_refused_are_counted_not_raised(self, configured, apple):
        await put_index(configured, {"gone": {"env": "Production"},
                                     "1000": {"env": "Production"}})
        apple["subs"] = {"1000": ("Production", "jws-a")}
        client, _ = revenuecat(status=401)

        text = await opsrevenuecat.import_subscriptions(client=client)

        assert "0 sent" in text
        assert "1 unknown to Apple" in text
        assert "1 refused by RevenueCat" in text
        # RevenueCat's own reason, escaped for the bot's HTML.
        assert "RevenueCat said (1×): HTTP 401 — 7225: Invalid &lt;JWS&gt;" in text

    @pytest.mark.asyncio
    async def test_rate_limit_stops_the_run(self, configured, apple):
        await put_index(configured, {"1000": {"env": "Production"}})
        apple["rate_limited"] = True
        client, seen = revenuecat()

        text = await opsrevenuecat.import_subscriptions(client=client)

        assert seen == []
        assert "Stopped early" in text

    @pytest.mark.asyncio
    async def test_refuses_a_secret_key_and_defaults_to_the_apps(
            self, configured, apple, monkeypatch):
        await put_index(configured, {"1000": {"env": "Production"}})
        apple["subs"] = {"1000": ("Production", "jws-a")}
        client, seen = revenuecat()
        monkeypatch.setenv(opsrevenuecat.KEY_ENV, "sk_secret")
        assert "did not run" in await opsrevenuecat.import_subscriptions(client=client)
        assert seen == []
        monkeypatch.delenv(opsrevenuecat.KEY_ENV)
        await opsrevenuecat.import_subscriptions(client=client)
        assert seen[0]["headers"]["authorization"] == f"Bearer {opsrevenuecat.PUBLIC_KEY}"

    def test_default_key_is_the_apps(self):
        config = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "..", "ios", "SnapWorth", "Config.swift")
        if not os.path.exists(config):
            pytest.skip("iOS sources not checked out")
        with open(config, encoding="utf-8") as f:
            assert f'"{opsrevenuecat.PUBLIC_KEY}"' in f.read()


class TestCommand:
    @pytest.mark.asyncio
    async def test_first_tap_only_asks(self, configured, apple):
        await put_index(configured, {"1000": {"env": "Production"}})
        apple["subs"] = {"1000": ("Production", "jws-a")}

        text, buttons = await opsrevenue.revenue_command("rcimport", "rcimport")

        assert "Send existing subscriptions to RevenueCat?" in text
        callbacks = [cb for row in buttons for _, cb in row]
        assert "revenue rcimport test" in callbacks
        assert "revenue rcimport yes" in callbacks


def revenuecat_customers(status: int = 200):
    """A RevenueCat whose GET /v1/subscribers records each customer fetched."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"method": request.method, "url": str(request.url),
                     "headers": dict(request.headers)})
        if 200 <= status < 300:
            return httpx.Response(status, json={"subscriber": {}})
        return httpx.Response(status, json={"code": 7243, "message": "Forbidden"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


class TestUsersImport:
    @pytest.mark.asyncio
    async def test_creates_each_device_not_on_a_subscription(self, configured):
        await put_index(configured, {"1000": {"env": "Production",
                                              "devices": ["aaaa111122223333"]}})
        await configured.set(opsindex.USERS_INDEX_KEY, json.dumps({
            "aaaa111122223333": {"first": 1, "tier": "pro"},
            "cccc111122223333": {"first": 9, "tier": "free"},
            "bbbb111122223333": {"first": 2, "tier": "free"},
        }))
        client, seen = revenuecat_customers()

        text = await opsrevenuecat.import_users(client=client)

        expected = [opsrevenuecat.device_app_user_id("bbbb111122223333"),
                    opsrevenuecat.device_app_user_id("cccc111122223333")]
        assert [s["method"] for s in seen] == ["GET", "GET"]
        assert [s["url"] for s in seen] == [
            opsrevenuecat.SUBSCRIBERS_URL + "%24RCAnonymousID%3A" + e.split(":", 1)[1]
            for e in expected]
        assert seen[0]["headers"]["authorization"] == "Bearer appl_test"
        assert all("bbbb" not in s["url"] and "cccc" not in s["url"] for s in seen)
        assert "2 devices without a subscription" in text
        assert "2 sent" in text

    @pytest.mark.asyncio
    async def test_legacy_short_device_id_on_a_subscription_is_skipped(self, configured):
        await put_index(configured, {"1000": {"env": "Production", "who": "aaaa11"}})
        await configured.set(opsindex.USERS_INDEX_KEY,
                             json.dumps({"aaaa111122223333": {"first": 1}}))
        client, seen = revenuecat_customers()

        await opsrevenuecat.import_users(client=client)

        assert seen == []

    @pytest.mark.asyncio
    async def test_refusal_reason_and_limit(self, configured):
        await configured.set(opsindex.USERS_INDEX_KEY, json.dumps(
            {"a": {"first": 1}, "b": {"first": 2}}))
        client, seen = revenuecat_customers(status=403)

        text = await opsrevenuecat.import_users(limit=1, client=client)

        assert len(seen) == 1
        assert "1 refused by RevenueCat" in text
        assert "HTTP 403 — 7243: Forbidden" in text

    @pytest.mark.asyncio
    async def test_refuses_a_secret_key(self, configured, monkeypatch):
        monkeypatch.setenv(opsrevenuecat.KEY_ENV, "sk_secret")
        client, seen = revenuecat_customers()
        assert "did not run" in await opsrevenuecat.import_users(client=client)
        assert seen == []

    @pytest.mark.asyncio
    async def test_command_asks_before_sending(self, configured):
        text, buttons = await opsrevenue.revenue_command("rcimport", "rcimport users")

        assert "devices without a subscription" in text
        callbacks = [cb for row in buttons for _, cb in row]
        assert "revenue rcimport users test" in callbacks
        assert "revenue rcimport users yes" in callbacks
        assert all(len(cb.encode()) <= 64 for cb in callbacks)
