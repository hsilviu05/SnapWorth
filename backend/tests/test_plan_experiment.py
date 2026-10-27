"""The paywall default-plan A/B test (`/lever plan split`).

Each enrolled device keeps one arm, only builds that read the field are
enrolled, and trials, purchases and conversions are credited to the arm the
buyer was in — including a conversion Apple reports with no device.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops import levers  # noqa: E402
from ops import notify  # noqa: E402
from ops import opsstats  # noqa: E402
from ops import planexperiment  # noqa: E402
from core.cache import InMemoryCache, ResilientCache  # noqa: E402
from billing.entitlements import Entitlement  # noqa: E402

from tests.test_notify import FakeNotification  # noqa: E402

BUILD = planexperiment.MIN_BUILD


def subject_in(arm: str, experiment: str, start: int = 0) -> str:
    """A subject the hash puts in `arm`."""
    for i in range(start, start + 1000):
        subject = f"{i:064x}"
        if planexperiment.arm_for(experiment, subject) == arm:
            return subject
    raise AssertionError("no subject found")


def trial(otid: str) -> Entitlement:
    now = int(time.time())
    return Entitlement("pro", "com.snapworth.yearly", now + 3 * 86_400, otid,
                       "Production", original_purchase_at=now - 60,
                       offer_type=1, offer_discount_type="FREE_TRIAL")


def first_paid_year(otid: str) -> Entitlement:
    now = int(time.time())
    return Entitlement("pro", "com.snapworth.yearly", now + 365 * 86_400, otid,
                       "Production", original_purchase_at=now - 3 * 86_400,
                       price=39.99, currency="USD")


def monthly(otid: str) -> Entitlement:
    now = int(time.time())
    return Entitlement("pro", "com.snapworth.monthly", now + 30 * 86_400, otid,
                       "Production", original_purchase_at=now - 60,
                       price=4.99, currency="USD")


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


async def start_split() -> str:
    await levers.lever_command("plan", "plan split yes")
    doc = await levers.read()
    experiment = planexperiment.active_experiment(doc)
    assert experiment == opsstats.day()
    return experiment


async def count(experiment: str, arm: str, name: str) -> int:
    return int(await notify._cache.get(
        planexperiment.counter_key(experiment, arm, name)) or 0)


class TestArms:
    def test_an_arm_is_stable_and_the_split_is_even(self):
        arms = [planexperiment.arm_for("20261027", f"{i:064x}") for i in range(2000)]
        assert arms == [planexperiment.arm_for("20261027", f"{i:064x}") for i in range(2000)]
        assert 900 < arms.count("monthly") < 1100

    def test_a_new_experiment_reshuffles(self):
        subjects = [f"{i:064x}" for i in range(200)]
        a = [planexperiment.arm_for("20261027", s) for s in subjects]
        b = [planexperiment.arm_for("20261110", s) for s in subjects]
        assert a != b


class TestAssignment:
    @pytest.mark.asyncio
    async def test_without_a_split_the_lever_value_is_sent(self, configured):
        assert await levers.paywall_default_plan("s" * 64, BUILD) is None
        await levers.lever_command("plan", "plan monthly yes")
        assert await levers.paywall_default_plan("s" * 64, BUILD) == "monthly"

    @pytest.mark.asyncio
    async def test_a_device_gets_its_arm_and_is_counted_once(self, configured):
        experiment = await start_split()
        subject = subject_in("monthly", experiment)
        for _ in range(3):
            assert await levers.paywall_default_plan(subject, BUILD) == "monthly"
        assert await count(experiment, "monthly", "devices") == 1
        assert await count(experiment, "yearly", "devices") == 0

    @pytest.mark.asyncio
    async def test_old_or_unknown_builds_are_not_enrolled(self, configured):
        experiment = await start_split()
        subject = subject_in("monthly", experiment)
        assert await levers.paywall_default_plan(subject, BUILD - 1) is None
        assert await levers.paywall_default_plan(subject, None) is None
        assert await count(experiment, "monthly", "devices") == 0

    @pytest.mark.asyncio
    async def test_ending_the_split_sends_the_new_value_and_keeps_the_readout(self, configured):
        experiment = await start_split()
        await levers.lever_command("plan", "plan yearly yes")
        doc = await levers.read()
        assert planexperiment.active_experiment(doc) is None
        assert doc["plan_split_id"] == experiment
        assert await levers.paywall_default_plan("s" * 64, BUILD) == "yearly"
        text = await planexperiment.readout_text(doc)
        assert text is not None and "ended" in text

    @pytest.mark.asyncio
    async def test_split_asks_first(self, configured):
        text, buttons = await levers.lever_command("plan", "plan split")
        assert "A/B" in text
        assert planexperiment.active_experiment(await levers.read()) is None
        assert ("✅ Yes, change it", "lever plan split yes") in buttons[0]

    @pytest.mark.asyncio
    async def test_an_unreadable_cache_still_answers_an_arm(self, configured, monkeypatch):
        experiment = await start_split()
        subject = subject_in("yearly", experiment)

        async def broken(*a, **k):
            raise RuntimeError("redis down")
        monkeypatch.setattr(notify._cache, "add", broken)
        assert await levers.paywall_default_plan(subject, BUILD) == "yearly"


class TestAttribution:
    @pytest.mark.asyncio
    async def test_a_trial_and_its_conversion_go_to_the_trial_arm(self, configured):
        experiment = await start_split()
        subject = subject_in("monthly", experiment)
        await levers.paywall_default_plan(subject, BUILD)

        await notify.entitlement_recorded(subject, trial("otid-1"))
        await notify.entitlement_recorded(subject, trial("otid-1"))
        assert await count(experiment, "monthly", "trial_starts") == 1
        assert await count(experiment, "monthly", "bought:yearly") == 1

        # Apple reports the first paid year, with no device attached.
        await notify.subscription_event(FakeNotification(
            first_paid_year("otid-1"), paid_period=True))
        await notify.subscription_event(FakeNotification(
            first_paid_year("otid-1"), uuid="uuid-2", paid_period=True))
        assert await count(experiment, "monthly", "conversions") == 1
        assert await count(experiment, "monthly", "value:USD") == 3999

    @pytest.mark.asyncio
    async def test_a_direct_monthly_is_valued_over_a_year(self, configured):
        experiment = await start_split()
        subject = subject_in("yearly", experiment)
        await levers.paywall_default_plan(subject, BUILD)
        await notify.entitlement_recorded(subject, monthly("otid-2"))
        assert await count(experiment, "yearly", "paid_direct") == 1
        assert await count(experiment, "yearly", "bought:monthly") == 1
        assert await count(experiment, "yearly", "value:USD") == 499 * 12

    @pytest.mark.asyncio
    async def test_an_unenrolled_buyer_counts_nowhere(self, configured):
        experiment = await start_split()
        subject = subject_in("yearly", experiment)
        await notify.entitlement_recorded(subject, monthly("otid-3"))
        for arm in planexperiment.ARMS:
            assert await count(experiment, arm, "paid_direct") == 0

    @pytest.mark.asyncio
    async def test_paywall_shows_both_arms(self, configured):
        experiment = await start_split()
        for arm, start in (("yearly", 0), ("monthly", 0)):
            await levers.paywall_default_plan(subject_in(arm, experiment, start), BUILD)
        reply = await notify.handle_command("/paywall")
        assert reply is not None
        assert "Default-plan test" in reply and "running" in reply
        assert "<b>yearly</b> preselected: 1 devices" in reply
        assert "<b>monthly</b> preselected: 1 devices" in reply
        assert "Sample size: fixed once" in reply and "No verdict" in reply
        assert "p =" not in reply


async def seed(experiment: str, arm: str, devices: int, starts: int,
               value_cents: int = 0) -> None:
    await notify._cache.set(planexperiment.counter_key(experiment, arm, "devices"),
                            str(devices))
    await notify._cache.set(planexperiment.counter_key(experiment, arm, "trial_starts"),
                            str(starts))
    if value_cents:
        await notify._cache.set(planexperiment.counter_key(experiment, arm, "value:USD"),
                                str(value_cents))
        await notify._cache.set(f"planab:{experiment}:currencies", '["USD"]')


class TestFixedHorizon:
    """No verdict until a sample fixed in advance is reached (#396)."""

    def test_the_size_is_the_two_proportion_formula(self):
        # 10% vs 13%, two-sided 5%, 80% power: the textbook ~1,770 per arm.
        assert planexperiment.planned_devices_per_arm(0.10) == 1772
        assert planexperiment.planned_devices_per_arm(0.0) is None
        assert planexperiment.planned_devices_per_arm(1.0) is None

    @pytest.mark.asyncio
    async def test_a_significant_p_before_the_sample_is_not_a_verdict(self, configured):
        experiment = await start_split()
        # p < 0.001 on these counts, but far short of the sample they size.
        await seed(experiment, "yearly", 200, 5)
        await seed(experiment, "monthly", 200, 30)
        text = await planexperiment.readout_text(await levers.read())
        assert text is not None
        planned = planexperiment.planned_devices_per_arm(35 / 400)
        assert f"Sample: 200 of {planned} devices per arm" in text
        assert "No verdict before then" in text
        assert "p =" not in text and "real difference" not in text

    @pytest.mark.asyncio
    async def test_the_size_is_fixed_once_and_does_not_move_with_the_data(self, configured):
        experiment = await start_split()
        await seed(experiment, "yearly", 200, 10)
        await seed(experiment, "monthly", 200, 10)
        await planexperiment.readout_text(await levers.read())
        first = await notify._cache.get(f"planab:{experiment}:planned")
        await seed(experiment, "yearly", 400, 120)
        await seed(experiment, "monthly", 400, 120)
        await planexperiment.readout_text(await levers.read())
        assert await notify._cache.get(f"planab:{experiment}:planned") == first

    @pytest.mark.asyncio
    async def test_at_the_sample_it_calls_the_test_and_the_value_leader(self, configured):
        experiment = await start_split()
        await notify._cache.set(f"planab:{experiment}:planned", "300")
        # Monthly starts more, yearly earns more per device.
        await seed(experiment, "yearly", 300, 30, value_cents=30 * 3999)
        await seed(experiment, "monthly", 300, 60, value_cents=60 * 499 * 3)
        text = await planexperiment.readout_text(await levers.read())
        assert text is not None
        assert "Sample reached (300 per arm). Start rate: p = 0.00, a real difference." in text
        assert "<b>yearly</b> leads, ≈ $4.00 vs $2.99" in text

    @pytest.mark.asyncio
    async def test_a_split_ended_short_of_its_sample_reads_inconclusive(self, configured):
        experiment = await start_split()
        await notify._cache.set(f"planab:{experiment}:planned", "1000")
        await seed(experiment, "yearly", 100, 5)
        await seed(experiment, "monthly", 100, 30)
        await levers.lever_command("plan", "plan yearly yes")
        text = await planexperiment.readout_text(await levers.read())
        assert text is not None and "inconclusive" in text


class TestOverlapWarning:
    @pytest.mark.asyncio
    async def test_an_armed_free_scan_lever_is_named_before_a_split(
            self, configured, monkeypatch):
        doc = await levers.read()
        doc["free_scans_first_day"] = 3
        await notify._cache.set(levers.LEVERS_KEY, __import__("json").dumps(doc))
        text, _ = await levers.lever_command("plan", "plan split")
        assert "Also running: the free-scan lever is armed (3 first-day scans)" in text

    @pytest.mark.asyncio
    async def test_referrals_switched_on_are_named(self, configured, monkeypatch):
        from billing import referral

        async def on():
            return True
        monkeypatch.setattr(referral, "switched_on", on)
        text, _ = await levers.lever_command("plan", "plan split")
        assert "referrals are switched on" in text

    @pytest.mark.asyncio
    async def test_nothing_else_running_says_nothing(self, configured, monkeypatch):
        from billing import referral

        async def off():
            return False
        monkeypatch.setattr(referral, "switched_on", off)
        text, _ = await levers.lever_command("plan", "plan split")
        assert "Also running" not in text


class TestSignificance:
    def test_small_arms_are_not_called(self):
        assert planexperiment.two_proportion_p(1, 10, 5, 10) is None

    def test_equal_rates_are_not_significant(self):
        p = planexperiment.two_proportion_p(10, 100, 10, 100)
        assert p is not None and p > 0.99

    def test_a_large_gap_is(self):
        p = planexperiment.two_proportion_p(5, 200, 30, 200)
        assert p is not None and p < 0.001


class TestTokenRoute:
    def test_the_mint_passes_the_subject_and_build(self, monkeypatch):
        from security import auth
        from tests.test_auth import build_deps

        seen = {}

        async def plan(subject=None, build=None):
            seen.update(subject=subject, build=build)
            return "monthly"
        monkeypatch.setattr(levers, "paywall_default_plan", plan)
        build_deps(enforce=True)
        try:
            response = asyncio.run(auth._issue_token("subj", None, 25))
        finally:
            build_deps()
        assert seen == {"subject": "subj", "build": 25}
        assert response.paywall_default_plan == "monthly"
