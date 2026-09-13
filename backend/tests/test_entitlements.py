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

def _make_cert(subject_name, issuer_name, subject_key, issuer_key, ca=False,
               purpose_oid=None):
    """Build one certificate.

    `purpose_oid` adds the marker extension Apple puts on its App Store signing
    certificates. The contents are irrelevant — both Apple's verifier and ours
    check only that the extension is *present* — so an empty value is a
    faithful stand-in.
    """
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
    if purpose_oid is not None:
        builder = builder.add_extension(
            x509.UnrecognizedExtension(x509.ObjectIdentifier(purpose_oid), b""),
            critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


def build_chain(leaf_purpose=entitlements.LEAF_PURPOSE_OID,
                intermediate_purpose=entitlements.INTERMEDIATE_PURPOSE_OID):
    """A three-certificate chain shaped like Apple's.

    The purpose OIDs are parameters so a test can build a chain that is
    otherwise perfect but carries the wrong marker — the case the extension
    check exists for, and the one an Apple Pay leaf would present.
    """
    root_key = ec.generate_private_key(ec.SECP256R1())
    inter_key = ec.generate_private_key(ec.SECP256R1())
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    root = _make_cert("Test Root", "Test Root", root_key, root_key, ca=True)
    inter = _make_cert("Test Intermediate", "Test Root", inter_key, root_key,
                       ca=True, purpose_oid=intermediate_purpose)
    leaf = _make_cert("Test Leaf", "Test Intermediate", leaf_key, inter_key,
                      purpose_oid=leaf_purpose)
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
        with pytest.raises(EntitlementError, match="three"):
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
    async def test_a_revoked_jws_the_client_presents_drops_the_proof(
            self, service, pinned_root):
        # Kept for what it does cover — a revoked transaction arriving through
        # `record` — but note the shipped client cannot make this call:
        # `StoreKitPurchaseService` sets `activeJWS` only when
        # `revocationDate == nil`, and `currentEntitlements` omits revoked
        # transactions entirely. This test was the whole coverage for refunds,
        # and it was green over a path that does not exist in production. The
        # real path is the notification, below.
        leaf_key, chain = pinned_root
        await service.record("subject-h", make_jws(valid_payload(), leaf_key, chain))
        revoked = valid_payload(revocationDate=int(time.time() * 1000))
        await service.record("subject-h", make_jws(revoked, leaf_key, chain))
        await service._cache.delete("ent:subject-h")

        assert (await service.current("subject-h")).tier == "free", (
            "A refunded subscription must not come back when the short free "
            "entry lapses")

    # ── Revocation by notification ──────────────────────────────────────────
    #
    # The path that actually happens. A refund does not move `expiresDate` and
    # Apple never rewrites a signed transaction, so the pre-refund proof keeps
    # verifying and keeps reading active. `_rederive` re-cached Pro from it on
    # every miss: a yearly refunded on day three bought unlimited scans until
    # the following September, with no client involvement at all.

    @pytest.mark.asyncio
    async def test_a_refund_stops_the_proof_re_deriving_pro(
            self, service, pinned_root):
        leaf_key, chain = pinned_root
        payload = valid_payload()
        await service.record("subject-refund", make_jws(payload, leaf_key, chain))
        assert (await service.current("subject-refund")).tier == "pro"

        # What the notification handler does, with the entitlement Apple's
        # REFUND notification carries.
        refunded = entitlements.Entitlement(
            tier="pro", product_id=payload["productId"],
            expires_at=payload["expiresDate"] // 1000,
            original_transaction_id=payload["originalTransactionId"],
            environment="Production",
            revoked_at=int(time.time()))
        assert await service.revoke(refunded) is True

        # The short entry lapses, exactly as it does in production.
        await service._cache.delete("ent:subject-refund")

        assert (await service.current("subject-refund")).tier == "free", (
            "the refunded term is being re-derived from the pre-refund proof")

    @pytest.mark.asyncio
    async def test_a_revoked_proof_is_deleted_not_just_ignored(
            self, service, pinned_root):
        # Otherwise every later request pays for a signature check to reach
        # the same answer.
        leaf_key, chain = pinned_root
        payload = valid_payload()
        await service.record("subject-del", make_jws(payload, leaf_key, chain))
        await service.revoke(entitlements.Entitlement(
            tier="pro", product_id=payload["productId"],
            expires_at=payload["expiresDate"] // 1000,
            original_transaction_id=payload["originalTransactionId"],
            environment="Production"))
        await service._cache.delete("ent:subject-del")
        await service.current("subject-del")

        assert await service._cache.get("entproof:subject-del") in (None, ""), (
            "the dead proof is still stored")

    @pytest.mark.asyncio
    async def test_a_client_cannot_re_present_a_revoked_term(
            self, service, pinned_root):
        # The other way in. `record` must consult the tombstone too, or a
        # cold launch would re-grant what the refund took away.
        leaf_key, chain = pinned_root
        payload = valid_payload()
        await service.revoke(entitlements.Entitlement(
            tier="pro", product_id=payload["productId"],
            expires_at=payload["expiresDate"] // 1000,
            original_transaction_id=payload["originalTransactionId"],
            environment="Production"))

        ent = await service.record("subject-re",
                                   make_jws(payload, leaf_key, chain))
        assert ent.tier == "free"
        assert await service._cache.get("entproof:subject-re") in (None, "")

    @pytest.mark.asyncio
    async def test_re_subscribing_after_a_refund_works(self, service, pinned_root):
        # The reason the tombstone stores the revoked term's expiry rather
        # than only the revocation date: `originalTransactionId` is stable
        # across renewals *and* re-subscriptions, so keyed on the id alone
        # this would deny someone who later paid again, permanently.
        leaf_key, chain = pinned_root
        first = valid_payload()
        await service.revoke(entitlements.Entitlement(
            tier="pro", product_id=first["productId"],
            expires_at=first["expiresDate"] // 1000,
            original_transaction_id=first["originalTransactionId"],
            environment="Production"))

        # A later term, same original transaction id, expiring further out.
        again = valid_payload(
            expiresDate=int((time.time() + 400 * 86_400) * 1000))
        ent = await service.record("subject-again",
                                   make_jws(again, leaf_key, chain))

        assert ent.tier == "pro", (
            "a re-subscription was killed by the tombstone for the term "
            "before it")

    @pytest.mark.asyncio
    async def test_a_revocation_without_a_transaction_id_is_not_retried(
            self, service):
        # Nothing to key on, and nothing a retry would fix — so this reports
        # False rather than raising, and the webhook answers 200.
        assert await service.revoke(entitlements.Entitlement(
            tier="pro", product_id="com.snapworth.yearly", expires_at=None,
            original_transaction_id=None, environment="Production")) is False

    @pytest.mark.asyncio
    async def test_a_non_expiring_revocation_kills_the_term_outright(
            self, service, pinned_root):
        # No expiry to compare against, so there is no later term it could be
        # confused with.
        leaf_key, chain = pinned_root
        payload = valid_payload()
        await service.revoke(entitlements.Entitlement(
            tier="pro", product_id=payload["productId"], expires_at=None,
            original_transaction_id=payload["originalTransactionId"],
            environment="Production"))

        ent = await service.record("subject-perp",
                                   make_jws(payload, leaf_key, chain))
        assert ent.tier == "free"

    @pytest.mark.asyncio
    async def test_an_unrelated_subscription_is_untouched(
            self, service, pinned_root):
        leaf_key, chain = pinned_root
        await service.revoke(entitlements.Entitlement(
            tier="pro", product_id="com.snapworth.yearly", expires_at=None,
            original_transaction_id="2000000000000999",
            environment="Production"))

        ent = await service.record("subject-other",
                                   make_jws(valid_payload(), leaf_key, chain))
        assert ent.tier == "pro", "a tombstone leaked onto another subscription"

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

    @pytest.mark.parametrize("length", [0, 1, 2, 4, 24])
    def test_chain_must_be_exactly_three(self, length, pinned_root):
        """Length is bounded before any certificate is parsed.

        `_verify_chain` does work proportional to the list the caller sent — a
        validity check per certificate and an ECDSA verification per adjacent
        pair — and `/apple/notifications` cannot be authenticated.

        `verify_apple_jws` already caps the whole JWS at 16,384 characters,
        which holds a chain to roughly twenty-five certificates: the
        amplification was about eightfold, not unbounded. 24 is the largest
        length that still fits under that cap, so it is the case that
        distinguishes this check from the one already there — a longer chain
        is rejected by the size cap and proves nothing about this bound.
        """
        leaf_key, chain = pinned_root
        # Pad by repeating the intermediate: the padding is well-formed, so a
        # rejection can only come from the length check itself.
        stretched = (chain[:1] + [chain[1]] * max(0, length - 2) + chain[-1:])[:length]
        x5c = [base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode()
               for c in stretched]
        jws = pyjwt.encode(valid_payload(), leaf_key, algorithm="ES256",
                           headers={"x5c": x5c})
        with pytest.raises(EntitlementError, match="three"):
            verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)

    def test_leaf_without_the_app_store_purpose_oid_rejected(self, monkeypatch):
        """The case root pinning alone cannot catch.

        Apple Root CA G3 also anchors branches that issue P-256 leaves to
        enrolled developers who hold the private key — an Apple Pay
        payment-processing certificate being the plainest example. Such a leaf
        satisfies every other property this module checks, so without the
        purpose extension its holder could sign their own `transactionId` and
        be granted Pro.
        """
        leaf_key, chain = build_chain(leaf_purpose="1.2.840.113635.100.6.38.7")
        monkeypatch.setattr(entitlements, "APPLE_ROOT_CA_G3_PEM",
                            chain[-1].public_bytes(serialization.Encoding.PEM))
        jws = make_jws(valid_payload(), leaf_key, chain)
        with pytest.raises(EntitlementError,
                           match="leaf is not an App Store signing"):
            verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)

    def test_intermediate_without_the_wwdr_purpose_oid_rejected(self, monkeypatch):
        leaf_key, chain = build_chain(intermediate_purpose=None)
        monkeypatch.setattr(entitlements, "APPLE_ROOT_CA_G3_PEM",
                            chain[-1].public_bytes(serialization.Encoding.PEM))
        jws = make_jws(valid_payload(), leaf_key, chain)
        with pytest.raises(EntitlementError,
                           match="intermediate is not an App Store signing"):
            verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)

    def test_the_pinned_oids_are_apple_s(self):
        """Guards the one thing the generated-certificate tests cannot.

        Every chain above is built by this file, so it would satisfy whatever
        OID string `entitlements` happened to name — a typo would pass the
        suite and reject every genuine transaction in production, taking Pro
        away from all paying users at once. These two literals are transcribed
        from apple/app-store-server-library-python.
        """
        assert entitlements.LEAF_PURPOSE_OID == "1.2.840.113635.100.6.11.1"
        assert entitlements.INTERMEDIATE_PURPOSE_OID == "1.2.840.113635.100.6.2.1"


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


# ── Sharing alert vs. the 1.3.4 migration ────────────────────────────────────
# A record written before device ids holds one attest subject per reinstall.
# The first launch with a device id evicts one of those; that is not sharing.

class TestSharingAlertOnMigration:
    @pytest.fixture
    def service(self):
        from cache import InMemoryCache, ResilientCache
        return EntitlementService(
            ResilientCache(None, InMemoryCache()), BUNDLE_ID, PRODUCTS, max_devices=3)

    @pytest.fixture
    def alerts(self, monkeypatch):
        import notify
        calls: list[dict] = []
        monkeypatch.setattr(notify, "subscription_over_cap",
                            lambda *a, **k: calls.append(k))
        return calls

    @pytest.mark.asyncio
    async def test_evicting_a_reinstall_ghost_is_not_reported(self, service, pinned_root, alerts):
        """The exact production message: one phone, six old subjects, first
        launch of 1.3.4 — '🔁 more than 6 devices active … likely sharing'."""
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        now = int(time.time())
        await service._cache.set("txn:2000000000000001", json.dumps({
            "a" * 64: now - 36 * 3600,
            "b" * 64: now - 30 * 3600,
            "c" * 64: now - 24 * 3600,
        }))

        await service.record("d" * 64, jws, device_id="8F1C2A3E-0000-4000-8000-000000000001")

        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert "a" * 64 not in bindings, "the ghost is still evicted"
        assert alerts == [], "but evicting a ghost is migration, not sharing"

    @pytest.mark.asyncio
    async def test_evicting_a_real_device_is_reported(self, service, pinned_root, alerts):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        now = int(time.time())
        await service._cache.set("txn:2000000000000001", json.dumps({
            "8F1C2A3E-0000-4000-8000-000000000001": now - 36 * 3600,
            "8F1C2A3E-0000-4000-8000-000000000002": now - 30 * 3600,
            "8F1C2A3E-0000-4000-8000-000000000003": now - 24 * 3600,
        }))

        await service.record("d" * 64, jws, device_id="8F1C2A3E-0000-4000-8000-000000000004")

        assert len(alerts) == 1
        assert alerts[0]["idle_seconds"] >= 36 * 3600 - 5

    @pytest.mark.asyncio
    async def test_a_hex_shaped_device_id_cannot_silence_the_alert(
            self, service, pinned_root, alerts):
        """The eviction alert is the only remaining control on a shared JWS.

        `_is_legacy_subject` infers the *kind* of an identity from the shape of
        its string, and `device_id` arrives from the client — the wire pattern
        `^[A-Za-z0-9._-]+$` with `max_length=64` admits a 64-character
        lowercase hex string perfectly well. So a sharer who names every
        install `0000…01` is read as a pre-1.3.4 reinstall ghost, and the
        eviction the cap exists to report is filed as migration instead.

        Measured against the real function before the fix: 20 sharers at a cap
        of 6 produced 14 evictions and zero alerts with hex-shaped ids, and 14
        evictions with 14 alerts when the same 20 were UUIDs.

        What discriminates here is the *storage* assertion at the bottom, not
        the alert count: with the record already holding prefixed ids, the
        evicted one is outside the legacy class either way, so the alert fires
        on the old code too. The alert assertion is the control — it says the
        prefix did not cost the signal — and the prefix is what restores the
        classification on every record from here on.

        One honest limit: bindings already written as bare hex before this fix
        stay misclassified until they age out at `DEVICE_BINDING_IDLE_SECONDS`
        or that install records again. Nothing rewrites them, because nothing
        can tell them apart from a real pre-1.3.4 subject — which is the same
        ambiguity the prefix exists to stop creating.
        """
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(), leaf_key, chain)
        now = int(time.time())
        # Three devices already bound, each one named to look like an attest
        # subject. The oldest is the one that will be pushed out.
        await service._cache.set("txn:2000000000000001", json.dumps({
            "dev:" + "%064x" % 1: now - 36 * 3600,
            "dev:" + "%064x" % 2: now - 30 * 3600,
            "dev:" + "%064x" % 3: now - 24 * 3600,
        }))

        await service.record("d" * 64, jws, device_id="%064x" % 4)

        assert len(alerts) == 1, \
            "a device id shaped like a subject is still a device being evicted"
        assert alerts[0]["idle_seconds"] >= 36 * 3600 - 5

        bindings = json.loads(await service._cache.get("txn:2000000000000001"))
        assert "dev:" + "%064x" % 4 in bindings, \
            "stored under a prefix the client cannot forge — no colon in the pattern"
        assert "%064x" % 4 not in bindings

    def test_identity_kinds_are_distinguishable(self):
        assert entitlements._is_legacy_subject("0f" * 32)
        assert not entitlements._is_legacy_subject("8F1C2A3E-0000-4000-8000-000000000001")
        assert not entitlements._is_legacy_subject("device-1")
        # And the prefix `_bind_device` applies takes a hex-shaped device id
        # back out of the legacy class, which is the whole point of it.
        assert not entitlements._is_legacy_subject("dev:" + "0f" * 32)


# ── Offer and price fields ───────────────────────────────────────────────────
# Carried so the operator can tell a comped subscription from a paying one.

class TestOfferAndPrice:
    def test_parsed_from_the_signed_transaction(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(offerType=3, offerDiscountType="FREE_TRIAL",
                                     price=39990, currency="USD"), leaf_key, chain)
        ent = verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)
        assert (ent.offer_type, ent.offer_discount_type) == (3, "FREE_TRIAL")
        assert (ent.price, ent.currency) == (39.99, "USD")

    def test_absent_is_tolerated_and_round_trips(self, pinned_root):
        leaf_key, chain = pinned_root
        ent = verify_signed_transaction(make_jws(valid_payload(), leaf_key, chain),
                                        BUNDLE_ID, PRODUCTS)
        assert ent.offer_type is None and ent.price is None
        assert Entitlement.from_json(ent.to_json()) == ent

    def test_garbage_types_are_ignored(self, pinned_root):
        leaf_key, chain = pinned_root
        jws = make_jws(valid_payload(offerType="three", price="lots", currency=7),
                       leaf_key, chain)
        ent = verify_signed_transaction(jws, BUNDLE_ID, PRODUCTS)
        assert ent.offer_type is None and ent.price is None and ent.currency is None
