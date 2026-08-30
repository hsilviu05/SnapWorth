"""Phase 1 verification: authentication, entitlement, quota, replay.

Covers the acceptance criteria stated for the security phase:
  * anonymous requests fail (when enforcement is on)
  * expired tokens fail
  * tampered tokens fail
  * invalid App Attest fails
  * free-scan limits cannot be bypassed
  * reinstall does not reset limits
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import cbor2
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import appattest  # noqa: E402
import auth  # noqa: E402
from cache import CacheUnavailable, InMemoryCache, ResilientCache  # noqa: E402
from main import app  # noqa: E402
from quota import QuotaExceeded, QuotaUnavailable, ScanQuota  # noqa: E402
from tests.conftest import TEST_APP_ID, build_deps  # noqa: E402
from tests.images import VALID_JPEG  # noqa: E402
from tokens import TokenError, TokenSigner  # noqa: E402

client = TestClient(app)

MOCK_AI = {
    "item_name": "Nike Shoes", "brand": "Nike", "category": "shoes",
    "condition_notes": "Good", "est_value_low_usd": 50.0, "est_value_high_usd": 100.0,
    "confidence": "High", "listing_title": "Nike Shoes", "listing_description": "Great.",
}


def _scan(headers=None, device_id="auth-test"):
    mock = MagicMock()
    mock.text = json.dumps(MOCK_AI)
    h = {"x-device-id": device_id}
    h.update(headers or {})
    with patch("main._model") as m:
        m.generate_content_async = AsyncMock(return_value=mock)
        return client.post(
            "/scan",
            files={"file": ("s.jpg", io.BytesIO(VALID_JPEG), "image/jpeg")},
            headers=h,
        )


# ── Token signing, expiry, tampering, rotation ───────────────────────────────

class TestTokens:
    def setup_method(self):
        self.signer = TokenSigner({"k1": b"secret-one-01234567890123456789"}, "k1")

    def test_round_trip(self):
        token, claims = self.signer.mint("subject-a", tier="pro")
        verified = self.signer.verify(token)
        assert verified["sub"] == "subject-a"
        assert verified["tier"] == "pro"
        assert verified["jti"] == claims["jti"]

    def test_expired_token_rejected(self):
        token, _ = self.signer.mint("subject-a", ttl=-100)
        with pytest.raises(TokenError, match="expired"):
            self.signer.verify(token, leeway=0)

    def test_tampered_payload_rejected(self):
        token, _ = self.signer.mint("subject-a", tier="free")
        payload, sig = token.split(".")
        forged = json.dumps({"sub": "subject-a", "tier": "pro", "exp": int(time.time()) + 999,
                             "iat": int(time.time()), "jti": "x", "kid": "k1"}).encode()
        bad = base64.urlsafe_b64encode(forged).rstrip(b"=").decode()
        with pytest.raises(TokenError, match="signature"):
            self.signer.verify(f"{bad}.{sig}")

    def test_tampered_signature_rejected(self):
        token, _ = self.signer.mint("subject-a")
        payload, sig = token.split(".")
        flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
        with pytest.raises(TokenError, match="signature"):
            self.signer.verify(f"{payload}.{flipped}")

    def test_token_from_other_key_rejected(self):
        other = TokenSigner({"k1": b"a-completely-different-secret-key"}, "k1")
        token, _ = other.mint("subject-a")
        with pytest.raises(TokenError, match="signature"):
            self.signer.verify(token)

    def test_unknown_kid_rejected(self):
        token, _ = TokenSigner({"retired": b"old-secret-000000000000000000"}, "retired").mint("s")
        with pytest.raises(TokenError, match="key is not recognised"):
            self.signer.verify(token)

    def test_malformed_token_rejected(self):
        for bad in ["", "no-dot", "a.b.c", "!!!.???"]:
            with pytest.raises(TokenError):
                self.signer.verify(bad)

    def test_oversized_token_rejected(self):
        with pytest.raises(TokenError):
            self.signer.verify("x" * 5000)

    def test_extra_claims_cannot_override_tier(self):
        # A caller-supplied `tier` must not beat the server-set one.
        token, claims = self.signer.mint("s", tier="free", extra={"tier": "pro"})
        assert self.signer.verify(token)["tier"] == "free"

    def test_rotation_accepts_old_key_and_signs_with_new(self):
        old = TokenSigner({"k1": b"secret-one-01234567890123456789"}, "k1")
        issued_before_rotation, _ = old.mint("subject-a")
        rotated = TokenSigner(
            {"k1": b"secret-one-01234567890123456789",
             "k2": b"secret-two-01234567890123456789"}, "k2")
        # Old token still valid during the overlap window …
        assert rotated.verify(issued_before_rotation)["sub"] == "subject-a"
        # … and new tokens carry the new kid.
        new_token, claims = rotated.mint("subject-a")
        assert claims["kid"] == "k2"
        assert rotated.verify(new_token)["kid"] == "k2"

    def test_retiring_key_invalidates_its_tokens(self):
        old = TokenSigner({"k1": b"secret-one-01234567890123456789"}, "k1")
        token, _ = old.mint("s")
        retired = TokenSigner({"k2": b"secret-two-01234567890123456789"}, "k2")
        with pytest.raises(TokenError):
            retired.verify(token)


# ── App Attest verification ──────────────────────────────────────────────────

class TestAppAttest:
    def test_garbage_attestation_rejected(self):
        with pytest.raises(appattest.AttestationError):
            appattest.verify_attestation(b"not-cbor", b"chal", b"k", TEST_APP_ID)

    def test_wrong_format_rejected(self):
        blob = cbor2.dumps({"fmt": "packed", "attStmt": {}, "authData": b"\x00" * 60})
        with pytest.raises(appattest.AttestationError, match="format"):
            appattest.verify_attestation(blob, b"chal", b"k", TEST_APP_ID)

    def test_missing_certificate_chain_rejected(self):
        blob = cbor2.dumps({"fmt": "apple-appattest", "attStmt": {},
                            "authData": b"\x00" * 60})
        with pytest.raises(appattest.AttestationError, match="missing required"):
            appattest.verify_attestation(blob, b"chal", b"k", TEST_APP_ID)

    def test_self_signed_chain_rejected(self):
        """A chain that parses but is not Apple's must fail.

        This is the check that makes attestation meaningful — without it an
        attacker could mint their own certificate and pass.
        """
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        import datetime as dt

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(days=1))
                .not_valid_after(now + dt.timedelta(days=30))
                .sign(key, hashes.SHA256()))
        der = cert.public_bytes(serialization.Encoding.DER)
        blob = cbor2.dumps({"fmt": "apple-appattest",
                            "attStmt": {"x5c": [der, der], "receipt": b""},
                            "authData": b"\x00" * 60})
        with pytest.raises(appattest.AttestationError, match="not signed by Apple"):
            appattest.verify_attestation(blob, b"chal", b"k", TEST_APP_ID)

    def test_assertion_replay_rejected(self):
        """Counter must strictly advance — this is the anti-replay control."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        import hashlib
        import struct

        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        rp = hashlib.sha256(TEST_APP_ID.encode()).digest()
        auth_data = rp + b"\x00" + struct.pack(">I", 5)
        challenge = b"challenge-value"
        digest = hashlib.sha256(auth_data + hashlib.sha256(challenge).digest()).digest()
        from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
        from cryptography.hazmat.primitives import hashes as _h
        sig = key.sign(digest, ec.ECDSA(Prehashed(_h.SHA256())))
        assertion = cbor2.dumps({"signature": sig, "authenticatorData": auth_data})

        # Fresh counter (5 > 4) is accepted …
        assert appattest.verify_assertion(assertion, challenge, pem, TEST_APP_ID, 4) == 5
        # … the identical assertion replayed at the same counter is not.
        with pytest.raises(appattest.AttestationError, match="counter did not advance"):
            appattest.verify_assertion(assertion, challenge, pem, TEST_APP_ID, 5)

    def test_assertion_for_other_app_rejected(self):
        import hashlib
        import struct
        auth_data = hashlib.sha256(b"OTHER.app").digest() + b"\x00" + struct.pack(">I", 1)
        assertion = cbor2.dumps({"signature": b"x", "authenticatorData": auth_data})
        with pytest.raises(appattest.AttestationError, match="different app"):
            appattest.verify_assertion(assertion, b"c", b"", TEST_APP_ID, 0)


# ── Challenge endpoint & single use ──────────────────────────────────────────

class TestChallenge:
    def test_issues_challenge(self):
        r = client.post("/auth/challenge")
        assert r.status_code == 200
        assert len(r.json()["challenge"]) >= 32

    def test_challenges_are_unique(self):
        a = client.post("/auth/challenge").json()["challenge"]
        b = client.post("/auth/challenge").json()["challenge"]
        assert a != b

    def test_unknown_challenge_rejected_on_attest(self):
        r = client.post("/auth/attest", json={
            "key_id": base64.b64encode(b"k").decode(),
            "attestation": base64.b64encode(b"a").decode(),
            "challenge": "never-issued",
        })
        assert r.status_code == 400

    def test_challenge_is_single_use(self):
        challenge = client.post("/auth/challenge").json()["challenge"]
        body = {"key_id": base64.b64encode(b"k").decode(),
                "attestation": base64.b64encode(b"not-valid").decode(),
                "challenge": challenge}
        first = client.post("/auth/attest", json=body)
        second = client.post("/auth/attest", json=body)
        # First consumes the nonce (and then fails on the bad attestation);
        # the second must fail specifically because the nonce is gone.
        assert first.status_code in (400, 401)
        assert second.status_code == 400


# ── Enforcement on protected routes ──────────────────────────────────────────

class TestEnforcement:
    def teardown_method(self):
        build_deps()

    def test_anonymous_request_allowed_when_not_enforcing(self):
        build_deps(enforce=False)
        assert _scan().status_code == 200

    def test_anonymous_request_rejected_when_enforcing(self):
        build_deps(enforce=True)
        r = _scan()
        assert r.status_code == 401
        assert r.headers.get("WWW-Authenticate") == "Bearer"

    def test_valid_token_accepted_when_enforcing(self):
        build_deps(enforce=True)
        assert auth.deps.signer is not None   # set by the build_deps/conftest fixture
        token, _ = auth.deps.signer.mint("subject-x")
        r = _scan(headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_expired_token_rejected(self):
        build_deps(enforce=True)
        assert auth.deps.signer is not None   # set by the build_deps/conftest fixture
        token, _ = auth.deps.signer.mint("subject-x", ttl=-3600)
        r = _scan(headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_tampered_token_rejected(self):
        build_deps(enforce=True)
        assert auth.deps.signer is not None   # set by the build_deps/conftest fixture
        token, _ = auth.deps.signer.mint("subject-x")
        payload, sig = token.split(".")
        r = _scan(headers={"Authorization": f"Bearer {payload}.{'A' * len(sig)}"})
        assert r.status_code == 401

    def test_listing_also_enforced(self):
        build_deps(enforce=True)
        r = client.post("/listing", json={
            "item_name": "Jacket", "price_low_usd": 10, "price_likely_usd": 20,
            "price_high_usd": 30, "marketplace": "ebay",
        })
        assert r.status_code == 401

    def test_error_does_not_leak_internals(self):
        build_deps(enforce=True)
        r = _scan(headers={"Authorization": "Bearer garbage"})
        assert r.status_code == 401
        assert "Traceback" not in r.text


# ── Free-scan quota ──────────────────────────────────────────────────────────

def test_exhausted_message_reads_correctly_at_every_limit():
    """The client echoes this string verbatim, so it must be grammatical.

    At limit=1 the old hardcoded plural read "You've used all 1 free scans
    today." — which is what production served once the free tier moved to one
    scan a day.
    """
    from quota import _exhausted_message
    assert _exhausted_message(1) == "You've used your free scan for today."
    assert _exhausted_message(3) == "You've used all 3 free scans today."
    assert "1 free scans" not in _exhausted_message(1)


def _quota(limit=3, device_check=None, cache=None):
    return ScanQuota(cache or ResilientCache(None, InMemoryCache()),
                     device_check, limit=limit)


class TestQuota:
    def test_allows_up_to_limit(self):
        q = _quota(3)

        async def run():
            for _ in range(3):
                await q.check("s", False)
                await q.consume("s", False)
            with pytest.raises(QuotaExceeded):
                await q.check("s", False)
        asyncio.run(run())

    def test_pro_is_unlimited(self):
        q = _quota(3)

        async def run():
            for _ in range(50):
                await q.check("s", True)
                await q.consume("s", True)
        asyncio.run(run())

    def test_subjects_are_independent(self):
        q = _quota(1)

        async def run():
            await q.consume("a", False)
            await q.check("b", False)          # must not raise
        asyncio.run(run())

    def test_concurrent_consumption_is_atomic(self):
        """Ten parallel scans must not all observe an unused counter."""
        q = _quota(3)

        async def run():
            await asyncio.gather(*(q.consume("s", False) for _ in range(10)))
            status = await q.status("s", False)
            assert status.used == 10           # every increment landed
        asyncio.run(run())

    def test_exceeded_carries_reset_time(self):
        q = _quota(1)

        async def run():
            await q.consume("s", False)
            with pytest.raises(QuotaExceeded) as exc:
                await q.check("s", False)
            assert exc.value.resets_at > time.time()
        asyncio.run(run())

    def test_fails_closed_when_durable_cache_is_down(self):
        """A configured-but-failing backend must not grant free scans."""
        class _Broken:
            async def get(self, *a, **k): raise ConnectionError("down")
            async def set(self, *a, **k): raise ConnectionError("down")
            async def add(self, *a, **k): raise ConnectionError("down")
            async def incr(self, *a, **k): raise ConnectionError("down")
            async def delete(self, *a, **k): raise ConnectionError("down")
            async def ping(self): raise ConnectionError("down")

        q = _quota(3, cache=ResilientCache(_Broken(), InMemoryCache()))
        with pytest.raises(QuotaUnavailable):
            asyncio.run(q.check("s", False))

    def test_quota_enforced_end_to_end(self):
        build_deps(enforce=True, free_scans=2)
        assert auth.deps.signer is not None   # set by the build_deps/conftest fixture
        token, _ = auth.deps.signer.mint("quota-subject")
        h = {"Authorization": f"Bearer {token}"}
        assert _scan(headers=h).status_code == 200
        assert _scan(headers=h).status_code == 200
        third = _scan(headers=h)
        assert third.status_code == 402
        assert "X-Quota-Resets-At" in third.headers
        build_deps()

    def test_failed_scan_does_not_consume_quota(self):
        build_deps(enforce=True, free_scans=1)
        assert auth.deps.signer is not None   # set by the build_deps/conftest fixture
        token, _ = auth.deps.signer.mint("nocharge-subject")
        h = {"Authorization": f"Bearer {token}"}
        # Upstream failure → 502, and the allowance must survive it.
        with patch("main._model") as m:
            m.generate_content_async = AsyncMock(side_effect=RuntimeError("boom"))
            failed = client.post("/scan", files={"file": ("s.jpg", io.BytesIO(VALID_JPEG),
                                 "image/jpeg")}, headers=h)
        assert failed.status_code == 502
        assert _scan(headers=h).status_code == 200      # allowance intact
        build_deps()


class _FakeDeviceCheck:
    """Stands in for Apple's DeviceCheck, which we cannot call from tests."""

    def __init__(self, bit0=False):
        self.is_configured = True
        self.bits = {"bit0": bit0, "bit1": False}
        self.updated = False

    async def query_bits(self, device_token):
        return dict(self.bits)

    async def update_bits(self, device_token, bit0, bit1):
        self.updated = True
        self.bits = {"bit0": bit0, "bit1": bit1}


class TestReinstallResistance:
    def test_fresh_device_gets_full_allowance(self):
        q = _quota(3, device_check=_FakeDeviceCheck(bit0=False))
        assert asyncio.run(q.starting_balance("brand-new-subject", "device-token")) == 3

    def test_reinstall_gets_no_fresh_allowance(self):
        """A new App Attest key id on hardware that already spent its scans."""
        q = _quota(3, device_check=_FakeDeviceCheck(bit0=True))

        async def run():
            balance = await q.starting_balance("new-subject-after-reinstall", "device-token")
            assert balance == 0
            # And the counter is pre-filled, so a scan is refused immediately.
            with pytest.raises(QuotaExceeded):
                await q.check("new-subject-after-reinstall", False)
        asyncio.run(run())

    def test_known_subject_is_not_rechecked(self):
        dc = _FakeDeviceCheck(bit0=True)
        q = _quota(3, device_check=dc)

        async def run():
            await q.starting_balance("subject-1", "device-token")   # first sight
            # Second call must not re-consult DeviceCheck or zero the balance.
            assert await q.starting_balance("subject-1", "device-token") == 3
        asyncio.run(run())

    def test_exhaustion_sets_device_bit(self):
        dc = _FakeDeviceCheck()
        q = _quota(1, device_check=dc)
        asyncio.run(q.note_exhausted("device-token"))
        assert dc.updated and dc.bits["bit0"] is True

    def test_devicecheck_outage_does_not_block_users(self):
        class _Broken(_FakeDeviceCheck):
            async def query_bits(self, device_token):
                raise RuntimeError("apple is down")

        q = _quota(3, device_check=_Broken())
        # Availability of Apple's API must not gate our own service.
        assert asyncio.run(q.starting_balance("s", "device-token")) == 3


# ── Cache: configured vs connected ───────────────────────────────────────────
# A durable backend that was *intended* but is unreachable is not the same as
# one that was never configured. Conflating them made every `required` call
# silently fall back to per-process memory — a fail-open quota.

class TestCacheFailurePolicy:
    def test_unconfigured_cache_serves_required_calls_from_memory(self):
        """Single-instance deployment: memory IS the source of truth."""
        cache = ResilientCache(None, InMemoryCache())
        assert not cache.is_configured

        async def run():
            await cache.set("k", "1", 60, required=True)
            assert await cache.get("k", required=True) == "1"
        asyncio.run(run())

    def test_configured_but_unconnected_cache_fails_closed(self):
        """Redis was configured and never connected — memory is NOT authoritative."""
        cache = ResilientCache(None, InMemoryCache(), configured=True)
        assert cache.is_configured
        assert cache.backend == "redis-unavailable"

        async def run():
            with pytest.raises(CacheUnavailable):
                await cache.incr("quota:x", 60, required=True)
        asyncio.run(run())

    def test_configured_but_unconnected_still_serves_optional_calls(self):
        """Non-required state (rate limits) may still degrade to memory."""
        cache = ResilientCache(None, InMemoryCache(), configured=True)
        asyncio.run(cache.set("k", "1", 60))
        assert asyncio.run(cache.get("k")) == "1"

    def test_quota_fails_closed_when_cache_configured_but_down(self):
        """The end-to-end consequence: no free scans are granted on faith."""
        cache = ResilientCache(None, InMemoryCache(), configured=True)
        q = _quota(3, cache=cache)
        with pytest.raises(QuotaUnavailable):
            asyncio.run(q.check("subject", False))

    def test_primary_implies_configured(self):
        cache = ResilientCache(InMemoryCache(), InMemoryCache())
        assert cache.is_configured

    def test_health_reports_configured_flag(self):
        unconfigured = ResilientCache(None, InMemoryCache())
        assert asyncio.run(unconfigured.health())["healthy"] is True
        broken = ResilientCache(None, InMemoryCache(), configured=True)
        health = asyncio.run(broken.health())
        assert health["healthy"] is False
        assert health["configured"] is True


# ── DeviceCheck reinstall defence is actually armed ──────────────────────────
# `note_exhausted` previously had no production caller, so `starting_balance`
# read a bit nothing ever set and reinstalling reset the free allowance forever.

class TestReinstallDefenceWiring:
    def test_quota_exhaustion_sets_the_device_bit(self):
        dc = _FakeDeviceCheck()
        cache = ResilientCache(None, InMemoryCache())
        build_deps()
        auth.deps.cache = cache
        auth.deps.quota = ScanQuota(cache, dc, limit=1)

        principal = auth.Principal(subject="subj", tier="free", authenticated=True,
                                   device_token="device-token")

        async def run():
            await auth.enforce_quota(principal)          # 1st: allowed
            await auth.deps.quota.consume("subj", False)
            with pytest.raises(Exception):               # 2nd: 402
                await auth.enforce_quota(principal)
        asyncio.run(run())
        assert dc.updated and dc.bits["bit0"] is True

    def test_device_token_recovered_from_cache_when_absent_on_principal(self):
        """Tokens are stored at attest time and looked up only on exhaustion,
        so the hot path pays nothing for this."""
        dc = _FakeDeviceCheck()
        cache = ResilientCache(None, InMemoryCache())
        build_deps()
        auth.deps.cache = cache
        auth.deps.quota = ScanQuota(cache, dc, limit=1)
        asyncio.run(cache.set(auth._device_token_key("subj"), "stored-token", 600))

        principal = auth.Principal(subject="subj", tier="free", authenticated=True)

        async def run():
            await auth.enforce_quota(principal)
            await auth.deps.quota.consume("subj", False)
            with pytest.raises(Exception):
                await auth.enforce_quota(principal)
        asyncio.run(run())
        assert dc.updated and dc.bits["bit0"] is True

    def test_pro_users_never_touch_devicecheck(self):
        dc = _FakeDeviceCheck()
        build_deps()
        auth.deps.quota = ScanQuota(ResilientCache(None, InMemoryCache()), dc, limit=1)
        principal = auth.Principal(subject="pro", tier="pro", authenticated=True)
        asyncio.run(auth.enforce_quota(principal))
        assert not dc.updated
