"""Referrals (#97): pools, claims, rewards, and the routes.

`auth.deps` is rebuilt per test on an in-memory cache (conftest), so every
test starts with empty pools and no records.
"""

import asyncio
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
import referral  # noqa: E402
from entitlements import Entitlement, entitlement_from_payload  # noqa: E402
from main import app  # noqa: E402
from referral import POOL_FRIEND, POOL_REWARD, ReferralConfig, ReferralError  # noqa: E402

FRIEND_OFFER = "referral-friend-7d"
client = TestClient(app)


@pytest.fixture(autouse=True)
def active(monkeypatch):
    monkeypatch.setattr(referral, "config", ReferralConfig(enabled=True, friend_offer=FRIEND_OFFER))


def run(coro):
    return asyncio.run(coro)


async def load(pool: str, codes: list[str]) -> None:
    """What tools/load_referral_codes.py writes, through the app's cache."""
    cache = auth.deps.cache
    size = int(await cache.get(referral.pool_size_key(pool)) or 0)
    for code in codes:
        size += 1
        await cache.set(referral.pool_item_key(pool, size), code)
    await cache.set(referral.pool_size_key(pool), str(size))


def redeemed(offer_identifier=FRIEND_OFFER, offer_type=3) -> Entitlement:
    return Entitlement("pro", "com.snapworth.yearly", None, "otid-1", "Production",
                       offer_type=offer_type, offer_identifier=offer_identifier)


class TestPool:
    def test_hands_out_each_code_once_then_runs_dry(self):
        async def go():
            await load(POOL_FRIEND, ["AAA111", "BBB222"])
            return [await referral.take_code(POOL_FRIEND) for _ in range(3)]
        assert run(go()) == ["AAA111", "BBB222", None]

    def test_concurrent_takes_never_share_a_code(self):
        async def go():
            await load(POOL_FRIEND, [f"CODE{i:02d}" for i in range(10)])
            return await asyncio.gather(*(referral.take_code(POOL_FRIEND) for _ in range(25)))
        got = run(go())
        issued = [c for c in got if c]
        assert len(issued) == 10 and len(set(issued)) == 10
        assert got.count(None) == 15

    def test_an_appended_batch_is_handed_out_after_the_first(self):
        async def go():
            await load(POOL_REWARD, ["R1AAAA"])
            first = await referral.take_code(POOL_REWARD)
            await load(POOL_REWARD, ["R2BBBB"])
            return first, await referral.take_code(POOL_REWARD), await referral.pool_remaining(POOL_REWARD)
        assert run(go()) == ("R1AAAA", "R2BBBB", 0)


class TestCode:
    def test_stable_per_device_and_distinct_between_devices(self):
        async def go():
            return (await referral.code_for("dev-a"), await referral.code_for("dev-a"),
                    await referral.code_for("dev-b"))
        a1, a2, b = run(go())
        assert a1 == a2 and a1 != b
        assert len(a1) == referral.CODE_LENGTH
        assert set(a1) <= set(referral.CODE_ALPHABET)

    def test_normalises_what_people_type(self):
        assert referral.normalise_code(" ab-c2 3d ") == "ABC23D"


class TestClaim:
    def setup_code(self, device="referrer"):
        return run(referral.code_for(device))

    def test_happy_path_returns_a_friend_code_and_records_the_referrer(self):
        code = self.setup_code()
        run(load(POOL_FRIEND, ["FRIEND1"]))
        assert run(referral.claim("subj-f", "friend", code.lower())) == "FRIEND1"
        record = json.loads(run(auth.deps.cache.get("ref:claim:friend")))
        assert record["referrer"] == "referrer" and record["friend_code"] == "FRIEND1"

    def test_unknown_code_is_404_and_counts_as_a_failure(self):
        with pytest.raises(ReferralError) as exc:
            run(referral.claim("subj-f", "friend", "ZZZZZZ"))
        assert exc.value.status == 404

    def test_too_many_failures_are_refused_for_the_day(self):
        for _ in range(referral.MAX_FAILED_CLAIMS_PER_DAY):
            with pytest.raises(ReferralError):
                run(referral.claim("subj-f", "friend", "ZZZZZZ"))
        code = self.setup_code()
        run(load(POOL_FRIEND, ["FRIEND1"]))
        with pytest.raises(ReferralError) as exc:
            run(referral.claim("subj-f", "friend", code))
        assert exc.value.status == 429

    def test_own_code_is_refused(self):
        code = self.setup_code("same-device")
        with pytest.raises(ReferralError) as exc:
            run(referral.claim("subj", "same-device", code))
        assert exc.value.status == 400

    def test_once_per_device_and_once_per_subject(self):
        code = self.setup_code()
        run(load(POOL_FRIEND, ["F1AAAA", "F2BBBB", "F3CCCC"]))
        run(referral.claim("subj-1", "dev-1", code))
        for subject, device in (("subj-2", "dev-1"),     # same device, reinstalled
                                ("subj-1", "dev-9")):    # same install, spoofed device
            with pytest.raises(ReferralError) as exc:
                run(referral.claim(subject, device, code))
            assert exc.value.status == 409

    def test_empty_pool_is_503_and_undone_so_a_retry_can_succeed(self):
        code = self.setup_code()
        with pytest.raises(ReferralError) as exc:
            run(referral.claim("subj-f", "friend", code))
        assert exc.value.status == 503
        run(load(POOL_FRIEND, ["LATE01"]))
        assert run(referral.claim("subj-f", "friend", code)) == "LATE01"


class TestReward:
    def claimed(self, friend="friend", subject="subj-f", referrer="referrer"):
        code = run(referral.code_for(referrer))
        run(load(POOL_FRIEND, [f"F{friend.upper()[:5]:0<5}"]))
        run(referral.claim(subject, friend, code))

    def test_redeeming_the_friend_offer_rewards_the_referrer(self):
        self.claimed()
        run(load(POOL_REWARD, ["REWARD1"]))
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is True
        assert run(referral.rewards_for("referrer"))[0]["code"] == "REWARD1"

    @pytest.mark.parametrize("ent", [
        redeemed(offer_identifier="some-other-campaign"),
        redeemed(offer_type=1),
        redeemed(offer_type=None, offer_identifier=None),
    ])
    def test_other_offers_and_plain_purchases_reward_nothing(self, ent):
        self.claimed()
        run(load(POOL_REWARD, ["REWARD1"]))
        assert run(referral.on_entitlement("subj-f", "friend", ent)) is False

    def test_no_claim_no_reward(self):
        run(load(POOL_REWARD, ["REWARD1"]))
        assert run(referral.on_entitlement("subj-x", "stranger", redeemed())) is False

    def test_each_friend_pays_out_once(self):
        self.claimed()
        run(load(POOL_REWARD, ["REWARD1", "REWARD2"]))
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is True
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is False
        assert len(run(referral.rewards_for("referrer"))) == 1

    def test_capped_per_referrer_per_year(self, monkeypatch):
        monkeypatch.setattr(referral, "config", ReferralConfig(
            enabled=True, friend_offer=FRIEND_OFFER, rewards_per_year=1))
        self.claimed("friend1", "subj-1")
        self.claimed("friend2", "subj-2")
        run(load(POOL_REWARD, ["REWARD1", "REWARD2"]))
        assert run(referral.on_entitlement("subj-1", "friend1", redeemed())) is True
        assert run(referral.on_entitlement("subj-2", "friend2", redeemed())) is False

    def test_empty_reward_pool_is_retried_after_a_refill(self):
        self.claimed()
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is False
        run(load(POOL_REWARD, ["REWARD1"]))
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is True

    def test_off_means_nothing_happens(self, monkeypatch):
        self.claimed()
        run(load(POOL_REWARD, ["REWARD1"]))
        monkeypatch.setattr(referral, "config", ReferralConfig(enabled=False, friend_offer=FRIEND_OFFER))
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is False

    def test_never_raises(self, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("redis gone")
        monkeypatch.setattr(auth.deps.cache, "get", boom)
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is False


class TestEntitlementOfferIdentifier:
    def test_parsed_and_round_tripped(self):
        ent = entitlement_from_payload(
            {"offerType": 3, "offerIdentifier": FRIEND_OFFER, "productId": "com.snapworth.yearly"},
            environment="Production")
        assert ent.offer_identifier == FRIEND_OFFER
        assert Entitlement.from_json(ent.to_json()).offer_identifier == FRIEND_OFFER

    def test_older_cached_json_without_it_still_loads(self):
        legacy = json.dumps({"tier": "pro", "product_id": "p", "expires_at": None,
                             "original_transaction_id": "o", "environment": "Production"})
        assert Entitlement.from_json(legacy).offer_identifier is None


class TestRoutes:
    H = {"x-device-id": "route-test"}

    def test_status_when_off_says_so_and_nothing_else(self, monkeypatch):
        monkeypatch.setattr(referral, "config", ReferralConfig(enabled=False))
        r = client.post("/referral/status", json={"device_id": "dev-a"}, headers=self.H)
        assert r.status_code == 200 and r.json() == {
            "enabled": False, "code": None, "share_url": None, "rewards": [],
            "rewards_left_this_year": None}

    def test_enabled_without_the_offer_name_is_still_off(self, monkeypatch):
        monkeypatch.setattr(referral, "config", ReferralConfig(enabled=True, friend_offer=""))
        assert client.post("/referral/status", json={"device_id": "d"}, headers=self.H).json()["enabled"] is False

    def test_status_returns_code_link_and_rewards(self):
        r = client.post("/referral/status", json={"device_id": "dev-a"}, headers=self.H).json()
        assert r["enabled"] is True
        assert r["share_url"] == "https://www.snapworth.eu/i/" + r["code"]
        assert r["rewards"] == [] and r["rewards_left_this_year"] == 5

    def test_claim_route_end_to_end(self):
        code = client.post("/referral/status", json={"device_id": "dev-a"}, headers=self.H).json()["code"]
        run(load(POOL_FRIEND, ["APPLE1"]))
        r = client.post("/referral/claim", json={"device_id": "dev-b", "code": code},
                        headers={"x-device-id": "friend-install"})
        assert r.status_code == 200
        assert r.json()["redeem_url"].endswith("&code=APPLE1")

    def test_claim_errors_carry_the_users_message(self):
        r = client.post("/referral/claim", json={"device_id": "dev-b", "code": "ZZZZZZ"}, headers=self.H)
        assert r.status_code == 404 and "doesn't match" in r.json()["detail"]

    def test_device_id_is_validated(self):
        r = client.post("/referral/status", json={"device_id": "bad id!"}, headers=self.H)
        assert r.status_code == 422


class TestEntitlementHook:
    def test_the_entitlement_route_hands_the_sync_to_the_referral_hook(self, monkeypatch):
        """Wiring, not logic: `/auth/entitlement` must pass the verified
        entitlement and the request's device_id through, or no reward ever
        fires in production however well `on_entitlement` is tested."""
        ent = redeemed()

        async def record(subject, jws, device_id=None):
            return ent
        seen = []

        async def hook(subject, device_id, entitlement):
            seen.append((device_id, entitlement))
            return False
        monkeypatch.setattr(auth.deps.entitlements, "record", record)
        monkeypatch.setattr(referral, "on_entitlement", hook)
        r = client.post("/auth/entitlement", json={"signed_transaction": "x", "device_id": "friend-dev"},
                        headers={"x-device-id": "friend-install"})
        assert r.status_code == 200
        assert seen == [("friend-dev", ent)]
