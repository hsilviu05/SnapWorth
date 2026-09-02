"""Server-side entitlement verification via StoreKit 2 signed transactions.

Closes SEC-02. Previously the backend had no concept of a paying user: the app
decided locally whether someone was subscribed, so the paywall was advisory.

StoreKit 2 hands the client a **JWS-signed transaction** that Apple produced.
The signature chains to Apple's Root CA, so the server can verify it *without*
calling Apple and without storing a mirror of every purchase — verification is
stateless, and the result is cached only as an optimisation.

That statelessness is why this needs no Postgres: the signed transaction is
itself the record, and the cache is disposable.

The server therefore keeps that transaction — the *proof* — alongside the
derived entitlement, and re-verifies it whenever the short entitlement entry
has lapsed. Without it the cache is not disposable at all: only the client can
repopulate it, at cold launch, so every expiry silently moved a paying
subscriber onto the free quota until they next relaunched the app. Losing the
proof store costs a re-POST, never someone's subscription.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import jwt
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import notify

log = logging.getLogger("snapworth.entitlements")

# Apple Root CA - G3. Public; the trust anchor for StoreKit signed transactions.
# https://www.apple.com/certificateauthority/
APPLE_ROOT_CA_G3_PEM = b"""-----BEGIN CERTIFICATE-----
MIICQzCCAcmgAwIBAgIILcX8iNLFS5UwCgYIKoZIzj0EAwMwZzEbMBkGA1UEAwwS
QXBwbGUgUm9vdCBDQSAtIEczMSYwJAYDVQQLDB1BcHBsZSBDZXJ0aWZpY2F0aW9u
IEF1dGhvcml0eTETMBEGA1UECgwKQXBwbGUgSW5jLjELMAkGA1UEBhMCVVMwHhcN
MTQwNDMwMTgxOTA2WhcNMzkwNDMwMTgxOTA2WjBnMRswGQYDVQQDDBJBcHBsZSBS
b290IENBIC0gRzMxJjAkBgNVBAsMHUFwcGxlIENlcnRpZmljYXRpb24gQXV0aG9y
aXR5MRMwEQYDVQQKDApBcHBsZSBJbmMuMQswCQYDVQQGEwJVUzB2MBAGByqGSM49
AgEGBSuBBAAiA2IABJjpLz1AcqTtkyJygRMc3RCV8cWjTnHcFBbZDuWmBSp3ZHtf
TjjTuxxEtX/1H7YyYl3J6YRbTzBPEVoA/VhYDKX1DyxNB0cTddqXl5dvMVztK517
IDvYuVTZXpmkOlEKMaNCMEAwHQYDVR0OBBYEFLuw3qFYM4iapIqZ3r6966/ayySr
MA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMAoGCCqGSM49BAMDA2gA
MGUCMQCD6cHEFl4aXTQY2e3v9GwOAEZLuN+yRhHFD/3meoyhpmvOwgPUnPWTxnS4
at+qIxUCMG1mihDK1A3UT82NQz60imOlM27jbdoXt2QfyFMm+YhidDkLF1vLUagM
6BgD56KyKA==
-----END CERTIFICATE-----"""

# Grace period after expiry during which we still honour a subscription, to
# absorb Apple's billing-retry window and clock skew. Being briefly generous is
# far cheaper than wrongly locking out a paying customer.
EXPIRY_GRACE_SECONDS = 3600

ENTITLEMENT_CACHE_TTL = 900          # 15 min — free, and the refund window

# A Pro entitlement has to outlive an ordinary gap between app launches.
# Only the client can refresh this cache: it POSTs /auth/entitlement from
# StoreKitPurchaseService.refreshSubscriptionStatus(), which runs at cold
# launch, purchase, restore and Transaction.updates — there is no foreground
# hook. iOS keeps apps suspended, so resuming one does not re-run init(), and
# at 15 minutes a paying subscriber read as `free` and was handed the free
# quota: one scan a day. Still capped by the subscription's own expiry below,
# and `Entitlement.is_active` re-checks expiry on every read.
PRO_ENTITLEMENT_CACHE_TTL = 86_400   # 24 h

# How long Apple's signed transaction itself is kept, so the server can rebuild
# an entitlement without waiting for the client to re-POST one.
#
# The TTL above only narrows the window in which a subscriber reads as `free`;
# it cannot close it, because nothing on the server could re-derive the
# entitlement once the entry lapsed. Holding the proof is what closes it. Long,
# because it is the subscription and not this constant that decides when the
# proof stops being worth anything — see `_store_proof`.
ENTITLEMENT_PROOF_TTL = 60 * 60 * 24 * 400

# Which StoreKit environments this deployment accepts.
#
# Sandbox transactions are signed by the *same* Apple chain as production ones,
# so every signature/bundle/product check below passes for a Sandbox JWS. Without
# this gate a free Sandbox tester account grants production Pro indefinitely.
#
# Comma-separated; defaults to Production only.
#
# OPERATIONAL NOTE: TestFlight builds receive *Sandbox* transactions. To exercise
# the purchase flow end-to-end from TestFlight, point that build at a staging
# deployment with ALLOWED_STOREKIT_ENVIRONMENTS="Sandbox" — do not widen
# production, or you reopen the bypass for everyone.
def _parse_environments(raw: str) -> frozenset[str]:
    values = {v.strip() for v in raw.split(",") if v.strip()}
    return frozenset(values or {"Production"})


ALLOWED_ENVIRONMENTS = _parse_environments(
    os.environ.get("ALLOWED_STOREKIT_ENVIRONMENTS", "Production"))

# How many distinct attested devices one subscription may entitle.
#
# The signed transaction is handed to the client in plaintext, so a single payer
# can share it. Apple's own Family Sharing tops out at six, which makes six the
# natural cap: invisible to every honest household, bounded for everyone else.
MAX_DEVICES_PER_SUBSCRIPTION = int(os.environ.get("MAX_DEVICES_PER_SUBSCRIPTION", "6"))

# Device-binding records outlive any single entitlement cache entry, otherwise
# the cap resets every 15 minutes and stops being a cap.
DEVICE_BINDING_TTL = 60 * 60 * 24 * 400

# A binding not seen for this long is not a device in use. It is almost always
# an install that was deleted: App Attest mints a *new* key on reinstall, so the
# old subject goes silent forever while still occupying a slot.
#
# This is what makes the cap survivable. Slots used to leak permanently — six
# reinstalls on a single phone exhausted a subscription's whole allowance for
# 400 days, and every later `/auth/entitlement` answered 409, which the client
# swallows. The subscriber then read as `free` and got one scan a day, on the
# subscription they were paying for, with no way back short of deleting the
# record by hand.
DEVICE_BINDING_IDLE_SECONDS = 60 * 60 * 24 * 30


class EntitlementError(Exception):
    """Signed transaction was missing, malformed, or failed verification."""


@dataclass(frozen=True)
class Entitlement:
    tier: str                        # "free" | "pro"
    product_id: str | None
    expires_at: int | None           # epoch seconds
    original_transaction_id: str | None
    environment: str                 # "Production" | "Sandbox"

    @property
    def is_active(self) -> bool:
        if self.tier != "pro":
            return False
        if self.expires_at is None:
            return True              # non-expiring purchase
        return self.expires_at + EXPIRY_GRACE_SECONDS > int(time.time())

    def to_json(self) -> str:
        return json.dumps({
            "tier": self.tier, "product_id": self.product_id,
            "expires_at": self.expires_at,
            "original_transaction_id": self.original_transaction_id,
            "environment": self.environment,
        })

    @staticmethod
    def from_json(raw: str) -> "Entitlement":
        d = json.loads(raw)
        return Entitlement(
            tier=d.get("tier", "free"), product_id=d.get("product_id"),
            expires_at=d.get("expires_at"),
            original_transaction_id=d.get("original_transaction_id"),
            environment=d.get("environment", "Production"),
        )


FREE = Entitlement("free", None, None, None, "Production")


def _decode_x5c_chain(header: dict) -> list[x509.Certificate]:
    chain = header.get("x5c") or []
    if len(chain) < 2:
        raise EntitlementError("Signed transaction certificate chain is incomplete.")
    try:
        return [x509.load_der_x509_certificate(base64.b64decode(c)) for c in chain]
    except Exception:
        raise EntitlementError("Signed transaction certificates could not be parsed.") from None


def _verify_chain(certs: list[x509.Certificate]) -> x509.Certificate:
    """Verify leaf → intermediate → Apple Root CA G3. Returns the leaf."""
    root = x509.load_pem_x509_certificate(APPLE_ROOT_CA_G3_PEM)
    now = datetime.now(timezone.utc)

    for cert in certs:
        if cert.not_valid_before_utc > now or cert.not_valid_after_utc < now:
            raise EntitlementError("Signed transaction certificate is not valid today.")

    # The last element should be Apple's root; compare against our pinned copy
    # rather than trusting whatever the client supplied.
    if (certs[-1].public_bytes(serialization.Encoding.DER)
            != root.public_bytes(serialization.Encoding.DER)):
        raise EntitlementError("Signed transaction is not rooted in Apple's CA.")

    # Every non-leaf certificate must actually be allowed to sign certificates.
    # Without this an attacker who obtains any Apple-chained *leaf* could use it
    # as an intermediate and mint their own transaction-signing certificate.
    # Root pinning above bounds the damage, but a permissive chain walk is a
    # latent flaw and cheap to close.
    for issuer in certs[1:]:
        _require_ca(issuer)

    for child, parent in zip(certs, certs[1:]):
        try:
            key = parent.public_key()
            if not isinstance(key, ec.EllipticCurvePublicKey):
                raise EntitlementError("Unexpected certificate key type.")
            # See the matching guard in appattest._verify_chain: None here is
            # a TypeError out of ec.ECDSA, which `except InvalidSignature`
            # would not catch, on a chain supplied by the caller.
            algorithm = child.signature_hash_algorithm
            if algorithm is None:
                raise EntitlementError(
                    "Certificate uses an unsupported signature algorithm.")
            key.verify(child.signature, child.tbs_certificate_bytes,
                       ec.ECDSA(algorithm))
        except InvalidSignature:
            raise EntitlementError("Signed transaction chain is not signed by Apple.") from None
    return certs[0]


def _require_ca(cert: x509.Certificate) -> None:
    """Assert a certificate is a CA permitted to sign other certificates."""
    try:
        constraints = cert.extensions.get_extension_for_class(
            x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        raise EntitlementError("Signed transaction chain is malformed.") from None
    if not constraints.ca:
        raise EntitlementError("Signed transaction chain is malformed.")

    # keyUsage is optional in X.509; when present it must permit cert signing.
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return
    if not usage.key_cert_sign:
        raise EntitlementError("Signed transaction chain is malformed.")


def verify_signed_transaction(
    jws_value: str,
    bundle_id: str,
    allowed_product_ids: set[str] | None = None,
    allowed_environments: frozenset[str] | None = None,
) -> Entitlement:
    """Verify a StoreKit 2 JWS and return the entitlement it proves."""
    if not jws_value or len(jws_value) > 16_384:
        raise EntitlementError("Missing or oversized signed transaction.")

    try:
        header = jwt.get_unverified_header(jws_value)
    except Exception:
        raise EntitlementError("Signed transaction header could not be read.") from None

    if header.get("alg") != "ES256":
        # Refuse alg confusion outright, including "none".
        raise EntitlementError("Unexpected signing algorithm.")

    certs = _decode_x5c_chain(header)
    leaf = _verify_chain(certs)

    # Narrowed before use, mirroring the check _verify_chain applies to the
    # parent keys. `public_key()` can return DSA/RSA/Ed25519 types that PyJWT
    # will not accept for ES256, and the leaf comes from the caller's chain.
    leaf_key = leaf.public_key()
    if not isinstance(leaf_key, ec.EllipticCurvePublicKey):
        raise EntitlementError("Unexpected certificate key type.")

    try:
        payload = jwt.decode(
            jws_value,
            key=leaf_key,
            algorithms=["ES256"],
            # Apple's transaction payloads carry no aud/iss; expiry is handled
            # below against the StoreKit-specific `expiresDate` field.
            options={"verify_aud": False, "verify_iss": False, "verify_exp": False},
        )
    except jwt.InvalidSignatureError:
        raise EntitlementError("Signed transaction signature is invalid.") from None
    except Exception:
        raise EntitlementError("Signed transaction could not be decoded.") from None

    if payload.get("bundleId") != bundle_id:
        raise EntitlementError("Signed transaction is for a different app.")

    # Environment gate. Must run before the entitlement is built: a Sandbox JWS
    # is cryptographically indistinguishable from a production one, so this is
    # the *only* thing separating a free tester account from paid Pro.
    environments = ALLOWED_ENVIRONMENTS if allowed_environments is None else allowed_environments
    environment = payload.get("environment", "Production")
    if environment not in environments:
        log.warning("rejected transaction from disallowed environment",
                    extra={"environment": environment,
                           "allowed": sorted(environments)})
        raise EntitlementError("Signed transaction is from the wrong environment.")

    product_id = payload.get("productId")
    if allowed_product_ids and product_id not in allowed_product_ids:
        raise EntitlementError("Signed transaction is for an unrecognised product.")

    # StoreKit timestamps are milliseconds.
    expires_ms = payload.get("expiresDate")
    expires_at = int(expires_ms / 1000) if isinstance(expires_ms, (int, float)) else None

    revoked = payload.get("revocationDate")
    if revoked:
        log.info("signed transaction was revoked", extra={"product_id": product_id})
        return FREE

    ent = Entitlement(
        tier="pro",
        product_id=product_id,
        expires_at=expires_at,
        original_transaction_id=payload.get("originalTransactionId"),
        environment=environment,
    )
    if not ent.is_active:
        log.info("signed transaction has expired", extra={"product_id": product_id})
        return FREE
    return ent


class EntitlementService:
    """Verifies signed transactions and caches the outcome per subject."""

    def __init__(self, cache, bundle_id: str, allowed_product_ids: set[str] | None = None,
                 max_devices: int = MAX_DEVICES_PER_SUBSCRIPTION) -> None:
        self._cache = cache
        self._bundle_id = bundle_id
        self._allowed = allowed_product_ids
        self._max_devices = max_devices

    @staticmethod
    def _key(subject: str) -> str:
        return f"ent:{subject}"

    @staticmethod
    def _device_key(original_transaction_id: str) -> str:
        return f"txn:{original_transaction_id}"

    @staticmethod
    def _proof_key(subject: str) -> str:
        return f"entproof:{subject}"

    @staticmethod
    def _cache_ttl(ent: Entitlement) -> int:
        """Lifetime of the short entitlement entry written for `ent`."""
        ttl = PRO_ENTITLEMENT_CACHE_TTL if ent.tier == "pro" else ENTITLEMENT_CACHE_TTL
        if ent.expires_at:
            # Never cache past the subscription's own expiry.
            ttl = max(60, min(ttl, ent.expires_at - int(time.time())))
        return ttl

    async def record(self, subject: str, jws_value: str,
                     device_id: str | None = None) -> Entitlement:
        """Verify, device-bind, and cache. Raises `EntitlementError` if invalid.

        `device_id` is the client's stable per-device identifier (Keychain-
        backed from iOS 1.3.4). When present, the subscription is bound to it
        rather than to `subject`, so reinstalls on one phone occupy one slot.
        """
        ent = verify_signed_transaction(jws_value, self._bundle_id, self._allowed)

        # Bind before caching. Binding no longer refuses anyone, so this is
        # ordering for its own sake rather than a gate: the record should
        # reflect this install before anything reads an entitlement for it.
        if ent.tier == "pro" and ent.original_transaction_id:
            await self._bind_device(subject, ent, device_id)

        await self._cache.set(self._key(subject), ent.to_json(), self._cache_ttl(ent))

        if ent.tier == "pro":
            await self._store_proof(subject, ent, jws_value)
        else:
            # Apple's latest word is that this subject is not entitled, so any
            # proof still held for it is void. Without this a refunded or
            # expired subscription would be resurrected by `_rederive` the
            # moment the short free entry lapsed — the proof carries the
            # revocation state it was signed with, not today's.
            await self._cache.delete(self._proof_key(subject))

        log.info("entitlement recorded",
                 extra={"tier": ent.tier, "product_id": ent.product_id})
        return ent

    async def _store_proof(self, subject: str, ent: Entitlement, jws_value: str) -> None:
        """Keep the signed transaction so `current()` can re-derive this later.

        The entitlement entry above is deliberately short-lived, and only the
        client can refresh it: it POSTs /auth/entitlement from
        `refreshSubscriptionStatus()`, which runs at cold launch, purchase,
        restore and `Transaction.updates`, with no foreground hook. Every lapse
        therefore used to drop a paying subscriber onto the free quota until
        they next relaunched. Holding the proof lets the server rebuild the
        entry on its own.

        What is stored is Apple's *signed* transaction, not the entitlement
        derived from it, so the cache never becomes the authority on who is
        Pro: a tampered or truncated proof fails verification on the way back
        out rather than granting anything.

        Best-effort. Refusing a verified purchase because this write failed
        would be a worse outcome than having nothing to re-derive from later.
        """
        ttl = ENTITLEMENT_PROOF_TTL
        if ent.expires_at:
            # Worthless past the subscription it proves, and `is_active` would
            # reject it anyway. Matches the device binding's horizon.
            ttl = max(3600, ent.expires_at + EXPIRY_GRACE_SECONDS - int(time.time()))
        try:
            await self._cache.set(self._proof_key(subject), jws_value, ttl)
        except Exception as exc:
            log.warning("entitlement proof write failed: %s", exc)

    @staticmethod
    def _decode_bindings(raw: str | None) -> dict[str, int]:
        """`{subject: last_seen}`, tolerating the original list-of-subjects form.

        Records written before last-seen tracking are bare JSON lists. They are
        read as "seen just now" rather than discarded: treating them as ancient
        would evict every real device the first time an install re-records.
        """
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except Exception:
            return {}
        now = int(time.time())
        if isinstance(decoded, list):
            return {str(s): now for s in decoded}
        if isinstance(decoded, dict):
            return {str(s): int(t) for s, t in decoded.items()
                    if isinstance(t, (int, float))}
        return {}

    async def _bind_device(self, subject: str, ent: Entitlement,
                           device_id: str | None = None) -> None:
        """Associate this device with the subscription, bounding how many share it.

        The signed transaction reaches the client in plaintext, so one payer can
        hand it to arbitrarily many installs. Each install attests under its own
        App Attest key, so without this every recipient becomes Pro.

        **This bounds concurrently-bound devices; it does not refuse anyone.**
        It used to refuse, and that was a lockout with no way out: an App Attest
        key is per *install*, not per device, so reinstalling minted a new
        subject and consumed another slot while the old one sat there for 400
        days. Six reinstalls on one phone exhausted the subscription, every
        later `/auth/entitlement` answered 409, the client swallowed it, and a
        paying subscriber silently got the free tier's one scan a day.

        The identity bound is `device_id` when the client sends one — a
        Keychain-backed value that outlives app deletion, from iOS 1.3.4 — and
        falls back to `subject` for older clients. With a stable identity a
        reinstall lands on the slot it already holds, so the record counts
        phones, and eviction churn means what it looks like: more phones than
        the cap on one subscription. Under subjects alone it could not be told
        apart from one phone reinstalled repeatedly.

        Eviction is still the least-recently-seen binding rather than a refusal.
        Even with a stable id, refusing means a seventh device — or a phone
        whose Keychain was wiped — locks a payer out, and locking out a payer
        remains far the worse failure: they have paid, and support cannot fix it
        without deleting the record by hand. What a genuinely shared
        subscription gets instead is a visible signal: steady churn, reported.

        A cache failure here is not fatal: refusing a legitimate paying customer
        because Redis blinked is a worse outcome than briefly permitting an extra
        device. The cap is anti-abuse, not an authorisation boundary.
        """
        identity = device_id or subject
        key = self._device_key(ent.original_transaction_id or "")
        try:
            bindings = self._decode_bindings(await self._cache.get(key))
        except Exception as exc:
            log.warning("device binding read failed, allowing: %s", exc)
            return

        now = int(time.time())
        # Drop what has gone quiet for a month: almost always a deleted install
        # whose key will never be presented again.
        bindings = {s: t for s, t in bindings.items()
                    if now - t < DEVICE_BINDING_IDLE_SECONDS}

        if identity not in bindings and len(bindings) >= self._max_devices:
            evicted = min(bindings, key=lambda s: bindings[s])
            idle_seconds = now - bindings.pop(evicted)
            # Worth seeing: on a genuinely shared subscription this is steady
            # churn, which is the signal the cap exists to surface. A short idle
            # time means the evicted device is still in use — real sharing —
            # while a long one is just a device that was replaced.
            log.info("device binding evicted to make room", extra={
                "devices": self._max_devices, "max": self._max_devices,
                "idle_seconds": idle_seconds})
            notify.subscription_over_cap(
                ent.original_transaction_id or "", ent.product_id,
                idle_seconds=idle_seconds, max_devices=self._max_devices)

        # Rewritten on every record, so a device in active use keeps its slot
        # and only genuinely dormant ones age out.
        bindings[identity] = now

        # TTL tracks the subscription, not the short entitlement cache, so the
        # cap survives far longer than one 15-minute entitlement window.
        ttl = DEVICE_BINDING_TTL
        if ent.expires_at:
            ttl = max(3600, ent.expires_at + EXPIRY_GRACE_SECONDS - int(time.time()))
        try:
            await self._cache.set(key, json.dumps(bindings, sort_keys=True), ttl)
        except Exception as exc:
            log.warning("device binding write failed: %s", exc)

    async def current(self, subject: str) -> Entitlement:
        """Best-known entitlement for a subject; defaults to free.

        A miss falls through to the stored proof rather than straight to FREE,
        which costs a second cache read on the free path and buys a subscriber
        their Pro access back without a cold launch.
        """
        raw = await self._cache.get(self._key(subject))
        if raw:
            try:
                ent = Entitlement.from_json(raw)
            except Exception:
                ent = None
            if ent is not None and ent.is_active:
                return ent
        return await self._rederive(subject)

    async def _rederive(self, subject: str) -> Entitlement:
        """Rebuild an entitlement from the stored proof, re-verifying it.

        This is the difference between a lapsed cache entry costing a
        subscriber their Pro access until the next cold launch, and costing
        them one signature check. FREE when there is no proof, when it no
        longer verifies, or when the subscription it proves has ended.
        """
        try:
            jws_value = await self._cache.get(self._proof_key(subject))
        except Exception as exc:
            log.warning("entitlement proof read failed: %s", exc)
            return FREE
        if not jws_value:
            return FREE

        try:
            ent = verify_signed_transaction(jws_value, self._bundle_id, self._allowed)
        except EntitlementError as exc:
            # Covers a tampered proof, but also an operator narrowing
            # ALLOWED_STOREKIT_ENVIRONMENTS or the product list under a proof
            # that predates the change: re-verification applies today's rules.
            log.warning("stored entitlement proof no longer verifies: %s", exc)
            return FREE
        if not ent.is_active:
            return FREE

        # Deliberately not re-bound to the device: this subject was bound when
        # the proof was recorded, and re-binding on every cache miss would churn
        # the last-seen timestamps from the scan path rather than from the
        # entitlement sync that actually represents an install checking in.
        try:
            await self._cache.set(
                self._key(subject), ent.to_json(), self._cache_ttl(ent))
        except Exception as exc:
            log.warning("entitlement re-cache failed: %s", exc)

        log.info("entitlement re-derived from stored proof",
                 extra={"tier": ent.tier, "product_id": ent.product_id})
        return ent

    async def clear(self, subject: str) -> None:
        await self._cache.delete(self._key(subject))
        # The proof too, or `current()` re-derives Pro straight back and this
        # stops being a revocation.
        await self._cache.delete(self._proof_key(subject))
