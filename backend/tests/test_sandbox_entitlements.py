"""Sandbox purchases on bounded terms (`entitlements.SANDBOX_ENTITLEMENTS`).

App Review buys in Sandbox, and so does every TestFlight build, against the one
backend the app has. Production accepted only `Production`, so a reviewer who
bought Pro got a 400 from `/auth/entitlement`, then the paywall again: the
Guideline 2.1 / 3.1.1 "purchased content not delivered" rejection
(AUDIT-2026-09-26, "Production rejects Sandbox purchases").

The fix honours Sandbox, but only on terms that make a tester's transaction
worth nothing to anyone else. Each class below is one of those terms, and each
has a test that would pass for the unbounded version and fails for this one.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import auth  # noqa: E402
import entitlements  # noqa: E402
import main  # noqa: E402
import notify  # noqa: E402
from appstorenotify import Notification  # noqa: E402
from cache import InMemoryCache, ResilientCache  # noqa: E402
from tests.conftest import not_none  # noqa: E402
from entitlements import (  # noqa: E402
    EXPIRY_GRACE_SECONDS,
    PRO_ENTITLEMENT_CACHE_TTL,
    SANDBOX_ENTITLEMENT_TTL,
    EntitlementError,
    EntitlementService,
    EntitlementsUnavailable,
    is_bounded,
)
from test_appstorenotify import make_notification  # noqa: E402
from test_entitlements import (  # noqa: E402
    BUNDLE_ID,
    PRODUCTS,
    build_chain,
    make_jws,
    valid_payload,
)

client = TestClient(main.app)

OTID = valid_payload()["originalTransactionId"]
MONTH = 30 * 86_400


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def production_defaults(monkeypatch):
    """The deployment these tests describe: Production trusted, Sandbox bounded.

    Pinned rather than read from the environment, so a developer's shell cannot
    turn a test of the default into a test of something else.
    """
    monkeypatch.setattr(entitlements, "ALLOWED_ENVIRONMENTS", frozenset({"Production"}))
    monkeypatch.setattr(entitlements, "SANDBOX_ENTITLEMENTS", entitlements.SANDBOX_BOUNDED)


@pytest.fixture
def pinned(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    leaf_key, chain = build_chain()
    monkeypatch.setattr(entitlements, "APPLE_ROOT_CA_G3_PEM",
                        chain[-1].public_bytes(serialization.Encoding.PEM))
    return leaf_key, chain


def sandbox(pinned, **overrides) -> str:
    leaf_key, chain = pinned
    return make_jws(valid_payload(environment="Sandbox", **overrides), leaf_key, chain)


def production(pinned, **overrides) -> str:
    leaf_key, chain = pinned
    return make_jws(valid_payload(**overrides), leaf_key, chain)


def in_a(seconds: int) -> int:
    """An `expiresDate`, in Apple's milliseconds."""
    return int((time.time() + seconds) * 1000)


def token_for(subject: str) -> str:
    """A bearer token as `/auth/attest` would mint it: App Attest-backed."""
    token, _ = not_none(auth.deps.signer).mint(subject)
    return token


class RecordingCache(ResilientCache):
    """The real in-memory cache, noting the TTL handed to every `set`."""

    def __init__(self, **kwargs):
        super().__init__(None, InMemoryCache(), **kwargs)
        self.ttls: dict[str, int | None] = {}

    async def set(self, key, value, ttl=None, **kw):
        self.ttls[key] = ttl
        return await super().set(key, value, ttl, **kw)


class _Redis:
    """A durable backend that can go down and come back.

    Behind `ResilientCache(..., configured=True)` this is production's shape:
    while `down`, a `required` call raises `CacheUnavailable` and every other
    call is quietly served from process memory instead.
    """

    def __init__(self) -> None:
        self.down = False
        self._store = InMemoryCache()

    def _up(self) -> InMemoryCache:
        if self.down:
            raise ConnectionError("redis is down")
        return self._store

    async def get(self, key: str) -> str | None:
        return await self._up().get(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        await self._up().set(key, value, ttl)

    async def add(self, key: str, value: str, ttl: int | None = None) -> bool:
        return await self._up().add(key, value, ttl)

    async def incr(self, key: str, ttl: int | None = None, amount: int = 1) -> int:
        return await self._up().incr(key, ttl, amount)

    async def delete(self, key: str) -> None:
        await self._up().delete(key)

    async def ping(self) -> bool:
        return await self._up().ping()


@pytest.fixture
def cache() -> RecordingCache:
    return RecordingCache()


@pytest.fixture
def service(cache) -> EntitlementService:
    return EntitlementService(cache, BUNDLE_ID, PRODUCTS)


# ── An attested caller gets Pro, briefly, with nothing kept ─────────────────

class TestAuthenticatedSandbox:
    def test_an_attested_reviewer_who_buys_is_pro(self, service, pinned):
        ent = run(service.record("reviewer", sandbox(pinned),
                                 device_id="review-iphone", authenticated=True))
        assert ent.tier == "pro"
        assert ent.environment == "Sandbox"
        assert is_bounded(ent)
        assert run(service.current("reviewer")).tier == "pro", (
            "the purchase was accepted and then not honoured on the next request")

    def test_it_lives_a_day_at_most(self, service, cache, pinned):
        run(service.record("reviewer", sandbox(pinned, expiresDate=in_a(MONTH)),
                           authenticated=True))
        assert cache.ttls["ent:reviewer"] == SANDBOX_ENTITLEMENT_TTL
        assert cache.ttls[f"entsandbox:{OTID}"] == SANDBOX_ENTITLEMENT_TTL

    def test_it_never_outlives_its_transaction(self, service, cache, pinned):
        """Sandbox renews a monthly plan every few minutes; the transaction's
        expiry, not the day, is what normally ends it. The grace is the same
        hour `is_active` allows everything, and is what covers the gap between
        a renewal and the client re-presenting it."""
        run(service.record("reviewer", sandbox(pinned, expiresDate=in_a(300)),
                           authenticated=True))
        ttl = cache.ttls["ent:reviewer"]
        assert ttl is not None and ttl <= 300 + EXPIRY_GRACE_SECONDS
        assert ttl < SANDBOX_ENTITLEMENT_TTL

    def test_no_proof_is_stored(self, service, cache, pinned):
        run(service.record("reviewer", sandbox(pinned, expiresDate=in_a(MONTH)),
                           authenticated=True))
        assert run(cache.get("entproof:reviewer")) is None
        assert not [k for k in cache.ttls if k.startswith("entproof:")], (
            "a Sandbox transaction was kept as a 400-day proof")

    def test_nothing_rederives_it_once_the_entry_lapses(self, service, pinned):
        """The difference from Production, where the proof rebuilds a lapsed
        entry: here the device has to present a live transaction again."""
        run(service.record("reviewer", sandbox(pinned, expiresDate=in_a(MONTH)),
                           authenticated=True))
        run(service._cache.delete("ent:reviewer"))
        assert run(service.current("reviewer")).tier == "free"

    def test_the_six_device_binding_is_not_used(self, service, pinned):
        run(service.record("reviewer", sandbox(pinned), authenticated=True))
        assert run(service._cache.get(f"txn:{OTID}")) is None

    def test_a_sandbox_transaction_without_an_id_is_refused(self, service, pinned):
        """The one-device rule is keyed on it; without one there is nothing
        to bound."""
        leaf_key, chain = pinned
        payload = valid_payload(environment="Sandbox")
        del payload["originalTransactionId"]
        with pytest.raises(EntitlementError, match="original transaction id"):
            run(service.record("reviewer", make_jws(payload, leaf_key, chain),
                               authenticated=True))
        assert run(service.current("reviewer")).tier == "free"


# ── Never without App Attest ────────────────────────────────────────────────

class TestUnauthenticatedSandbox:
    def test_refused_without_an_attested_caller(self, service, pinned):
        with pytest.raises(EntitlementError, match="wrong environment"):
            run(service.record("legacy:someone", sandbox(pinned)))
        assert run(service.current("legacy:someone")).tier == "free"
        assert run(service._cache.get(f"entsandbox:{OTID}")) is None

    def test_the_legacy_path_gets_the_old_400(self, pinned):
        """No bearer token: `require_auth` falls back to the legacy principal,
        whose subject is a header the caller chose."""
        r = client.post("/auth/entitlement",
                        json={"signed_transaction": sandbox(pinned)},
                        headers={"x-device-id": "anyone"})
        assert r.status_code == 400
        assert "wrong environment" in r.json()["detail"]

    def test_an_invalid_token_is_refused_before_anything(self, pinned):
        r = client.post("/auth/entitlement",
                        json={"signed_transaction": sandbox(pinned)},
                        headers={"Authorization": "Bearer not-a-token"})
        assert r.status_code == 401

    def test_a_forged_sandbox_transaction_is_refused_even_attested(self, pinned):
        other_key, other_chain = build_chain()        # not Apple's root
        forged = make_jws(valid_payload(environment="Sandbox"), other_key, other_chain)
        token = token_for("attested-subject")
        r = client.post("/auth/entitlement", json={"signed_transaction": forged},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 400

    def test_an_attested_caller_gets_pro_over_http(self, pinned):
        token = token_for("attested-subject")
        r = client.post("/auth/entitlement",
                        json={"signed_transaction": sandbox(pinned),
                              "device_id": "review-iphone"},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        assert r.json()["tier"] == "pro"
        assert r.json()["access_token"]
        assert run(auth.deps.entitlements.current("attested-subject")).tier == "pro"


# ── One device at a time, and the newest wins ───────────────────────────────

class TestOneDevicePerTransaction:
    def test_a_second_device_takes_it_over(self, service, pinned):
        jws = sandbox(pinned)
        run(service.record("iphone-install", jws, device_id="iphone", authenticated=True))
        run(service.record("ipad-install", jws, device_id="ipad", authenticated=True))

        assert run(service.current("ipad-install")).tier == "pro"
        assert run(service.current("iphone-install")).tier == "free", (
            "two devices are Pro on one Sandbox transaction")

    def test_the_first_device_can_take_it_back(self, service, pinned):
        jws = sandbox(pinned)
        run(service.record("iphone-install", jws, device_id="iphone", authenticated=True))
        run(service.record("ipad-install", jws, device_id="ipad", authenticated=True))
        run(service.record("iphone-install", jws, device_id="iphone", authenticated=True))

        assert run(service.current("iphone-install")).tier == "pro"
        assert run(service.current("ipad-install")).tier == "free"

    def test_a_reinstall_on_the_same_phone_moves_the_claim_to_the_new_install(
            self, service, pinned):
        jws = sandbox(pinned)
        run(service.record("old-install", jws, device_id="phone", authenticated=True))
        run(service.record("new-install", jws, device_id="phone", authenticated=True))

        assert run(service.current("new-install")).tier == "pro"
        assert run(service.current("old-install")).tier == "free"
        claim = json.loads(run(service._cache.get(f"entsandbox:{OTID}")))
        assert claim["subject"] == "new-install" and claim["device"] == "phone"

    def test_displacement_takes_nothing_but_the_sandbox_grant(self, service, pinned):
        """A device that also holds a real subscription keeps it."""
        run(service.record("both", production(pinned, originalTransactionId="real-sub",
                                              expiresDate=in_a(MONTH))))
        jws = sandbox(pinned)
        run(service.record("both", jws, device_id="phone-a", authenticated=True))
        run(service.record("other", jws, device_id="phone-b", authenticated=True))

        ent = run(service.current("both"))
        assert ent.tier == "pro" and ent.environment == "Production"

    def test_over_http_the_second_device_displaces_the_first(self, pinned):
        jws = sandbox(pinned)
        for subject, device in (("sub-iphone", "iphone"), ("sub-ipad", "ipad")):
            token = token_for(subject)
            r = client.post("/auth/entitlement",
                            json={"signed_transaction": jws, "device_id": device},
                            headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200 and r.json()["tier"] == "pro"

        assert run(auth.deps.entitlements.current("sub-ipad")).tier == "pro"
        assert run(auth.deps.entitlements.current("sub-iphone")).tier == "free"


# ── A Sandbox refund withdraws it, and cannot touch anyone real ─────────────

class TestSandboxRevocation:
    def test_a_refund_withdraws_it_at_the_next_request(self, service, pinned):
        """Not when the day-long entry lapses: the claim is checked on every
        read, and the revocation drops it."""
        run(service.record("reviewer", sandbox(pinned), authenticated=True))
        leaf_key, chain = pinned
        refunded = entitlements.verify_signed_transaction(
            make_jws(valid_payload(environment="Sandbox",
                                   revocationDate=int(time.time() * 1000)),
                     leaf_key, chain),
            BUNDLE_ID, PRODUCTS, allowed_environments=frozenset({"Sandbox"}),
            allow_inactive=True)
        assert run(service.revoke(refunded)) is True

        assert run(service._cache.get("ent:reviewer")) is not None, (
            "precondition: the entry is still cached, so this measures the claim")
        assert run(service.current("reviewer")).tier == "free"

    def test_the_pre_refund_transaction_is_refused_afterwards(self, service, pinned):
        jws = sandbox(pinned)
        ent = run(service.record("reviewer", jws, authenticated=True))
        run(service.revoke(ent, revoked_at=int(time.time())))

        again = run(service.record("reviewer", jws, authenticated=True))
        assert again.tier == "free"
        assert run(service.current("reviewer")).tier == "free"

    def test_a_sandbox_tombstone_cannot_deny_a_production_subscriber(
            self, service, pinned):
        """Apple does not promise the two environments' ids never meet, and a
        tester can have a REFUND signed for their own purchase at will."""
        ent = run(service.record("tester", sandbox(pinned), authenticated=True))
        run(service.revoke(ent, revoked_at=int(time.time())))

        paid = run(service.record("customer", production(pinned, expiresDate=in_a(MONTH))))
        assert paid.tier == "pro"
        run(service._cache.delete("ent:customer"))
        assert run(service.current("customer")).tier == "pro", (
            "a Sandbox refund took away a Production subscription with the same id")


# ── An ended Sandbox transaction is not Apple's word on anything else ───────

class TestExpiredSandbox:
    def test_it_does_not_void_a_real_subscription(self, service, pinned):
        run(service.record("both", production(pinned, originalTransactionId="real-sub",
                                              expiresDate=in_a(MONTH))))
        expired = sandbox(pinned, expiresDate=in_a(-2 * EXPIRY_GRACE_SECONDS))

        result = run(service.record("both", expired, authenticated=True))

        assert result.tier == "free" and is_bounded(result)
        assert run(service._cache.get("entproof:both")) is not None, (
            "a tester's expiry deleted a paying subscriber's stored proof")
        assert run(service.current("both")).tier == "pro"


# ── `off` is the old behaviour, without a deploy ────────────────────────────

class TestSwitchedOff:
    def test_off_refuses_sandbox_as_before(self, service, pinned, monkeypatch):
        monkeypatch.setattr(entitlements, "SANDBOX_ENTITLEMENTS", entitlements.SANDBOX_OFF)
        with pytest.raises(EntitlementError, match="wrong environment"):
            run(service.record("reviewer", sandbox(pinned), authenticated=True))

    def test_off_over_http_is_the_old_400(self, pinned, monkeypatch):
        monkeypatch.setattr(entitlements, "SANDBOX_ENTITLEMENTS", entitlements.SANDBOX_OFF)
        token = token_for("attested-subject")
        r = client.post("/auth/entitlement", json={"signed_transaction": sandbox(pinned)},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 400
        assert "wrong environment" in r.json()["detail"]

    def test_turning_it_off_withdraws_what_is_already_cached(
            self, service, pinned, monkeypatch):
        run(service.record("reviewer", sandbox(pinned), authenticated=True))
        monkeypatch.setattr(entitlements, "SANDBOX_ENTITLEMENTS", entitlements.SANDBOX_OFF)
        assert run(service.current("reviewer")).tier == "free"

    @pytest.mark.parametrize("raw, mode", [
        (None, "bounded"), ("", "bounded"), ("bounded", "bounded"),
        (" Bounded ", "bounded"), ("off", "off"), ("OFF", "off"),
        # A typo must not be what widens access.
        ("on", "off"), ("bounde", "off"), ("true", "off"),
    ])
    def test_the_switch_parses_closed(self, raw, mode):
        assert entitlements._parse_sandbox_mode(raw) == mode


# ── Fails closed when the claim cannot be kept ──────────────────────────────

class TestDurableStoreOutage:
    @staticmethod
    def _outage_service() -> EntitlementService:
        # Redis configured but unreachable: `required` calls raise.
        return EntitlementService(
            ResilientCache(None, InMemoryCache(), configured=True), BUNDLE_ID, PRODUCTS)

    def test_the_claim_is_not_skipped_when_redis_is_down(self, pinned):
        with pytest.raises(EntitlementsUnavailable):
            run(self._outage_service().record("reviewer", sandbox(pinned),
                                              authenticated=True))

    def test_over_http_that_is_a_retryable_503(self, pinned, monkeypatch):
        monkeypatch.setattr(auth.deps, "entitlements", self._outage_service())
        token = token_for("attested-subject")
        r = client.post("/auth/entitlement", json={"signed_transaction": sandbox(pinned)},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 503


# ── Not a customer: no row, no count, no alert, no reward ───────────────────

class _Sends:
    """Stands in for the Telegram notifier; records what would be pushed."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send(self, text, buttons=None) -> bool:
        self.texts.append(text)
        return True


@pytest.fixture
def operator(monkeypatch):
    """The operator's bot, wired to the same cache the app records into."""
    sends = _Sends()
    monkeypatch.setattr(notify, "_notifier", sends)
    monkeypatch.setattr(notify, "_cache", auth.deps.cache)
    # Activity tracking spawns background work on the request's loop; it is
    # not what these tests measure.
    monkeypatch.setattr(notify, "saw_user", lambda *a, **k: None)
    return sends


def _new_subs() -> str | None:
    return run(auth.deps.cache.get(notify._stat_key(notify._day(), "new_subs")))


def _index() -> dict:
    return json.loads(run(auth.deps.cache.get(notify.SUBS_INDEX_KEY)) or "{}")


def _post(jws: str, subject: str = "attested-subject"):
    token = token_for(subject)
    return client.post("/auth/entitlement", json={"signed_transaction": jws},
                       headers={"Authorization": f"Bearer {token}"})


class TestNotCounted:
    def test_a_sandbox_purchase_is_not_indexed_counted_or_announced(
            self, operator, pinned):
        r = _post(sandbox(pinned))
        assert r.status_code == 200 and r.json()["tier"] == "pro"

        assert operator.texts == [], f"the operator was told: {operator.texts}"
        assert _index() == {}, "a tester is in /subs and its MRR"
        assert _new_subs() is None, "a tester counted as a new subscription"

    def test_a_production_purchase_still_is(self, operator, pinned):
        """The control: the same harness does see a real sale."""
        r = _post(production(pinned))
        assert r.status_code == 200 and r.json()["tier"] == "pro"

        assert any("New Pro subscription" in t for t in operator.texts)
        assert OTID in _index()
        assert _new_subs() == "1"

    def test_an_ended_sandbox_transaction_is_not_a_churn_alert(self, operator, pinned):
        r = _post(sandbox(pinned, expiresDate=in_a(-2 * EXPIRY_GRACE_SECONDS)))
        assert r.status_code == 200 and r.json()["tier"] == "free"
        assert operator.texts == []

    def test_the_index_writer_itself_refuses_a_tester(self, operator):
        """Every writer shares `_index_subscription`, so the rule is there too."""
        tester = entitlements.Entitlement(
            "pro", "com.snapworth.yearly", int(time.time()) + 3600, OTID, "Sandbox")
        assert run(notify._index_subscription("someone", tester)) == {}
        assert _index() == {}

    def test_a_sandbox_notification_that_reaches_the_feed_is_dropped(self, operator):
        tester = entitlements.Entitlement(
            "pro", "com.snapworth.yearly", int(time.time()) + 3600, OTID, "Sandbox")
        note = Notification(notification_type="SUBSCRIBED", subtype=None,
                            uuid="u-1", entitlement=tester, environment="Sandbox")
        run(notify.subscription_event(note))
        assert operator.texts == [] and _index() == {} and _new_subs() is None

    def test_staging_that_trusts_sandbox_fully_still_indexes_it(
            self, operator, pinned, monkeypatch):
        """ALLOWED_STOREKIT_ENVIRONMENTS keeps its meaning: listed, Sandbox
        is treated as a customer, exactly as a staging deployment had it."""
        monkeypatch.setattr(entitlements, "ALLOWED_ENVIRONMENTS",
                            frozenset({"Production", "Sandbox"}))
        r = _post(sandbox(pinned))
        assert r.status_code == 200 and r.json()["tier"] == "pro"
        assert OTID in _index()


# ── The Production path does what it always did ─────────────────────────────

class TestProductionUnchanged:
    def test_an_attested_production_purchase_takes_the_full_path(
            self, service, cache, pinned):
        ent = run(service.record("customer", production(pinned, expiresDate=in_a(MONTH)),
                                 device_id="phone", authenticated=True))
        assert ent.tier == "pro" and not is_bounded(ent)
        assert cache.ttls["ent:customer"] == PRO_ENTITLEMENT_CACHE_TTL
        assert run(cache.get("entproof:customer")) is not None
        assert "phone" in json.loads(run(cache.get(f"txn:{OTID}")))
        assert run(cache.get(f"entsandbox:{OTID}")) is None

    def test_a_production_purchase_without_attest_is_still_recorded(
            self, service, pinned):
        """`authenticated` gates Sandbox only."""
        assert run(service.record("legacy:x", production(pinned))).tier == "pro"

    def test_production_tombstones_keep_their_key(self, service, pinned):
        """RUNBOOK §16 tells the operator to write this key by hand."""
        ent = run(service.record("customer", production(pinned)))
        run(service.revoke(ent, revoked_at=int(time.time())))
        assert run(service._cache.get(f"entrevoked:{OTID}")) is not None

    def test_staging_that_trusts_sandbox_takes_the_full_path(
            self, service, pinned, monkeypatch):
        monkeypatch.setattr(entitlements, "ALLOWED_ENVIRONMENTS",
                            frozenset({"Production", "Sandbox"}))
        ent = run(service.record("tester", sandbox(pinned)))
        assert ent.tier == "pro" and not is_bounded(ent)
        assert run(service._cache.get("entproof:tester")) is not None


# ── Sandbox notifications: their own URL, refunds only ──────────────────────

class TestSandboxNotificationRoute:
    @pytest.fixture(autouse=True)
    def wired(self, monkeypatch):
        monkeypatch.setattr(main, "_cache", ResilientCache(None, InMemoryCache()))
        main._ip_rate_store.clear()
        main._rate_store.clear()
        yield
        main._ip_rate_store.clear()
        main._rate_store.clear()

    @staticmethod
    def _refund(pinned, uuid="sbx-refund-1", environment="Sandbox"):
        leaf_key, chain = pinned
        return make_notification(
            leaf_key, chain, notification_type="REFUND", uuid=uuid,
            environment=environment, revocationDate=int(time.time() * 1000))

    def test_production_s_route_still_refuses_sandbox(self, pinned):
        """Everything it does after its refund branch feeds the revenue view."""
        r = client.post("/apple/notifications",
                        json={"signedPayload": self._refund(pinned)})
        assert r.status_code == 400
        assert "wrong environment" in r.json()["detail"]

    def test_a_sandbox_refund_withdraws_the_bounded_grant(self, pinned):
        store = auth.deps.entitlements
        run(store.record("reviewer", sandbox(pinned), authenticated=True))
        assert run(store.current("reviewer")).tier == "pro"

        r = client.post("/apple/notifications/sandbox",
                        json={"signedPayload": self._refund(pinned)})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "revoked"
        assert run(store.current("reviewer")).tier == "free"

    def test_every_other_type_is_acknowledged_and_ignored(self, operator, pinned):
        leaf_key, chain = pinned
        r = client.post("/apple/notifications/sandbox", json={
            "signedPayload": make_notification(
                leaf_key, chain, notification_type="SUBSCRIBED",
                uuid="sbx-sub-1", environment="Sandbox")})
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"
        assert operator.texts == [] and _index() == {} and _new_subs() is None

    def test_a_production_notification_is_refused_here(self, pinned):
        r = client.post("/apple/notifications/sandbox",
                        json={"signedPayload": self._refund(pinned, environment="Production")})
        assert r.status_code == 400

    def test_anything_apple_did_not_sign_is_refused(self, pinned):
        other_key, other_chain = build_chain()
        forged = make_notification(other_key, other_chain, notification_type="REFUND",
                                   environment="Sandbox")
        assert client.post("/apple/notifications/sandbox",
                           json={"signedPayload": forged}).status_code == 400
        assert client.post("/apple/notifications/sandbox",
                           json={"signedPayload": "not-a-jws"}).status_code == 400
        assert client.post("/apple/notifications/sandbox", json={}).status_code == 400

    def test_off_means_the_route_is_not_there(self, pinned, monkeypatch):
        monkeypatch.setattr(entitlements, "SANDBOX_ENTITLEMENTS", entitlements.SANDBOX_OFF)
        r = client.post("/apple/notifications/sandbox",
                        json={"signedPayload": self._refund(pinned)})
        assert r.status_code == 404

    def test_apple_s_retry_is_a_duplicate(self, pinned):
        payload = self._refund(pinned, uuid="sbx-repeat")
        assert client.post("/apple/notifications/sandbox",
                           json={"signedPayload": payload}).json()["status"] == "revoked"
        again = client.post("/apple/notifications/sandbox", json={"signedPayload": payload})
        assert again.status_code == 200 and again.json()["status"] == "duplicate"

    def test_apple_s_test_notification_gets_a_200(self, pinned):
        leaf_key, chain = pinned
        envelope = make_jws({
            "notificationType": "TEST", "notificationUUID": "sbx-test-1",
            "version": "2.0",
            "data": {"bundleId": BUNDLE_ID, "environment": "Sandbox"},
        }, leaf_key, chain)
        r = client.post("/apple/notifications/sandbox", json={"signedPayload": envelope})
        assert r.status_code == 200 and r.json()["status"] == "test"

    @pytest.mark.parametrize("route, seen", [
        ("/apple/notifications/sandbox", "apns2:sandbox:{uuid}"),
        ("/apple/notifications", "apns2:{uuid}"),
    ])
    def test_a_failed_revoke_is_a_503_and_leaves_the_retry_real(
            self, pinned, monkeypatch, route, seen):
        """Both routes share the helper that hands the idempotency key back;
        a 503 that kept it would turn Apple's retry into a no-op 'duplicate'."""
        environment = "Sandbox" if route.endswith("sandbox") else "Production"
        uuid = f"fail-{environment}"
        payload = self._refund(pinned, uuid=uuid, environment=environment)

        store_down = True
        real_revoke = auth.deps.entitlements.revoke

        async def flaky(*args, **kwargs):
            if store_down:
                raise RuntimeError("redis gone")
            return await real_revoke(*args, **kwargs)
        monkeypatch.setattr(auth.deps.entitlements, "revoke", flaky)

        r = client.post(route, json={"signedPayload": payload})
        assert r.status_code == 503
        assert run(not_none(main._cache).get(seen.format(uuid=uuid))) is None

        store_down = False
        again = client.post(route, json={"signedPayload": payload})
        assert again.status_code == 200
        assert again.json()["status"] != "duplicate"

    @pytest.mark.parametrize("route, environment, seen", [
        ("/apple/notifications/sandbox", "Sandbox", "apns2:sandbox:{uuid}"),
        ("/apple/notifications", "Production", "apns2:{uuid}"),
    ])
    def test_a_redis_outage_is_a_503_not_a_revocation_in_memory(
            self, pinned, monkeypatch, route, environment, seen):
        """The test above makes `revoke` itself raise. A real outage never did:
        on the cache production wires in, a write that is not `required` falls
        back to this process's memory and returns normally. So the tombstone,
        and for Sandbox the claim's removal, landed on one replica only, Apple
        got a 200 and never retried, and once Redis was back the holder was
        Pro again — from the proof for Production, from the claim still in
        Redis for Sandbox."""
        redis = _Redis()
        cache = ResilientCache(redis, InMemoryCache(), configured=True)
        store = EntitlementService(cache, BUNDLE_ID, PRODUCTS)
        monkeypatch.setattr(auth.deps, "entitlements", store)
        monkeypatch.setattr(main, "_cache", cache)

        # The same term the refund below names, so its tombstone covers it.
        jws = sandbox(pinned) if environment == "Sandbox" else production(pinned)
        run(store.record("holder", jws, authenticated=True))
        assert run(store.current("holder")).tier == "pro"

        uuid = f"outage-{environment}"
        payload = self._refund(pinned, uuid=uuid, environment=environment)
        redis.down = True
        r = client.post(route, json={"signedPayload": payload})
        assert r.status_code == 503, (
            "Apple was told the refund was handled while the revocation "
            "existed only in one process's memory")
        assert run(cache.get(seen.format(uuid=uuid))) is None

        redis.down = False
        again = client.post(route, json={"signedPayload": payload})
        assert again.status_code == 200, again.text
        assert again.json()["status"] != "duplicate"
        if environment == "Production":
            # The day-long entry lapses, as it does in production; the
            # tombstone is what stops the proof re-deriving Pro.
            run(cache.delete("ent:holder"))
        else:
            assert run(cache.get("ent:holder")) is not None, (
                "precondition: the entry is still cached, so this measures the claim")
        assert run(store.current("holder")).tier == "free"
