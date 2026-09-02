"""StoreKit JWS verification and audit-log tests.

Both were implemented without direct coverage in the first Phase 1 pass; this
closes that gap.

We cannot obtain a genuine Apple-signed transaction in CI, so the happy path is
exercised with a locally-generated chain injected via the pinned-root hook. Every
*rejection* path — which is where the security value lives — is tested against
real cryptography.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import os
import sys
import time

import jwt as pyjwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auditlog  # noqa: E402
import entitlements  # noqa: E402
from auditlog import AuditEvent  # noqa: E402
from entitlements import (  # noqa: E402
    ENTITLEMENT_CACHE_TTL,
    PRO_ENTITLEMENT_CACHE_TTL,
    Entitlement,
    EntitlementError,
    EntitlementService,
    verify_signed_transaction,
)

BUNDLE_ID = "eu.snapworth.app"
PRODUCTS = {"com.snapworth.monthly", "com.snapworth.yearly"}


# ── Helpers: build a locally-signed JWS mimicking Apple's shape ──────────────

def _make_cert(subject_name, issuer_name, subject_key, issuer_key, ca=False):
    now = dt.datetime.now(dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_name)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
        .public_key(subject_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
    )
    if ca:
        builder = builder.add_extension(x509.BasicConstraints(True, None), critical=True)
    return builder.sign(issuer_key, hashes.SHA256())


def build_chain():
    root_key = ec.generate_private_key(ec.SECP256R1())
    inter_key = ec.generate_private_key(ec.SECP256R1())
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    root = _make_cert("Test Root", "Test Root", root_key, root_key, ca=True)
    inter = _make_cert("Test Intermediate", "Test Root", inter_key, root_key, ca=True)
    leaf = _make_cert("Test Leaf", "Test Intermediate", leaf_key, inter_key)
    return leaf_key, [leaf, inter, root]


def make_jws(payload: dict, leaf_key, chain) -> str:
    x5c = [base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode()
           for c in chain]
    return pyjwt.encode(payload, leaf_key, algorithm="ES256", headers={"x5c": x5c})


def valid_payload(**overrides) -> dict:
    payload = {
        "bundleId": BUNDLE_ID,
        "productId": "com.snapworth.yearly",
        "originalTransactionId": "2000000000000001",
        "expiresDate": int((time.time() + 86_400) * 1000),
        "environment": "Production",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def pinned_root(monkeypatch):
    """Swap the pinned Apple root for our test root for the happy-path cases."""
    leaf_key, chain = build_chain()
    root_pem = chain[-1].public_bytes(serialization.Encoding.PEM)
    monkeypatch.setattr(entitlements, "APPLE_ROOT_CA_G3_PEM", root_pem)
    return leaf_key, chain


# ── Happy path ───────────────────────────────────────────────────────────────

class TestVerifySignedTransactionValid:
    def test_valid_subscription_grants_pro(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        ent = verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)
        assert ent.tier == "pro"
        assert ent.is_active
        assert ent.product_id == "com.snapworth.yearly"
        assert ent.original_transaction_id == "2000000000000001"

    def test_non_expiring_purchase_is_active(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(expiresDate=None), leaf_key, chain)
        assert verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS).is_active

    def test_expired_subscription_returns_free(self, pinned_root):
        leaf_key, chain = pinned_root
        past = int((time.time() - 86_400) * 1000)
        jws = make_jws(valid_payload(expiresDate=past), leaf_key, chain)
        assert verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS).tier == "free"

    def test_revoked_subscription_returns_free(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(revocationDate=int(time.time() * 1000)),
                       leaf_key, chain)
        assert verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS).tier == "free"

    def test_grace_period_keeps_recent_expiry_active(self, pinned_root):
        # Just-expired subscriptions stay active through Apple's billing retry.
        leaf_key, chain = pinned_root
        recent = int((time.time() - 60) * 1000)
        jws = make_jws(valid_payload(expiresDate=recent), leaf_key, chain)
        assert verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS).tier == "pro"

    def test_wrong_bundle_rejected(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(bundleId="com.attacker.app"), leaf_key, chain)
        with pytest.raises(EntitlementError, match="different app"):
            verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)

    def test_unknown_product_rejected(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(productId="com.snapworth.free"), leaf_key, chain)
        with pytest.raises(EntitlementError, match="unrecognised product"):
            verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)


# ── Rejection paths (real crypto, pinned Apple root) ────────────────────────

class TestVerifySignedTransactionRejects:
    def test_chain_not_rooted_in_apple_rejected(self):
        """The core control: a well-formed JWS signed by someone else."""
        leaf_key, chain = build_chain()
        jws = make_jws(valid_payload(), leaf_key, chain)
        with pytest.raises(EntitlementError, match="not rooted in Apple"):
            verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)

    def test_alg_none_rejected(self):
        # Classic alg-confusion: unsigned token claiming to be valid.
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "x5c": ["a", "b"]}).encode()).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(
            json.dumps(valid_payload()).encode()).rstrip(b"=").decode()
        with pytest.raises(EntitlementError, match="algorithm"):
            verify_signed_transaction(f"{header}.{body}.", BUNDLE_ID, PRODUCTS)

    def test_hs256_substitution_rejected(self):
        forged = pyjwt.encode(valid_payload(), "secret", algorithm="HS256",
                              headers={"x5c": ["a", "b"]})
        with pytest.raises(EntitlementError, match="algorithm"):
            verify_signed_transaction(forged, BUNDLE_ID, PRODUCTS)

    def test_missing_chain_rejected(self):
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        jws = pyjwt.encode(valid_payload(), leaf_key, algorithm="ES256")
        with pytest.raises(EntitlementError, match="incomplete"):
            verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)

    def test_tampered_payload_rejected(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        header, body, sig = jws.split(".")
        forged = base64.urlsafe_b64encode(
            json.dumps(valid_payload(productId="com.snapworth.monthly")).encode()
        ).rstrip(b"=").decode()
        with pytest.raises(EntitlementError):
            verify_signed_transaction(f"{header}.{forged}.{sig}", BUNDLE_ID, PRODUCTS)

    def test_empty_rejected(self):
        with pytest.raises(EntitlementError, match="Missing"):
            verify_signed_transaction("", BUNDLE_ID, PRODUCTS)

    def test_oversized_rejected(self):
        with pytest.raises(EntitlementError, match="oversized"):
            verify_signed_transaction("x" * 20_000, BUNDLE_ID, PRODUCTS)

    def test_garbage_rejected(self):
        with pytest.raises(EntitlementError):
            verify_signed_transaction("not-a-jws", BUNDLE_ID, PRODUCTS)


# ── EntitlementService caching ───────────────────────────────────────────────

class TestEntitlementService:
    @pytest.fixture
    def service(self):
        from cache import InMemoryCache, ResilientCache
        return EntitlementService(ResilientCache(None, InMemoryCache()), BUNDLE_ID, PRODUCTS)

    @pytest.mark.asyncio
    async def test_unknown_subject_is_free(self, service):
        assert (await service.current("nobody")).tier == "free"

    @pytest.mark.asyncio
    async def test_record_then_read(self, service, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        await service.record("subject-a", jws)
        assert (await service.current("subject-a")).tier == "pro"

    @pytest.mark.asyncio
    async def test_bad_jws_does_not_grant(self, service):
        leaf_key, chain = build_chain()          # not Apple-rooted
        with pytest.raises(EntitlementError):
            await service.record("subject-b", make_jws(valid_payload(), leaf_key, chain))
        assert (await service.current("subject-b")).tier == "free"

    # ── Cache lifetime ───────────────────────────────────────────────────
    #
    # Only the client can refresh this cache — it POSTs /auth/entitlement from
    # refreshSubscriptionStatus(), which runs at cold launch, purchase, restore
    # and Transaction.updates, with no foreground hook. iOS suspends apps
    # rather than terminating them, so at a 15-minute TTL a paying subscriber
    # who resumed the app read as `free` and got the free quota: one scan/day.

    @staticmethod
    def _recording_cache():
        """Wraps the real cache, capturing the TTL handed to each `set`."""
        from cache import InMemoryCache, ResilientCache

        class Recorder(ResilientCache):
            def __init__(self):
                super().__init__(None, InMemoryCache())
                self.ttls: dict[str, int | None] = {}

            async def set(self, key, value, ttl=None, **kw):
                self.ttls[key] = ttl
                return await super().set(key, value, ttl, **kw)

        return Recorder()

    @pytest.mark.asyncio
    async def test_pro_entitlement_outlives_a_launch_gap(self, pinned_root):
        leaf_key, chain = pinned_root
        cache = self._recording_cache()
        service = EntitlementService(cache, BUNDLE_ID, PRODUCTS)
        # Expiry far enough out that the subscription cap is not the binding
        # constraint, so we are measuring the entitlement TTL itself.
        payload = valid_payload(expiresDate=int((time.time() + 30 * 86_400) * 1000))
        await service.record("subject-ttl", make_jws(payload, leaf_key, chain))

        ttl = next(v for k, v in cache.ttls.items() if "subject-ttl" in k)
        assert ttl == PRO_ENTITLEMENT_CACHE_TTL
        assert ttl > ENTITLEMENT_CACHE_TTL, (
            "A Pro entitlement must survive longer than a 15-minute gap between "
            "cold launches, or the subscriber silently reverts to the free quota")

    @pytest.mark.asyncio
    async def test_ttl_never_outlives_the_subscription(self, pinned_root):
        leaf_key, chain = pinned_root
        cache = self._recording_cache()
        service = EntitlementService(cache, BUNDLE_ID, PRODUCTS)
        payload = valid_payload(expiresDate=int((time.time() + 600) * 1000))
        await service.record("subject-short", make_jws(payload, leaf_key, chain))

        ttl = next(v for k, v in cache.ttls.items() if "subject-short" in k)
        assert ttl <= 600, "Never cache a Pro entitlement past its own expiry"

    # ── Re-derivation from the stored proof ──────────────────────────────
    #
    # The TTL above narrows the window in which a subscriber reads as `free`.
    # These close it: the server keeps Apple's signed transaction and rebuilds
    # the entitlement itself, so a lapsed entry costs a signature check rather
    # than the free quota until the next cold launch.

    @pytest.mark.asyncio
    async def test_pro_survives_the_entitlement_entry_lapsing(self, service, pinned_root):
        leaf_key, chain = pinned_root
        await service.record("subject-f", make_jws(valid_payload(), leaf_key, chain))
        # Exactly the state a TTL expiry leaves behind.
        await service._cache.delete("ent:subject-f")

        assert (await service.current("subject-f")).tier == "pro", (
            "A paying subscriber must not fall to the free quota just because "
            "the cache entry lapsed and only the client can re-POST it")

    @pytest.mark.asyncio
    async def test_rederiving_repopulates_the_entry(self, service, pinned_root):
        leaf_key, chain = pinned_root
        await service.record("subject-g", make_jws(valid_payload(), leaf_key, chain))
        await service._cache.delete("ent:subject-g")
        await service.current("subject-g")

        assert await service._cache.get("ent:subject-g") is not None, (
            "Re-derivation should leave a cache entry so the next request is a "
            "plain hit, not another signature check")

    @pytest.mark.asyncio
    async def test_revocation_is_not_resurrected_by_the_proof(self, service, pinned_root):
        # The proof carries the revocation state it was signed with, not
        # today's, so a stale one must be dropped when Apple says otherwise.
        leaf_key, chain = pinned_root
        await service.record("subject-h", make_jws(valid_payload(), leaf_key, chain))
        revoked = valid_payload(revocationDate=int(time.time() * 1000))
        await service.record("subject-h", make_jws(revoked, leaf_key, chain))
        await service._cache.delete("ent:subject-h")

        assert (await service.current("subject-h")).tier == "free", (
            "A refunded subscription must not come back when the short free "
            "entry lapses")

    @pytest.mark.asyncio
    async def test_expired_proof_reads_free(self, service, pinned_root):
        # Written straight to the cache: `record` refuses to store a proof it
        # has just read as expired, so this is the shape of a proof that was
        # valid when stored and has since lapsed.
        leaf_key, chain = pinned_root
        past = int((time.time() - 86_400) * 1000)
        jws = make_jws(valid_payload(expiresDate=past), leaf_key, chain)
        await service._cache.set("entproof:subject-i", jws)

        assert (await service.current("subject-i")).tier == "free", (
            "A subscription that has actually ended must not be re-derived")

    @pytest.mark.asyncio
    async def test_tampered_proof_does_not_grant(self, service, pinned_root):
        # The stored proof is re-verified, so the cache is never the authority
        # on who is Pro — which is why the JWS is kept rather than the
        # entitlement derived from it.
        leaf_key, chain = pinned_root
        await service.record("subject-j", make_jws(valid_payload(), leaf_key, chain))
        stored = await service._cache.get("entproof:subject-j")
        header, _, sig = stored.split(".")
        forged = base64.urlsafe_b64encode(
            json.dumps(valid_payload(productId="com.snapworth.monthly")).encode()
        ).rstrip(b"=").decode()
        await service._cache.set("entproof:subject-j", f"{header}.{forged}.{sig}")
        await service._cache.delete("ent:subject-j")

        assert (await service.current("subject-j")).tier == "free"

    @pytest.mark.asyncio
    async def test_proof_outlives_the_entitlement_entry(self, pinned_root):
        leaf_key, chain = pinned_root
        cache = self._recording_cache()
        service = EntitlementService(cache, BUNDLE_ID, PRODUCTS)
        payload = valid_payload(expiresDate=int((time.time() + 30 * 86_400) * 1000))
        await service.record("subject-k", make_jws(payload, leaf_key, chain))

        entry_ttl = cache.ttls["ent:subject-k"]
        proof_ttl = cache.ttls["entproof:subject-k"]
        assert proof_ttl > entry_ttl, (
            "The proof exists to outlive the entry it rebuilds; equal TTLs "
            "would leave nothing to re-derive from")

    @pytest.mark.asyncio
    async def test_clear_removes_the_proof(self, service, pinned_root):
        leaf_key, chain = pinned_root
        await service.record("subject-l", make_jws(valid_payload(), leaf_key, chain))
        await service.clear("subject-l")

        assert await service._cache.get("entproof:subject-l") is None

    @pytest.mark.asyncio
    async def test_clear_revokes(self, service, pinned_root):
        leaf_key, chain = pinned_root
        await service.record("subject-c", make_jws(valid_payload(), leaf_key, chain))
        await service.clear("subject-c")
        assert (await service.current("subject-c")).tier == "free"

    @pytest.mark.asyncio
    async def test_cached_expired_entitlement_reads_free(self, service):
        # A cached record that has since expired must not keep granting Pro.
        stale = Entitlement("pro", "com.snapworth.yearly",
                            int(time.time()) - 100_000, "1", "Production")
        await service._cache.set("ent:subject-d", stale.to_json(), 900)
        assert (await service.current("subject-d")).tier == "free"

    @pytest.mark.asyncio
    async def test_corrupt_cache_entry_reads_free(self, service):
        await service._cache.set("ent:subject-e", "{not json", 900)
        assert (await service.current("subject-e")).tier == "free"


# ── Audit log ────────────────────────────────────────────────────────────────

class TestAuditLog:
    def test_subject_is_pseudonymised(self):
        raw = "a1b2c3d4e5f6"
        assert auditlog.pseudonymise(raw) != raw
        assert len(auditlog.pseudonymise(raw)) == 16

    def test_pseudonym_is_stable(self):
        assert auditlog.pseudonymise("x") == auditlog.pseudonymise("x")

    def test_distinct_subjects_differ(self):
        assert auditlog.pseudonymise("a") != auditlog.pseudonymise("b")

    def test_none_subject_handled(self):
        assert auditlog.pseudonymise(None) == "-"

    def test_record_emits_event(self, caplog):
        with caplog.at_level(logging.INFO, logger="snapworth.audit"):
            auditlog.record(AuditEvent.SCAN_AUTHORISED, "subject-1", tier="pro")
        assert any(r.message == "scan.authorised" for r in caplog.records)

    def test_failure_logs_at_warning(self, caplog):
        with caplog.at_level(logging.INFO, logger="snapworth.audit"):
            auditlog.record(AuditEvent.TOKEN_REJECTED, "s", outcome="failure",
                            reason="expired")
        record = next(r for r in caplog.records if r.message == "token.rejected")
        assert record.levelno == logging.WARNING
        assert record.reason == "expired"

    def test_raw_subject_never_appears_in_output(self, caplog):
        secret = "deadbeefcafebabe"
        with caplog.at_level(logging.INFO, logger="snapworth.audit"):
            auditlog.record(AuditEvent.QUOTA_CONSUMED, secret)
        assert all(secret not in str(r.__dict__) for r in caplog.records)

    def test_record_never_raises(self):
        # An audit failure must not break the request path.
        class _Unserialisable:
            def __repr__(self): raise RuntimeError("boom")
        auditlog.record(AuditEvent.SCAN_AUTHORISED, "s", weird=_Unserialisable())


# ── Environment gate ─────────────────────────────────────────────────────────
# Sandbox transactions are signed by the *same* Apple chain as production ones,
# so signature, bundle and product checks all pass for a free Sandbox tester
# subscription. The environment field is the only thing separating the two.

class TestEnvironmentGate:
    def test_sandbox_rejected_by_default(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(environment="Sandbox"), leaf_key, chain)
        with pytest.raises(EntitlementError, match="wrong environment"):
            verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)

    def test_unknown_environment_rejected(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(environment="Xcode"), leaf_key, chain)
        with pytest.raises(EntitlementError, match="wrong environment"):
            verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)

    def test_sandbox_accepted_when_explicitly_allowed(self, pinned_root):
        """Staging deployments opt in; production must not."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(environment="Sandbox"), leaf_key, chain)
        ent = verify_signed_transaction(
            jws, BUNDLE_ID, PRODUCTS, allowed_environments=frozenset({"Sandbox"}))
        assert ent.tier == "pro"
        assert ent.environment == "Sandbox"

    def test_production_still_accepted(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        assert verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS).tier == "pro"

    def test_missing_environment_defaults_to_production(self, pinned_root):
        """Backwards compatibility: absent field must not lock out real users."""
        leaf_key, chain = pinned_root
        payload = valid_payload()
        del payload["environment"]
        jws = make_jws(payload, leaf_key, chain)
        assert verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS).tier == "pro"

    def test_env_parsing_handles_lists_and_blanks(self):
        assert entitlements._parse_environments("Production,Sandbox") == frozenset(
            {"Production", "Sandbox"})
        assert entitlements._parse_environments("  ") == frozenset({"Production"})
        assert entitlements._parse_environments("Sandbox , ") == frozenset({"Sandbox"})


# ── Chain hardening ──────────────────────────────────────────────────────────

class TestChainConstraints:
    def test_non_ca_intermediate_rejected(self, monkeypatch):
        """An Apple-chained leaf must not be usable as an issuer."""
        root_key = ec.generate_private_key(ec.SECP256R1())
        inter_key = ec.generate_private_key(ec.SECP256R1())
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        root = _make_cert("Root", "Root", root_key, root_key, ca=True)
        # Intermediate deliberately lacks BasicConstraints CA:TRUE.
        inter = _make_cert("Inter", "Root", inter_key, root_key, ca=False)
        leaf = _make_cert("Leaf", "Inter", leaf_key, inter_key)
        chain = [leaf, inter, root]
        monkeypatch.setattr(entitlements, "APPLE_ROOT_CA_G3_PEM",
                            root.public_bytes(serialization.Encoding.PEM))
        jws = make_jws(valid_payload(), leaf_key, chain)
        with pytest.raises(EntitlementError, match="malformed"):
            verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)


# ── Device binding cap ───────────────────────────────────────────────────────
# The signed transaction reaches the client in plaintext, so one payer can share
# it. Each recipient attests under its own key and would otherwise become Pro.

class TestDeviceCap:
    @pytest.fixture
    def service(self):
        from cache import InMemoryCache, ResilientCache
        return EntitlementService(
            ResilientCache(None, InMemoryCache()), BUNDLE_ID, PRODUCTS, max_devices=3)

    @pytest.mark.asyncio
    async def test_devices_up_to_cap_allowed(self, service, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        for i in range(3):
            assert (await service.record(f"device-{i}", jws)).tier == "pro"

    @pytest.mark.asyncio
    async def test_device_beyond_cap_evicts_rather_than_refusing(self, service, pinned_root):
        """A payer is never locked out of their own subscription.

        This asserted the opposite until a subscriber hit it for real. An App
        Attest key is per *install*, so each reinstall arrives as a new subject
        and consumed a slot that was never released; after six the subscription
        was spent and every `/auth/entitlement` answered 409, which the client
        swallows. The subscriber then read as `free` — one scan a day, while
        paying.
        """
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        for i in range(3):
            await service.record(f"device-{i}", jws)

        assert (await service.record("device-3", jws)).tier == "pro"
        assert (await service.current("device-3")).tier == "pro"

    @pytest.mark.asyncio
    async def test_eviction_takes_the_least_recently_seen(self, service, pinned_root):
        # Seeded rather than recorded in a loop: consecutive `record` calls all
        # land in the same second, so the timestamps tie and the assertion
        # passes whichever end of the order eviction takes. Spreading them by
        # days is what makes this test able to fail.
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        now = int(time.time())
        await service._cache.set("txn:2000000000000001", json.dumps({
            "device-oldest": now - 3 * 86_400,
            "device-middle": now - 2 * 86_400,
            "device-newest": now - 1 * 86_400,
        }))

        await service.record("device-new", jws)
        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert "device-oldest" not in bindings, (
            "the least-recently-seen slot is the one to reuse — evicting an "
            "actively-used install just moves the lockout to another device")
        assert set(bindings) == {"device-middle", "device-newest", "device-new"}

    @pytest.mark.asyncio
    async def test_active_install_keeps_its_slot(self, service, pinned_root):
        """Re-recording refreshes last-seen, so use protects a binding."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        now = int(time.time())
        await service._cache.set("txn:2000000000000001", json.dumps({
            "device-a": now - 9 * 86_400,
            "device-b": now - 8 * 86_400,
            "device-c": now - 7 * 86_400,
        }))

        await service.record("device-a", jws)      # device-a checks in
        await service.record("device-new", jws)    # forces an eviction

        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert "device-a" in bindings, "an install that just checked in is not the LRU"
        assert "device-b" not in bindings

    @pytest.mark.asyncio
    async def test_reinstalling_repeatedly_never_locks_out(self, service, pinned_root):
        """The exact production sequence: one phone, many reinstalls."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        for install in range(12):
            assert (await service.record(f"install-{install}", jws)).tier == "pro", (
                f"reinstall {install} was refused — a paying subscriber is "
                "locked out of the subscription they are paying for")

    @pytest.mark.asyncio
    async def test_cap_still_bounds_concurrent_devices(self, service, pinned_root):
        """Eviction is not the same as no cap: the record stays bounded."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        for i in range(20):
            await service.record(f"device-{i}", jws)

        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert len(bindings) == 3

    @pytest.mark.asyncio
    async def test_dormant_bindings_age_out(self, service, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        stale = int(time.time()) - entitlements.DEVICE_BINDING_IDLE_SECONDS - 1
        await service._cache.set(
            "txn:2000000000000001",
            json.dumps({f"gone-{i}": stale for i in range(3)}))

        await service.record("fresh", jws)
        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert set(bindings) == {"fresh"}, "a month-silent install is a deleted one"

    @pytest.mark.asyncio
    async def test_an_already_full_legacy_record_self_heals(self, service, pinned_root):
        """The production recovery path: no Redis surgery needed.

        A subscription locked out under the old code holds a full *list* of
        subjects. The next `/auth/entitlement` must admit the caller by itself,
        because the alternative is deleting the key by hand for every affected
        subscriber.
        """
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        await service._cache.set(
            "txn:2000000000000001", json.dumps([f"stuck-{i}" for i in range(3)]))

        assert (await service.record("after-reinstall", jws)).tier == "pro"
        assert (await service.current("after-reinstall")).tier == "pro"

    @pytest.mark.asyncio
    async def test_legacy_list_records_are_readable(self, service, pinned_root):
        """Records written before last-seen tracking are plain JSON lists."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        await service._cache.set(
            "txn:2000000000000001", json.dumps(["old-a", "old-b"]))

        await service.record("new-c", jws)
        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert set(bindings) == {"old-a", "old-b", "new-c"}, (
            "an existing device must not be evicted just because the record "
            "predates timestamps")

    @pytest.mark.asyncio
    async def test_same_device_re_records_freely(self, service, pinned_root):
        """Token refresh must not consume a device slot each time."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        for _ in range(10):
            assert (await service.record("device-a", jws)).tier == "pro"

    @pytest.mark.asyncio
    async def test_distinct_subscriptions_have_independent_caps(self, service, pinned_root):
        leaf_key, chain = pinned_root
        jws_a = make_jws(valid_payload(originalTransactionId="A"), leaf_key, chain)
        jws_b = make_jws(valid_payload(originalTransactionId="B"), leaf_key, chain)
        for i in range(3):
            await service.record(f"a-{i}", jws_a)
        # A different subscription starts from an empty slot list.
        assert (await service.record("b-0", jws_b)).tier == "pro"


# ── Stable device identity ───────────────────────────────────────────────────
# From iOS 1.3.4 the client sends a Keychain-backed device id with the signed
# transaction. Binding on it instead of the per-install attest subject is what
# lets the record count phones rather than installs.

class TestDeviceIdentityBinding:
    @pytest.fixture
    def service(self):
        from cache import InMemoryCache, ResilientCache
        return EntitlementService(
            ResilientCache(None, InMemoryCache()), BUNDLE_ID, PRODUCTS, max_devices=3)

    @pytest.mark.asyncio
    async def test_reinstalls_on_one_phone_hold_one_slot(self, service, pinned_root):
        """The production sequence again, now with a stable id: no churn at all."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        for install in range(12):
            ent = await service.record(f"install-{install}", jws, device_id="phone-A")
            assert ent.tier == "pro"

        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert set(bindings) == {"phone-A"}, (
            "twelve installs of one phone must occupy one slot, not evict "
            "eleven times")

    @pytest.mark.asyncio
    async def test_distinct_devices_still_count_toward_the_cap(self, service, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        for phone in range(5):
            await service.record(f"install-{phone}", jws, device_id=f"phone-{phone}")

        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert len(bindings) == 3
        assert "phone-4" in bindings

    @pytest.mark.asyncio
    async def test_older_clients_are_still_bound_by_subject(self, service, pinned_root):
        """No device id (pre-1.3.4) keeps the previous behaviour exactly."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        await service.record("old-client-subject", jws)
        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert set(bindings) == {"old-client-subject"}

    @pytest.mark.asyncio
    async def test_a_phone_that_reinstalls_keeps_its_place_in_the_lru(self, service, pinned_root):
        """Re-recording under a new subject but the same device refreshes the
        existing slot rather than adding one — so it is never the LRU."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        now = int(time.time())
        await service._cache.set("txn:2000000000000001", json.dumps({
            "phone-A": now - 9 * 86_400,
            "phone-B": now - 8 * 86_400,
            "phone-C": now - 7 * 86_400,
        }))

        await service.record("phone-A-reinstalled", jws, device_id="phone-A")
        await service.record("new-install", jws, device_id="phone-D")   # evicts

        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert "phone-A" in bindings
        assert "phone-B" not in bindings
        assert len(bindings) == 3


# ── Original purchase date ───────────────────────────────────────────────────
# Carried so the operator alert can say whether a subscriber is new or merely
# re-syncing: renewals keep Apple's originalPurchaseDate.

class TestOriginalPurchaseDate:
    def test_parsed_from_the_signed_transaction(self, pinned_root):
        leaf_key, chain = pinned_root
        bought_ms = int((time.time() - 40 * 86_400) * 1000)
        jws = make_jws(valid_payload(originalPurchaseDate=bought_ms), leaf_key, chain)
        ent = verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)
        assert ent.original_purchase_at == bought_ms // 1000

    def test_absent_is_tolerated(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        assert verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS).original_purchase_at is None

    def test_survives_the_cache_round_trip(self):
        ent = Entitlement("pro", "com.snapworth.monthly", 1_900_000_000,
                          "otid", "Production", original_purchase_at=1_800_000_000)
        assert Entitlement.from_json(ent.to_json()) == ent

    def test_entries_cached_before_this_field_still_load(self):
        # Production cache holds entitlements written without the key.
        raw = json.dumps({"tier": "pro", "product_id": "com.snapworth.yearly",
                          "expires_at": 1_900_000_000,
                          "original_transaction_id": "otid",
                          "environment": "Production"})
        assert Entitlement.from_json(raw).original_purchase_at is None
