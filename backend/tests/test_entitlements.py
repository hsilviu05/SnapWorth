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
    async def test_device_beyond_cap_rejected(self, service, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        for i in range(3):
            await service.record(f"device-{i}", jws)
        with pytest.raises(entitlements.DeviceLimitExceeded):
            await service.record("device-3", jws)

    @pytest.mark.asyncio
    async def test_rejected_device_gets_no_entitlement(self, service, pinned_root):
        """Binding runs before the cache write — a refused device stays free."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        for i in range(3):
            await service.record(f"device-{i}", jws)
        with pytest.raises(entitlements.DeviceLimitExceeded):
            await service.record("device-3", jws)
        assert (await service.current("device-3")).tier == "free"

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
