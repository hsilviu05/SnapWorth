"""Trial starts, trial conversions and direct purchases, counted apart (#218).

`new_subs` was one day counter for every first sighting of a subscription, on
a once-per-originalTransactionId guard shared by both halves — the device's
sync and Apple's notification. In the real order the device syncs the trial
first and spends that guard, so the "Trial converted" branch that called the
same function days later counted nothing. The number a trial experiment is
judged on was missing, and the row could not recover it: `acq` is rewritten on
every sync, so a converted trial read "paid", like a direct purchase.

The existing conversion test seeded the index directly, which is why nothing
noticed. These drive the real order through the public entry points.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify  # noqa: E402
import opsstats  # noqa: E402
from cache import InMemoryCache, ResilientCache  # noqa: E402
from entitlements import Entitlement  # noqa: E402

from tests.test_notify import FAKE_CHAT, FAKE_TOKEN, FakeNotification, Recorder  # noqa: E402

SUBJECT = "b" * 64
ANALYTICS_SWIFT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "ios", "SnapWorth", "Services", "Analytics.swift")


def trial(otid: str = "otid-trial") -> Entitlement:
    """A yearly bought just now with its three-day free trial."""
    now = int(time.time())
    return Entitlement("pro", "com.snapworth.yearly", now + 3 * 86_400, otid,
                       "Production", original_purchase_at=now - 60,
                       offer_type=1, offer_discount_type="FREE_TRIAL")


def first_paid_period(otid: str = "otid-trial") -> Entitlement:
    """The same subscription's first paid year: no offer, bought three days ago."""
    now = int(time.time())
    return Entitlement("pro", "com.snapworth.yearly", now + 365 * 86_400, otid,
                       "Production", original_purchase_at=now - 3 * 86_400,
                       price=39.99, currency="USD")


def monthly(otid: str = "otid-monthly") -> Entitlement:
    """A monthly bought just now, no offer: a direct purchase."""
    now = int(time.time())
    return Entitlement("pro", "com.snapworth.monthly", now + 30 * 86_400, otid,
                       "Production", original_purchase_at=now - 60,
                       price=4.99, currency="USD")


class Clock:
    """Stands in for `opsstats.day()` with no argument, so a test can move the
    counters from one day to the next; with an argument it is the real one."""

    def __init__(self, monkeypatch, day: str) -> None:
        self.day = day
        real = opsstats.day
        monkeypatch.setattr(opsstats, "day",
                            lambda at=None: self.day if at is None else real(at))


async def stat(day: str, name: str) -> int:
    return int(await notify._cache.get(opsstats.stat_key(day, name)) or 0)


async def counts(day: str) -> dict[str, int]:
    return {name: await stat(day, name)
            for name in ("trial_starts", "trial_conversions", "paid_direct",
                         "offer_starts", "new_subs")}


async def row(otid: str) -> dict:
    return (await notify._read_index(notify.SUBS_INDEX_KEY))[otid]


async def drain() -> None:
    pending = [t for t in notify._tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
def cache() -> ResilientCache:
    return ResilientCache(None, InMemoryCache())


@pytest_asyncio.fixture
async def with_bot(cache):
    recorder = Recorder()
    notifier = notify.TelegramNotifier(
        FAKE_TOKEN, FAKE_CHAT,
        client=httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler)))
    notify.configure(cache, notifier=notifier)
    yield recorder
    await notify.aclose()


@pytest_asyncio.fixture
async def without_bot(cache, monkeypatch):
    """The cache and nothing else: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID unset."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    notify.configure(cache)
    assert not notify.enabled()
    yield
    await notify.aclose()


ZERO = {"trial_starts": 0, "trial_conversions": 0, "paid_direct": 0,
        "offer_starts": 0, "new_subs": 0}


class TestTheRealOrder:
    """The device syncs the trial the moment it starts; Apple reports the
    first paid period three days later."""

    @pytest.mark.asyncio
    async def test_a_trial_that_converts_counts_one_start_then_one_conversion(
            self, with_bot, monkeypatch):
        clock = Clock(monkeypatch, "20261001")
        await notify.entitlement_recorded(SUBJECT, trial())
        assert await counts("20261001") == {**ZERO, "trial_starts": 1, "new_subs": 1}

        clock.day = "20261004"
        await notify.subscription_event(
            FakeNotification(first_paid_period(), paid_period=True))
        assert await counts("20261004") == {**ZERO, "trial_conversions": 1}
        assert await counts("20261001") == {**ZERO, "trial_starts": 1, "new_subs": 1}

        # Then the app opens and syncs the paid transaction: nothing more.
        await notify.entitlement_recorded(SUBJECT, first_paid_period())
        assert await counts("20261004") == {**ZERO, "trial_conversions": 1}
        await drain()
        assert any("Trial converted" in t for t in with_bot.texts), with_bot.texts

    @pytest.mark.asyncio
    async def test_the_device_can_see_the_first_paid_period_before_apple(
            self, with_bot, monkeypatch):
        """Then `acq` already reads "paid" when the notification arrives, so
        only `started_as` can say it was a trial."""
        clock = Clock(monkeypatch, "20261001")
        await notify.entitlement_recorded(SUBJECT, trial())
        clock.day = "20261004"
        await notify.entitlement_recorded(SUBJECT, first_paid_period())
        assert await counts("20261004") == {**ZERO, "trial_conversions": 1}
        await notify.subscription_event(
            FakeNotification(first_paid_period(), paid_period=True))
        assert await counts("20261004") == {**ZERO, "trial_conversions": 1}

    @pytest.mark.asyncio
    async def test_the_second_paid_year_is_a_renewal_not_a_conversion(
            self, with_bot, monkeypatch):
        clock = Clock(monkeypatch, "20261001")
        await notify.entitlement_recorded(SUBJECT, trial())
        clock.day = "20261004"
        await notify.subscription_event(
            FakeNotification(first_paid_period(), paid_period=True))
        clock.day = "20271004"
        await notify.subscription_event(
            FakeNotification(first_paid_period(), paid_period=True, uuid="uuid-2"))
        assert await counts("20271004") == ZERO

    @pytest.mark.asyncio
    async def test_a_converted_row_still_reads_started_as_trial(self, with_bot):
        await notify.entitlement_recorded(SUBJECT, trial())
        assert (await row("otid-trial"))["started_as"] == "trial"
        await notify.subscription_event(
            FakeNotification(first_paid_period(), paid_period=True))
        await notify.entitlement_recorded(SUBJECT, first_paid_period())
        converted = await row("otid-trial")
        assert converted["acq"] == "paid", "acq is still the current transaction's"
        assert converted["started_as"] == "trial"
        # And when the free period ran out, which only the trial's own
        # transaction carried.
        assert converted["trial_ends"] == trial().expires_at

    @pytest.mark.asyncio
    async def test_a_row_from_before_the_field_takes_its_last_acq(self, with_bot):
        """A trial row written by the previous build has no `started_as`. Its
        first paid period still has to count as a conversion."""
        await notify._cache.set(notify.SUBS_INDEX_KEY, json.dumps({"otid-trial": {
            "product": "com.snapworth.yearly", "env": "Production",
            "acq": "trial", "expires": int(time.time()) - 60, "seen": 1}}))
        await notify.subscription_event(
            FakeNotification(first_paid_period(), paid_period=True))
        assert (await counts(opsstats.day()))["trial_conversions"] == 1
        assert (await row("otid-trial"))["started_as"] == "trial"


class TestWhatCountsAsWhat:

    @pytest.mark.asyncio
    async def test_a_direct_monthly_purchase_is_only_paid_direct(self, with_bot):
        await notify.entitlement_recorded(SUBJECT, monthly())
        day = opsstats.day()
        assert await counts(day) == {**ZERO, "paid_direct": 1, "new_subs": 1}
        assert (await row("otid-monthly"))["started_as"] == "paid"
        assert "trial_ends" not in await row("otid-monthly")

    @pytest.mark.asyncio
    async def test_a_renewal_counts_nothing(self, with_bot, monkeypatch):
        clock = Clock(monkeypatch, "20261001")
        await notify.entitlement_recorded(SUBJECT, monthly())
        clock.day = "20261031"
        await notify.subscription_event(
            FakeNotification(monthly(), paid_period=True))
        assert await counts("20261031") == ZERO

    @pytest.mark.asyncio
    async def test_a_launch_resync_counts_nothing(self, with_bot, monkeypatch):
        clock = Clock(monkeypatch, "20261001")
        await notify.entitlement_recorded(SUBJECT, monthly())
        await notify.entitlement_recorded(SUBJECT, trial())
        before = await counts("20261001")
        for _ in range(3):
            await notify.entitlement_recorded(SUBJECT, monthly())
            await notify.entitlement_recorded(SUBJECT, trial())
        assert await counts("20261001") == before
        clock.day = "20261002"
        await notify.entitlement_recorded(SUBJECT, monthly())
        assert await counts("20261002") == ZERO

    @pytest.mark.asyncio
    async def test_a_bounded_sandbox_entitlement_counts_nothing(self, with_bot):
        now = int(time.time())
        tester = Entitlement("pro", "com.snapworth.yearly", now + 3 * 86_400,
                             "otid-sandbox", "Sandbox", original_purchase_at=now,
                             offer_type=1, offer_discount_type="FREE_TRIAL")
        await notify.entitlement_recorded(SUBJECT, tester, paywall_trigger="scan_limit")
        assert await counts(opsstats.day()) == ZERO
        assert await stat(opsstats.day(), "trial_starts:scan_limit") == 0
        assert await notify._read_index(notify.SUBS_INDEX_KEY) == {}

    @pytest.mark.asyncio
    async def test_an_offer_code_is_neither_a_trial_nor_a_purchase(self, with_bot):
        now = int(time.time())
        code = Entitlement("pro", "com.snapworth.monthly", now + 7 * 86_400,
                           "otid-code", "Production", original_purchase_at=now,
                           offer_type=3, offer_discount_type="FREE_TRIAL")
        await notify.entitlement_recorded(SUBJECT, code, paywall_trigger="settings")
        assert await counts(opsstats.day()) == {**ZERO, "offer_starts": 1, "new_subs": 1}
        assert await stat(opsstats.day(), "trial_starts:settings") == 0

    @pytest.mark.asyncio
    async def test_a_payer_apple_reported_first_is_paid_direct(self, with_bot):
        await notify.subscription_event(
            FakeNotification(monthly(), paid_period=True))
        assert await counts(opsstats.day()) == {**ZERO, "paid_direct": 1, "new_subs": 1}
        await notify.entitlement_recorded(SUBJECT, monthly())
        assert await counts(opsstats.day()) == {**ZERO, "paid_direct": 1, "new_subs": 1}


class TestWithoutTelegram:
    """#190 fixed this class of bug for `/trends`: the counters returned
    early whenever `_notifier` was None, so switching the bot off would have
    stopped the count the trial experiment is read on, silently."""

    @pytest.mark.asyncio
    async def test_the_real_order_counts_with_the_bot_unconfigured(
            self, without_bot, monkeypatch):
        clock = Clock(monkeypatch, "20261001")
        await notify.entitlement_recorded(SUBJECT, trial(), paywall_trigger="scan_limit")
        await notify.entitlement_recorded(SUBJECT, monthly())
        assert await counts("20261001") == {
            **ZERO, "trial_starts": 1, "paid_direct": 1, "new_subs": 2}
        assert await stat("20261001", "trial_starts:scan_limit") == 1

        clock.day = "20261004"
        await notify.subscription_event(
            FakeNotification(first_paid_period(), paid_period=True))
        assert await counts("20261004") == {**ZERO, "trial_conversions": 1}
        assert (await row("otid-trial"))["started_as"] == "trial"

    @pytest.mark.asyncio
    async def test_nothing_is_sent_and_no_alert_guard_is_spent(self, without_bot):
        """The alert's guard is left for the bot, so turning it on later does
        not find every subscription already announced."""
        await notify.entitlement_recorded(SUBJECT, trial())
        assert notify._tasks == set(), "no sync note, no message"
        assert await notify._cache.get("opsseen:sub:otid-trial") is None


class TestPaywallTrigger:
    """`trial_starts:<trigger>` and `paid_direct:<trigger>`, once per
    originalTransactionId, on a guard of their own."""

    @pytest.mark.asyncio
    async def test_counted_once_however_many_syncs_follow(self, with_bot):
        for _ in range(5):
            await notify.entitlement_recorded(SUBJECT, trial(), paywall_trigger="scan_limit")
        assert await stat(opsstats.day(), "trial_starts:scan_limit") == 1

    @pytest.mark.asyncio
    async def test_a_sync_without_it_first_does_not_lose_it(self, with_bot):
        """Dismissing the purchase sheet re-activates the app, and that
        foreground sync carries no trigger. It can reach the server before
        the sync after the purchase, which does. On the first-sighting guard
        the trigger would always be lost."""
        day = opsstats.day()
        await notify.entitlement_recorded(SUBJECT, trial())
        assert await stat(day, "trial_starts:scan_limit") == 0
        await notify.entitlement_recorded(SUBJECT, trial(), paywall_trigger="scan_limit")
        assert await stat(day, "trial_starts:scan_limit") == 1
        await notify.entitlement_recorded(SUBJECT, trial(), paywall_trigger="scan_limit")
        assert await stat(day, "trial_starts:scan_limit") == 1
        # Nor does a later sync naming another paywall move it.
        await notify.entitlement_recorded(SUBJECT, trial(), paywall_trigger="onboarding")
        assert await stat(day, "trial_starts:onboarding") == 0
        assert await counts(day) == {**ZERO, "trial_starts": 1, "new_subs": 1}

    @pytest.mark.asyncio
    async def test_apple_reporting_a_purchase_first_does_not_lose_it(self, with_bot):
        await notify.subscription_event(FakeNotification(monthly(), paid_period=True))
        await notify.entitlement_recorded(SUBJECT, monthly(), paywall_trigger="haul")
        assert await stat(opsstats.day(), "paid_direct:haul") == 1
        assert await stat(opsstats.day(), "trial_starts:haul") == 0

    @pytest.mark.asyncio
    async def test_an_unknown_or_missing_trigger_counts_nothing_and_spends_nothing(
            self, with_bot):
        await notify.entitlement_recorded(SUBJECT, trial(), paywall_trigger="thrift_flip")
        await notify.entitlement_recorded(SUBJECT, trial(), paywall_trigger=None)
        assert await notify._cache.get("opsseen:subtrigger:otid-trial") is None
        await notify.entitlement_recorded(SUBJECT, trial(), paywall_trigger="trends")
        assert await stat(opsstats.day(), "trial_starts:trends") == 1

    @pytest.mark.asyncio
    async def test_an_old_subscription_restored_from_a_paywall_is_not_counted(
            self, with_bot):
        """Only a new subscription is counted, as the totals are, so the
        per-trigger rows can never add up to more than they do."""
        now = int(time.time())
        old = Entitlement("pro", "com.snapworth.monthly", now + 5 * 86_400,
                          "otid-old", "Production",
                          original_purchase_at=now - 40 * 86_400)
        await notify.entitlement_recorded(SUBJECT, old, paywall_trigger="settings")
        assert await stat(opsstats.day(), "paid_direct:settings") == 0

    @pytest.mark.asyncio
    async def test_the_trigger_is_never_written_to_the_row(self, with_bot):
        """Per-trigger counts are aggregate Product Interaction. On the row the
        trigger would be linked to Purchase History (#218, Notes)."""
        await notify.entitlement_recorded(SUBJECT, trial(), paywall_trigger="valuation_detail")
        raw = await notify._cache.get(notify.SUBS_INDEX_KEY)
        assert "valuation_detail" not in raw
        assert await stat(opsstats.day(), "trial_starts:valuation_detail") == 1

    def test_the_set_is_exactly_the_apps_paywall_trigger(self):
        """Copied from `PaywallTrigger`'s raw values. A trigger the app adds
        and this does not is dropped without a word, so hold the two equal."""
        with open(ANALYTICS_SWIFT, encoding="utf-8") as f:
            source = f.read()
        body = re.search(r"enum PaywallTrigger: String, CaseIterable \{(.*?)\n\}",
                         source, re.S)
        assert body, "PaywallTrigger not found in Analytics.swift"
        raw = []
        for line in body.group(1).splitlines():
            m = re.match(r"\s*case (\w+)(?:\s*=\s*\"([^\"]+)\")?\s*$", line)
            if m:
                raw.append(m.group(2) or m.group(1))
        assert sorted(raw) == sorted(notify.PAYWALL_TRIGGERS)


class TestEntitlementRoute:
    """`/auth/entitlement` with a missing or unknown `paywall_trigger` must
    behave exactly as it did before the field existed: no 422, ignored."""

    @pytest.fixture
    def seen(self, monkeypatch):
        import auth
        import referral

        ent = Entitlement("pro", "com.snapworth.yearly", None, "otid-1", "Production")
        calls: list[dict] = []

        async def record(subject, jws, device_id=None, authenticated=False):
            return ent

        async def recorded(subject, e, **kwargs):
            calls.append(kwargs)

        async def quiet(*_args, **_kwargs):
            return None

        monkeypatch.setattr(auth.deps.entitlements, "record", record)
        monkeypatch.setattr(notify, "entitlement_recorded", recorded)
        monkeypatch.setattr(referral, "on_entitlement", quiet)
        return calls

    def _post(self, body: dict):
        from fastapi.testclient import TestClient
        from main import app
        return TestClient(app).post("/auth/entitlement", json=body,
                                    headers={"x-device-id": "trigger-route"})

    @pytest.mark.parametrize("extra", [
        {},
        {"paywall_trigger": None},
        {"paywall_trigger": "thrift_flip"},
        {"paywall_trigger": "SCAN_LIMIT"},
        {"paywall_trigger": ""},
        {"paywall_trigger": "x" * 10_000},
        {"paywall_trigger": 7},
        {"paywall_trigger": ["scan_limit"]},
        {"paywall_trigger": {"name": "scan_limit"}},
    ])
    def test_missing_or_unknown_is_ignored_not_refused(self, seen, extra):
        response = self._post({"signed_transaction": "ok", **extra})
        assert response.status_code == 200, response.text
        assert response.json()["tier"] == "pro"
        assert seen == [{"paywall_trigger": None}]

    @pytest.mark.parametrize("trigger", notify.PAYWALL_TRIGGERS)
    def test_every_known_trigger_is_passed_on(self, seen, trigger):
        response = self._post({"signed_transaction": "ok", "paywall_trigger": trigger})
        assert response.status_code == 200, response.text
        assert seen == [{"paywall_trigger": trigger}]


class TestPaywallReadout:

    NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

    async def _seed(self, days_ago: int, name: str, value: int) -> None:
        day = opsstats.day(self.NOW - timedelta(days=days_ago))
        await notify._cache.set(opsstats.stat_key(day, name), str(value))

    async def _rows(self, rows: dict) -> None:
        await notify._cache.set(notify.SUBS_INDEX_KEY, json.dumps(rows))

    @pytest.mark.asyncio
    async def test_starts_and_purchases_per_trigger_over_28_days(self, with_bot):
        await self._seed(0, "trial_starts", 3)
        await self._seed(0, "trial_starts:scan_limit", 2)
        await self._seed(27, "paid_direct", 1)
        await self._seed(27, "paid_direct:onboarding", 1)
        await self._seed(10, "trial_conversions", 1)
        # Day 28 is outside the window.
        await self._seed(28, "trial_starts", 9)
        await self._seed(28, "trial_starts:haul", 9)
        text = await notify._paywall_text(self.NOW)
        assert text.startswith("💳 <b>Paywall — last 28 days</b>")
        assert "Trial starts: 3 · direct purchases: 1 · converted trials: 1" in text
        assert "<code>scan_limit            2      0</code>" in text
        assert "<code>onboarding            0      1</code>" in text
        assert "<code>no trigger            1      0</code>" in text
        assert "haul" not in text

    @pytest.mark.asyncio
    async def test_trial_to_paid_is_over_trials_that_ended_with_its_n(self, with_bot):
        now = self.NOW.timestamp()
        await self._rows({
            # Ended inside the window: two paid, one did not.
            "a": {"started_as": "trial", "acq": "paid", "trial_ends": now - 5 * 86_400},
            "b": {"started_as": "trial", "acq": "paid", "trial_ends": now - 20 * 86_400},
            "c": {"started_as": "trial", "acq": "trial", "trial_ends": now - 2 * 86_400},
            # Ended before the window, still in its trial, or never a trial.
            "d": {"started_as": "trial", "acq": "paid", "trial_ends": now - 40 * 86_400},
            "e": {"started_as": "trial", "acq": "trial", "trial_ends": now + 86_400},
            "f": {"started_as": "paid", "acq": "paid"},
            # A row converted before `trial_ends` existed cannot be placed.
            "g": {"started_as": "trial", "acq": "paid", "trial_ends": None},
        })
        text = await notify._paywall_text(self.NOW)
        assert "Trial → paid: 2 of 3 trials that ended (67%, n=3)" in text

    @pytest.mark.asyncio
    async def test_an_empty_window_says_so(self, with_bot):
        text = await notify._paywall_text(self.NOW)
        assert "No trial starts or direct purchases in the window." in text
        assert "Trial → paid: no trial ended in the window (n=0)" in text

    @pytest.mark.asyncio
    async def test_it_is_a_command(self, with_bot):
        assert "paywall" in dict(notify.COMMANDS)
        text = await notify.handle_command("/paywall")
        assert text is not None and "Paywall — last 28 days" in text
