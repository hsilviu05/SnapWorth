"""Server-side entitlement verification via StoreKit 2 signed transactions.

Closes SEC-02. Previously the backend had no concept of a paying user: the app
decided locally whether someone was subscribed, so the paywall was advisory.

StoreKit 2 hands the client a **JWS-signed transaction** that Apple produced.
The signature chains to Apple's Root CA, so the server can verify it *without*
calling Apple and without storing a mirror of every purchase — verification is
stateless, and the result is cached only as an optimisation.

That statelessness is why this needs no Postgres: the signed transaction is
itself the record, and the cache is disposable.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import jwt
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec

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

ENTITLEMENT_CACHE_TTL = 900          # 15 min


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
    if certs[-1].public_bytes(x509.Encoding.DER) != root.public_bytes(x509.Encoding.DER):
        raise EntitlementError("Signed transaction is not rooted in Apple's CA.")

    for child, parent in zip(certs, certs[1:]):
        try:
            key = parent.public_key()
            if not isinstance(key, ec.EllipticCurvePublicKey):
                raise EntitlementError("Unexpected certificate key type.")
            key.verify(child.signature, child.tbs_certificate_bytes,
                       ec.ECDSA(child.signature_hash_algorithm))
        except InvalidSignature:
            raise EntitlementError("Signed transaction chain is not signed by Apple.") from None
    return certs[0]


def verify_signed_transaction(
    jws_value: str,
    bundle_id: str,
    allowed_product_ids: set[str] | None = None,
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

    try:
        payload = jwt.decode(
            jws_value,
            key=leaf.public_key(),
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
        environment=payload.get("environment", "Production"),
    )
    if not ent.is_active:
        log.info("signed transaction has expired", extra={"product_id": product_id})
        return FREE
    return ent


class EntitlementService:
    """Verifies signed transactions and caches the outcome per subject."""

    def __init__(self, cache, bundle_id: str, allowed_product_ids: set[str] | None = None) -> None:
        self._cache = cache
        self._bundle_id = bundle_id
        self._allowed = allowed_product_ids

    @staticmethod
    def _key(subject: str) -> str:
        return f"ent:{subject}"

    async def record(self, subject: str, jws_value: str) -> Entitlement:
        """Verify and cache. Raises `EntitlementError` if the JWS is bad."""
        ent = verify_signed_transaction(jws_value, self._bundle_id, self._allowed)
        ttl = ENTITLEMENT_CACHE_TTL
        if ent.expires_at:
            # Never cache past the subscription's own expiry.
            ttl = max(60, min(ttl, ent.expires_at - int(time.time())))
        await self._cache.set(self._key(subject), ent.to_json(), ttl)
        log.info("entitlement recorded",
                 extra={"tier": ent.tier, "product_id": ent.product_id})
        return ent

    async def current(self, subject: str) -> Entitlement:
        """Best-known entitlement for a subject; defaults to free."""
        raw = await self._cache.get(self._key(subject))
        if not raw:
            return FREE
        try:
            ent = Entitlement.from_json(raw)
        except Exception:
            return FREE
        return ent if ent.is_active else FREE

    async def clear(self, subject: str) -> None:
        await self._cache.delete(self._key(subject))
