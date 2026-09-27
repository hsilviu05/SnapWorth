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


def redeemed(offer_identifier=FRIEND_OFFER, offer_type=3, otid="otid-1") -> Entitlement:
    return Entitlement("pro", "com.snapworth.yearly", None, otid, "Production",
                       offer_type=offer_type, offer_identifier=offer_identifier)


def bearer(subject: str) -> dict:
    """An App Attest principal, as the installed app always presents here."""
    token, _ = auth.deps.signer.mint(subject)
    return {"Authorization": f"Bearer {token}"}


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

    def test_a_sandbox_redemption_rewards_nothing(self, monkeypatch):
        """Production honours Sandbox for App Review and TestFlight, on bounded
        terms that keep it out of everything that counts money. A reward is a
        real Apple offer code, so it counts."""
        import dataclasses
        import entitlements
        monkeypatch.setattr(entitlements, "ALLOWED_ENVIRONMENTS", frozenset({"Production"}))
        self.claimed()
        run(load(POOL_REWARD, ["REWARD1"]))
        tester = dataclasses.replace(redeemed(), environment="Sandbox")
        assert run(referral.on_entitlement("subj-f", "friend", tester)) is False
        # The claim and the pool were left alone for the real redemption.
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is True

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
        r = client.post("/referral/status", json={"device_id": "dev-a"}, headers=bearer("subj-a")).json()
        assert r["enabled"] is True
        assert r["share_url"] == "https://www.snapworth.eu/i/" + r["code"]
        assert r["rewards"] == [] and r["rewards_left_this_year"] == 5

    def test_claim_route_end_to_end(self):
        code = client.post("/referral/status", json={"device_id": "dev-a"},
                           headers=bearer("subj-a")).json()["code"]
        run(load(POOL_FRIEND, ["APPLE1"]))
        r = client.post("/referral/claim", json={"device_id": "dev-b", "code": code},
                        headers=bearer("subj-b"))
        assert r.status_code == 200
        assert r.json()["redeem_url"].endswith("&code=APPLE1")

    def test_claim_errors_carry_the_users_message(self):
        r = client.post("/referral/claim", json={"device_id": "dev-b", "code": "ZZZZZZ"},
                        headers=bearer("subj-b"))
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

        async def record(subject, jws, device_id=None, *, authenticated=False):
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


# ── Hardening before referrals are switched on (AUDIT-2026-09-26) ────────────
#
# Every test below fails on the module as it was: routes that took any
# device_id from anyone, a reward deduped on that device_id alone, and a pool
# cursor that fell back to process memory.

class TestUnattestedCallersAreRefused:
    def test_both_routes_refuse_a_legacy_principal(self):
        """The legacy subject is `legacy:` plus a header the caller picks, so
        it can be bound to nothing and limited by nothing."""
        for path, body in (("/referral/status", {"device_id": "dev-a"}),
                           ("/referral/claim", {"device_id": "dev-a", "code": "ABCDEF"})):
            r = client.post(path, json=body, headers={"x-device-id": "any-string"})
            assert r.status_code == 401, (path, r.status_code)
            assert r.headers.get("WWW-Authenticate") == "Bearer"
        assert run(auth.deps.cache.get("ref:mine:dev-a")) is None

    def test_off_still_answers_everyone_and_writes_nothing(self, monkeypatch):
        """The app polls this on every foreground; off must stay the one
        cheap answer, for every build."""
        monkeypatch.setattr(referral, "config", ReferralConfig(enabled=False))
        r = client.post("/referral/status", json={"device_id": "dev-a"},
                        headers={"x-device-id": "any-string"})
        assert r.status_code == 200 and r.json()["enabled"] is False
        assert run(auth.deps.cache.get(referral._owner_key("dev-a"))) is None


class TestBinding:
    @staticmethod
    def status(subject, device):
        return client.post("/referral/status", json={"device_id": device},
                           headers=bearer(subject))

    def test_one_install_cannot_mint_codes_for_invented_devices(self):
        """The audit's farm: one attested install, a referral code per
        made-up device, each a pair of 400-day keys and a fresh yearly cap."""
        assert self.status("subj-a", "dev-a").status_code == 200
        for n in range(5):
            assert self.status("subj-a", f"invented-{n}").status_code == 403
            assert run(auth.deps.cache.get(f"ref:mine:invented-{n}")) is None

    def test_a_device_belongs_to_the_first_subject_that_presented_it(self):
        mine = self.status("subj-a", "dev-a").json()["code"]
        assert self.status("subj-b", "dev-a").status_code == 403
        assert self.status("subj-a", "dev-a").json()["code"] == mine

    def test_a_refused_subject_does_not_burn_the_device_it_named(self):
        self.status("subj-a", "dev-a")
        assert self.status("subj-a", "dev-b").status_code == 403
        assert self.status("subj-b", "dev-b").status_code == 200

    def test_a_claim_is_bound_the_same_way(self):
        code = self.status("subj-a", "dev-a").json()["code"]
        run(load(POOL_FRIEND, ["APPLE1", "APPLE2"]))
        self.status("subj-b", "dev-b")
        r = client.post("/referral/claim", json={"device_id": "dev-c", "code": code},
                        headers=bearer("subj-b"))
        assert r.status_code == 403
        assert run(referral.pool_remaining(POOL_FRIEND)) == 2

    def test_a_store_that_cannot_answer_is_a_503_not_a_binding_in_memory(self):
        """Redis configured and down: the binding must fail closed, or each
        replica keeps its own and the check is no check."""
        from cache import InMemoryCache, ResilientCache
        auth.deps.cache = ResilientCache(None, InMemoryCache(), configured=True)
        r = self.status("subj-a", "dev-a")
        assert r.status_code == 503 and "paused" in r.json()["detail"]


class TestLimiter:
    def test_both_routes_consult_the_limiter_with_route_subject_and_client_ip(self, monkeypatch):
        """The IP is `ratelimit.client_ip`'s: the first entry, which Railway
        writes, not its edge after it."""
        seen = []

        async def recording(route, subject, ip):
            seen.append((route, subject, ip))
        monkeypatch.setattr(referral, "limiter", recording)
        headers = {**bearer("subj-a"), "x-forwarded-for": "198.51.100.23, 95.173.10.20"}
        client.post("/referral/status", json={"device_id": "dev-a"}, headers=headers)
        client.post("/referral/claim", json={"device_id": "dev-a", "code": "ZZZZZZ"},
                    headers=headers)
        assert seen == [("status", "subj-a", "198.51.100.23"),
                        ("claim", "subj-a", "198.51.100.23")]

    @staticmethod
    def small_limits(monkeypatch):
        import main
        monkeypatch.setattr(referral, "limiter", main._enforce_referral_limit)
        monkeypatch.setattr(main, "_device_limiter", None)
        monkeypatch.setattr(main, "_ip_limiter", None)
        monkeypatch.setattr(main, "REFERRAL_RATE_MAX_REQUESTS", 2)
        monkeypatch.setattr(main, "REFERRAL_IP_RATE_MAX_REQUESTS", 3)
        for prefix in ("ref", "ref-claim"):
            for key in (f"{prefix}:lim-1", f"{prefix}:lim-2", f"{prefix}-ip:198.51.100.7"):
                main._device_memory.store.pop(key, None)
                main._ip_memory.store.pop(key, None)
        return main

    @staticmethod
    def post(path, subject, **body):
        return client.post(path, json={"device_id": f"dev-{subject}", **body},
                           headers={**bearer(subject), "x-forwarded-for": "198.51.100.7"})

    def test_per_subject_then_per_ip(self, monkeypatch):
        main = self.small_limits(monkeypatch)
        status = "/referral/status"
        assert [self.post(status, "lim-1").status_code for _ in range(3)] == [200, 200, 429]
        # A second install behind the same address: the IP bucket (3) is what
        # stops it, and it is not the scan route's `ip:` bucket.
        assert [self.post(status, "lim-2").status_code for _ in range(2)] == [429, 429]
        assert "ip:198.51.100.7" not in main._ip_memory.store

    def test_the_status_poll_cannot_spend_the_claim_allowance(self, monkeypatch):
        """The app asks /status on every foreground. Sharing one pair of
        buckets, those polls from everyone behind an address used up the
        claim allowance, and every installed build words a claim's 429 "Too
        many tries today. Try again tomorrow." — to a friend who had not
        tried."""
        self.small_limits(monkeypatch)
        for subject in ("lim-1", "lim-2"):         # both status buckets spent
            assert [self.post("/referral/status", subject).status_code
                    for _ in range(4)][-1] == 429
        r = self.post("/referral/claim", "lim-1", code="ZZZZZZ")
        assert r.status_code == 404, r.status_code   # the claim itself, not a 429

    def test_startup_wires_it(self):
        import inspect
        import main
        assert "referral.limiter = _enforce_referral_limit" in inspect.getsource(main._lifespan)


class TestRewardDedupe:
    def claim(self, subject, device, code):
        return run(referral.claim(subject, device, code))

    def test_one_apple_purchase_pays_out_once_whatever_device_it_is_synced_from(self):
        """`device_id` on the sync is the client's word. One real redemption,
        posted from three claimed devices, paid three rewards."""
        code = run(referral.code_for("referrer"))
        run(load(POOL_FRIEND, ["F1AAAA", "F2BBBB", "F3CCCC"]))
        run(load(POOL_REWARD, ["R1AAAA", "R2BBBB", "R3CCCC"]))
        for n in range(3):
            self.claim(f"subj-{n}", f"friend-{n}", code)
        paid = [run(referral.on_entitlement(f"subj-{n}", f"friend-{n}", redeemed()))
                for n in range(3)]
        assert paid == [True, False, False]
        assert len(run(referral.rewards_for("referrer"))) == 1
        # The refused devices were handed back: their own purchase still counts.
        assert run(referral.on_entitlement("subj-1", "friend-1", redeemed(otid="otid-2"))) is True

    def test_a_purchase_without_an_original_transaction_id_rewards_nothing(self):
        code = run(referral.code_for("referrer"))
        run(load(POOL_FRIEND, ["F1AAAA"]))
        run(load(POOL_REWARD, ["R1AAAA"]))
        self.claim("subj-f", "friend", code)
        assert run(referral.on_entitlement("subj-f", "friend", redeemed(otid=None))) is False


class _FlakyIncr:
    """A Redis whose INCR fails and everything else answers."""

    def __init__(self, store):
        self.store = store

    async def get(self, key):
        return await self.store.get(key)

    async def set(self, key, value, ttl=None):
        await self.store.set(key, value, ttl)

    async def add(self, key, value, ttl=None):
        return await self.store.add(key, value, ttl)

    async def incr(self, key, ttl=None, amount=1):
        raise ConnectionError("INCR timed out")

    async def delete(self, key):
        await self.store.delete(key)

    async def ping(self):
        return True


class TestPoolCursor:
    def test_a_failed_increment_never_hands_out_an_issued_code(self):
        """Not `required`, the INCR fell back to process memory and counted
        from 1 while Redis still answered the item read: the next friend got
        the code the first one had."""
        from cache import InMemoryCache, ResilientCache
        redis = InMemoryCache()
        run(redis.set(referral.pool_size_key(POOL_FRIEND), "2"))
        run(redis.set(referral.pool_item_key(POOL_FRIEND, 1), "ISSUED"))
        run(redis.set(referral.pool_item_key(POOL_FRIEND, 2), "FRESH1"))
        run(redis.set(referral.pool_cursor_key(POOL_FRIEND), "1"))
        auth.deps.cache = ResilientCache(_FlakyIncr(redis), InMemoryCache(), configured=True)
        referrer_code = "ABCDEF"
        run(redis.set(referral._code_key(referrer_code), "referrer"))
        with pytest.raises(ReferralError) as exc:
            run(referral.claim("subj-f", "friend", referrer_code))
        assert exc.value.status == 503
        # Undone at once, because this Redis still answers the deletes. One
        # that does not is TestInterruptedClaim.
        assert run(redis.get(referral._claim_key("friend"))) is None
        assert run(redis.get(referral._claim_subject_key("subj-f"))) is None

    def test_a_pool_that_ran_dry_does_not_skip_the_next_batch(self):
        """Each refused take moved the cursor past `size`, and the next batch
        is appended at `size + 1`: one code skipped per refusal."""
        async def go():
            await load(POOL_FRIEND, ["FIRST1"])
            first = await referral.take_code(POOL_FRIEND)
            dry = [await referral.take_code(POOL_FRIEND) for _ in range(3)]
            await load(POOL_FRIEND, ["NEXT01"])
            return first, dry, await referral.take_code(POOL_FRIEND)
        assert run(go()) == ("FIRST1", [None, None, None], "NEXT01")


class _Outage:
    """A Redis that goes down and stays down, deletes included.

    `_FlakyIncr` fails one kind of call and answers the rest, so every undo
    in its tests reached Redis. A real outage takes the undo with it. `trip`
    is the last call answered — (method, key prefix) — before every call
    fails, until `recover()`. `fail_once` fails that one call and no other.
    """

    def __init__(self, store, trip=None, fail_once=None):
        self.store = store
        self.down = False
        self.trip = trip
        self.fail_once = fail_once

    @staticmethod
    def _matches(which, method, key):
        return which is not None and which[0] == method and key.startswith(which[1])

    async def _call(self, method, key, *args):
        if self.down:
            raise ConnectionError("Redis is down")
        if self._matches(self.fail_once, method, key):
            self.fail_once = None
            raise ConnectionError(f"{method.upper()} timed out")
        result = await getattr(self.store, method)(key, *args)
        if self._matches(self.trip, method, key):
            self.down = True
        return result

    def recover(self):
        self.down, self.trip = False, None

    async def get(self, key):
        return await self._call("get", key)

    async def set(self, key, value, ttl=None):
        return await self._call("set", key, value, ttl)

    async def add(self, key, value, ttl=None):
        return await self._call("add", key, value, ttl)

    async def incr(self, key, ttl=None, amount=1):
        return await self._call("incr", key, ttl, amount)

    async def delete(self, key):
        return await self._call("delete", key)

    async def ping(self):
        return True


def _outage(**kwargs):
    """Redis (the store behind the fake) and the fake, wired in as the app's
    configured cache."""
    from cache import InMemoryCache, ResilientCache
    redis = InMemoryCache()
    fake = _Outage(redis, **kwargs)
    auth.deps.cache = ResilientCache(fake, InMemoryCache(), configured=True)
    return redis, fake


def _later(monkeypatch, seconds):
    """Move the clock, so what was written with a TTL can expire."""
    import time
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + seconds)


class TestInterruptedClaim:
    def setup(self, **kwargs):
        redis, fake = _outage(**kwargs)
        run(load(POOL_FRIEND, ["FRIEND1"]))
        run(redis.set(referral._code_key("ABCDEF"), "referrer"))
        return redis, fake

    def test_a_claim_cut_off_after_its_two_writes_does_not_lock_the_friend_out(self, monkeypatch):
        """Redis answered both markers and then went down, deletes included.
        Written for 400 days, the markers outlived the failed undo, and every
        retry was told "This phone has already used an invite" by a friend
        who had never been given a code."""
        redis, fake = self.setup(trip=("add", "ref:claimsubj:"))
        with pytest.raises(ReferralError) as exc:
            run(referral.claim("subj-f", "friend", "ABCDEF"))
        assert exc.value.status == 503
        fake.recover()
        held = json.loads(run(redis.get(referral._claim_key("friend"))))
        assert "friend_code" not in held               # the undo could not reach Redis
        # Straight after, it is still there: "try again later", which is true.
        with pytest.raises(ReferralError) as exc:
            run(referral.claim("subj-f", "friend", "ABCDEF"))
        assert exc.value.status == 503
        _later(monkeypatch, referral.PENDING_TTL + 1)
        assert run(referral.claim("subj-f", "friend", "ABCDEF")) == "FRIEND1"

    def test_a_finished_claim_is_kept_past_the_pending_window(self, monkeypatch):
        self.setup()
        run(load(POOL_FRIEND, ["FRIEND2"]))
        assert run(referral.claim("subj-f", "friend", "ABCDEF")) == "FRIEND1"
        _later(monkeypatch, referral.PENDING_TTL + 1)
        # Both markers were confirmed: the device, and the install on another.
        for device in ("friend", "other-device"):
            with pytest.raises(ReferralError) as exc:
                run(referral.claim("subj-f", device, "ABCDEF"))
            assert exc.value.status == 409
        assert run(referral.pool_remaining(POOL_FRIEND)) == 1


class TestInterruptedReward:
    def setup(self, **kwargs):
        redis, fake = _outage(**kwargs)
        run(load(POOL_REWARD, ["REWARD1", "REWARD2"]))
        run(redis.set(referral._claim_key("friend"),
                      json.dumps({"referrer": "referrer", "friend_code": "F1"})))
        return redis, fake

    @staticmethod
    def count(redis):
        import time
        return run(redis.get(referral._count_key("referrer", time.gmtime().tm_year)))

    def test_an_outage_that_outlasts_the_hand_back_delays_the_week_instead_of_losing_it(
            self, monkeypatch):
        """Down right after both markers, and still down for the undo. The
        undo raised inside the outage, the sync's catch-all swallowed it, and
        both markers stayed for 400 days: the referrer's week, gone."""
        redis, fake = self.setup(trip=("add", "ref:rewardedtxn:"))
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is False
        fake.recover()
        assert run(redis.get(referral._rewarded_key("friend"))) == "referrer"
        _later(monkeypatch, referral.PENDING_TTL + 1)
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is True
        assert [r["code"] for r in run(referral.rewards_for("referrer"))] == ["REWARD1"]
        assert self.count(redis) == "1"

    def test_a_second_marker_that_fails_hands_the_first_back(self):
        """The second marker's write sat outside the undo, so the first was
        never handed back."""
        redis, _ = self.setup(fail_once=("add", "ref:rewardedtxn:"))
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is False
        assert run(redis.get(referral._rewarded_key("friend"))) is None
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is True

    def test_a_count_that_never_landed_is_not_given_back(self):
        """Given back anyway, the count went to -1 and this referrer could be
        paid one week past the yearly cap."""
        redis, _ = self.setup(fail_once=("incr", "ref:count:"))
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is False
        assert self.count(redis) is None
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is True
        assert self.count(redis) == "1"

    def test_a_parked_week_is_not_paid_again_after_the_pending_window(self, monkeypatch):
        redis, _ = self.setup()
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is True
        _later(monkeypatch, referral.PENDING_TTL + 1)
        assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is False
        assert len(run(referral.rewards_for("referrer"))) == 1

    def test_each_step_the_undo_could_not_take_is_logged_with_the_purchase(self, caplog):
        """So a week that is lost after all — its free week over before a
        retry could run — can be found and reissued by hand (RUNBOOK §18)."""
        import logging
        import auditlog
        self.setup(trip=("incr", "ref:count:"))      # the count landed, then down
        with caplog.at_level(logging.WARNING, logger="snapworth.referral"):
            assert run(referral.on_entitlement("subj-f", "friend", redeemed())) is False
        mine = [r for r in caplog.records
                if getattr(r, "purchase", None) == auditlog.pseudonymise("otid-1")]
        assert {getattr(r, "marker", None) for r in mine} >= {"rewarded", "rewardedtxn"}
        assert any("count not given back" in r.getMessage() for r in mine)
        assert all(getattr(r, "referrer") == auditlog.pseudonymise("referrer") for r in mine)
        assert not any("otid-1" in r.getMessage() for r in caplog.records)


class _FakeSyncRedis:
    """The two calls the loader makes, on a dict."""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = str(value)
        return True


def _loader():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import load_referral_codes
    return load_referral_codes


class TestLoader:
    def test_appends_after_the_cursor_when_it_ran_past_the_pool(self):
        loader, r = _loader(), _FakeSyncRedis()
        loader.load(r, POOL_FRIEND, ["AAAAAA", "BBBBBB"])
        r.set(referral.pool_cursor_key(POOL_FRIEND), 4)     # two lost races
        loader.load(r, POOL_FRIEND, ["CCCCCC"])
        assert r.get(referral.pool_item_key(POOL_FRIEND, 5)) == "CCCCCC"
        assert r.get(referral.pool_size_key(POOL_FRIEND)) == "5"

    def test_retire_burns_what_is_left_and_the_next_batch_is_served(self):
        loader, r = _loader(), _FakeSyncRedis()
        loader.load(r, POOL_REWARD, ["AAAAAA", "BBBBBB", "CCCCCC"])
        r.set(referral.pool_cursor_key(POOL_REWARD), 1)
        assert loader.retire(r, POOL_REWARD) == 2
        assert r.get(referral.pool_cursor_key(POOL_REWARD)) == "3"
        loader.load(r, POOL_REWARD, ["DDDDDD"])
        assert r.get(referral.pool_item_key(POOL_REWARD, 4)) == "DDDDDD"

    def test_the_docstring_no_longer_calls_a_rerun_harmless_without_saying_when(self):
        doc = _loader().__doc__
        assert "A batch is burned by any loss of Redis's data" in doc
        assert "--retire" in doc


# ── What the operator is told ────────────────────────────────────────────────

class _Sent:
    def __init__(self):
        self.texts: list[str] = []

    def handler(self, request):
        import httpx
        if request.url.path.endswith("/sendMessage"):
            self.texts.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": []})


@pytest.fixture
def operator():
    """notify wired to the same cache the referral module uses, with a bot
    whose messages are captured."""
    import httpx
    import notify
    sent = _Sent()
    notifier = notify.TelegramNotifier(
        "123456789:AAtest-token-abcdefghijklmnopqrstuvwx", "424242",
        client=httpx.AsyncClient(transport=httpx.MockTransport(sent.handler)))
    notify.configure(auth.deps.cache, notifier=notifier)
    yield sent
    asyncio.run(notify.aclose())


async def _drain():
    import notify
    pending = [t for t in notify._tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _paid(otid="otid-1"):
    """The renewal after the free week: no offer, so Apple charged for it."""
    return Entitlement("pro", "com.snapworth.yearly", None, otid, "Production",
                       price=39.99, currency="USD")


class TestFunnelCounters:
    def test_claims_redemptions_rewards_and_paid_periods_reach_the_digest(self, operator):
        import notify
        from datetime import datetime, timedelta, timezone

        async def go():
            code = await referral.code_for("referrer")
            await load(POOL_FRIEND, ["F1AAAA", "F2BBBB"])
            await load(POOL_REWARD, ["R1AAAA"])
            await referral.claim("subj-1", "friend-1", code)
            await referral.claim("subj-2", "friend-2", code)
            assert await referral.on_entitlement("subj-1", "friend-1", redeemed()) is True
            # Re-syncs of the same purchase count nothing twice.
            await referral.on_entitlement("subj-1", "friend-1", redeemed())
            await referral.on_entitlement("subj-1", "friend-1", _paid())
            await referral.on_entitlement("subj-1", "friend-1", _paid())
            await _drain()
            now = datetime.now(timezone.utc) + timedelta(days=1)
            return await notify._digest_text(now - timedelta(days=1))
        digest = asyncio.run(go())
        assert ("Referrals: 2 claimed · 1 redeemed at Apple · 1 rewarded · "
                "1 paid after the free week") in digest

    def test_a_quiet_day_has_no_referral_line(self, operator):
        import notify
        from datetime import datetime, timezone
        digest = asyncio.run(notify._digest_text(datetime.now(timezone.utc)))
        assert "Referrals:" not in digest

    def test_apples_renewal_notice_counts_a_friend_who_never_reopened_the_app(self, operator):
        import appstorenotify
        import notify

        async def go():
            await auth.deps.cache.set(referral._redeemed_key("otid-9"), "1")
            note = appstorenotify.Notification(appstorenotify.DID_RENEW, None, "uuid-1",
                                               _paid("otid-9"))
            await notify.subscription_event(note)
            await notify.subscription_event(note)          # Apple redelivers
            await _drain()
            return await notify._read_stat(notify._day(), "referral_paid")
        assert asyncio.run(go()) == 1

    def test_a_paid_period_on_a_subscription_that_was_not_a_referral_counts_nothing(self):
        async def go():
            await referral.note_paid_period(_paid("otid-plain"))
            return await auth.deps.cache.get(referral._paid_key("otid-plain"))
        assert asyncio.run(go()) is None


class TestPoolAlerts:
    def test_low_then_empty_each_said_once_a_day(self, operator, monkeypatch):
        monkeypatch.setattr(referral, "POOL_LOW_AT", 1)

        async def go():
            await load(POOL_FRIEND, ["A1AAAA", "B2BBBB", "C3CCCC"])
            for _ in range(5):
                await referral.take_code(POOL_FRIEND)
                await _drain()
        asyncio.run(go())
        low = [t for t in operator.texts if "pool is low" in t]
        empty = [t for t in operator.texts if "pool is empty" in t]
        assert len(low) == 1 and "1 left" in low[0]
        assert len(empty) == 1 and "Invites are paused" in empty[0]

    def test_a_redis_outage_is_not_reported_as_an_empty_pool(self, operator):
        from cache import CacheUnavailable, InMemoryCache, ResilientCache
        auth.deps.cache = ResilientCache(None, InMemoryCache(), configured=True)

        async def go():
            with pytest.raises(CacheUnavailable):
                await referral.take_code(POOL_FRIEND)
            await _drain()
        asyncio.run(go())
        assert not [t for t in operator.texts if "pool" in t]

    def test_checkup_shows_the_pools_and_flags_a_low_one(self, operator, monkeypatch):
        import notify
        monkeypatch.setattr(referral, "POOL_LOW_AT", 2)

        async def go():
            await load(POOL_FRIEND, ["A1AAAA", "B2BBBB", "C3CCCC", "D4DDDD"])
            await load(POOL_REWARD, ["R1AAAA", "R2BBBB"])
            await referral.take_code(POOL_FRIEND)
            return await notify._referral_line()
        line = asyncio.run(go())
        assert line.startswith("Referrals: on · friend codes 3 of 4 left · reward codes 2 of 2 left")
        assert "⚠️ reward pool at 2" in line and "⚠️ friend" not in line

    def test_checkup_reports_an_outage_as_one_not_as_empty_pools(self, operator, monkeypatch):
        """Not required, both reads were answered (0, 0) from process memory,
        and the checkup said every invite was refused for want of codes."""
        import notify
        from cache import InMemoryCache, ResilientCache
        down = ResilientCache(None, InMemoryCache(), configured=True)
        monkeypatch.setattr(notify, "_cache", down)
        line = asyncio.run(notify._referral_line())
        assert line == "Referrals: on · pools unreadable (CacheUnavailable)"

    def test_checkup_when_off_is_one_quiet_line(self, operator, monkeypatch):
        import notify
        monkeypatch.setattr(referral, "config", ReferralConfig(enabled=False))
        assert asyncio.run(notify._referral_line()) == "Referrals: off"
