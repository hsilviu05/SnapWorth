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
from cache import CacheUnavailable

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


def _is_legacy_subject(identity: str) -> bool:
    """True for a binding keyed by App Attest key id rather than device id.

    Subjects are the hex of a 32-byte key id; device ids are UUID strings.
    The two never collide, which is what lets the sharing alert tell a
    pre-1.3.4 ghost apart from a phone.
    """
    return len(identity) == 64 and all(c in "0123456789abcdef" for c in identity)


class EntitlementError(Exception):
    """Signed transaction was missing, malformed, or failed verification."""


class EntitlementsUnavailable(Exception):
    """The durable store could not be reached, so entitlement is *unknown*.

    Distinct from a genuine miss, which means "this subject is free". Reading
    an outage as a miss downgraded every paying subscriber for its duration.
    """


@dataclass(frozen=True)
class Entitlement:
    tier: str                        # "free" | "pro"
    product_id: str | None
    expires_at: int | None           # epoch seconds
    original_transaction_id: str | None
    environment: str                 # "Production" | "Sandbox"
    # When the subscription was first bought (epoch seconds), from Apple's
    # originalPurchaseDate. Renewals keep the original date, so this is what
    # separates a new customer from an existing one checking in.
    original_purchase_at: int | None = None
    # How the subscription was obtained, from Apple's offerType (1 introductory,
    # 2 promotional, 3 offer code; absent for a plain purchase or renewal) and
    # offerDiscountType ("FREE_TRIAL", "PAY_AS_YOU_GO", "PAY_UP_FRONT"). This is
    # what tells a comped user from a paying one in the operator's view.
    offer_type: int | None = None
    offer_discount_type: str | None = None
    # What Apple charged for this transaction, in currency units (the JWS
    # carries milliunits). Absent on older transactions.
    price: float | None = None
    currency: str | None = None
    # When Apple revoked this transaction (refund, family-sharing removal), from
    # revocationDate. Only ever populated on the reporting path — the access
    # path turns a revoked transaction into `FREE` before it gets this far.
    revoked_at: int | None = None

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
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
            "original_purchase_at": self.original_purchase_at,
            "offer_type": self.offer_type,
            "offer_discount_type": self.offer_discount_type,
            "price": self.price, "currency": self.currency,
            "revoked_at": self.revoked_at,
        })

    @staticmethod
    def from_json(raw: str) -> "Entitlement":
        d = json.loads(raw)
        return Entitlement(
            tier=d.get("tier", "free"), product_id=d.get("product_id"),
            expires_at=d.get("expires_at"),
            original_transaction_id=d.get("original_transaction_id"),
            environment=d.get("environment", "Production"),
            original_purchase_at=d.get("original_purchase_at"),
            offer_type=d.get("offer_type"),
            offer_discount_type=d.get("offer_discount_type"),
            price=d.get("price"), currency=d.get("currency"),
            revoked_at=d.get("revoked_at"),
        )


FREE = Entitlement("free", None, None, None, "Production")


#: Apple sends leaf, intermediate, root — exactly three, which is what Apple's
#: own `app-store-server-library` requires (`if len(certificates) != 3`). The
#: bound is also what stops an unauthenticated caller choosing how much work
#: the server does.
X5C_CHAIN_LENGTH = 3

#: The two extension OIDs Apple's own verifier requires, and the reason a
#: chain-to-the-pinned-root check is not sufficient on its own.
#:
#: Apple Root CA G3 is not a StoreKit-only root. It anchors several CA branches
#: that issue P-256 leaves to ordinary enrolled developers from a CSR — an
#: Apple Pay payment-processing certificate is the clearest example, where the
#: developer generates and keeps the private key. Without a purpose check, any
#: such leaf could sign a transaction payload that this module would accept as
#: Apple's, because every other property held: it chains to the pinned root
#: through CA-flagged intermediates and its key is an EC key.
#:
#: Verified against
#: apple/app-store-server-library-python `signed_data_verifier.py`, which
#: checks the same two OIDs on `trusted_chain[0]` and `trusted_chain[1]`.
LEAF_PURPOSE_OID = "1.2.840.113635.100.6.11.1"
INTERMEDIATE_PURPOSE_OID = "1.2.840.113635.100.6.2.1"


def _decode_x5c_chain(header: dict) -> list[x509.Certificate]:
    chain = header.get("x5c") or []
    # Exactly three, checked *before* any base64 or DER parsing.
    #
    # `_verify_chain` walks the whole caller-supplied list: a validity check on
    # every certificate, a CA check on every non-leaf, and one ECDSA
    # verification per adjacent pair. The only early abort was the root pin, so
    # putting the genuine Apple Root CA G3 last bought an attacker the entire
    # walk over as many certificates as they cared to send — on
    # `/apple/notifications`, which is unauthenticated by necessity. That is a
    # CPU amplifier, not a signature check.
    if len(chain) != X5C_CHAIN_LENGTH:
        raise EntitlementError(
            "Signed transaction certificate chain is not Apple's three.")
    try:
        return [x509.load_der_x509_certificate(base64.b64decode(c)) for c in chain]
    except Exception:
        raise EntitlementError("Signed transaction certificates could not be parsed.") from None


def _verify_chain(certs: list[x509.Certificate]) -> x509.Certificate:
    """Verify leaf → intermediate → Apple Root CA G3. Returns the leaf."""
    root = x509.load_pem_x509_certificate(APPLE_ROOT_CA_G3_PEM)
    now = datetime.now(timezone.utc)

    # The root pin runs first: it is one comparison and it rejects every chain
    # that was not built on Apple's CA, so nothing below is spent on one that
    # was never going to verify.
    if (certs[-1].public_bytes(serialization.Encoding.DER)
            != root.public_bytes(serialization.Encoding.DER)):
        raise EntitlementError("Signed transaction is not rooted in Apple's CA.")

    for cert in certs:
        if cert.not_valid_before_utc > now or cert.not_valid_after_utc < now:
            raise EntitlementError("Signed transaction certificate is not valid today.")

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

    # What the certificates are *for*, which the walk above does not establish.
    _require_purpose(certs[0], LEAF_PURPOSE_OID, "leaf")
    _require_purpose(certs[1], INTERMEDIATE_PURPOSE_OID, "intermediate")
    return certs[0]


def _require_purpose(cert: x509.Certificate, oid: str, what: str) -> None:
    """Assert a certificate carries the extension marking it for this job.

    Chaining to the pinned root proves who issued the certificate, not what it
    was issued *for*. See `LEAF_PURPOSE_OID`.
    """
    try:
        cert.extensions.get_extension_for_oid(x509.ObjectIdentifier(oid))
    except x509.ExtensionNotFound:
        raise EntitlementError(
            f"Signed transaction {what} is not an App Store signing "
            f"certificate.") from None


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


def verify_apple_jws(jws_value: str, *, max_length: int = 16_384) -> dict:
    """Verify an Apple-signed JWS against the pinned root and return its payload.

    Every JWS Apple hands us — a StoreKit signed transaction, and the
    `signedPayload` of an App Store Server Notification and the transaction
    nested inside it — is ES256 with the signing chain in the `x5c` header, so
    this is the one place that decides whether something really came from
    Apple. It is deliberately shared rather than copied: a second
    implementation is a second thing to get wrong, and this one is the only
    reason the server can trust a purchase it never saw a device make.

    Returns the decoded payload. Says nothing about what the payload *means* —
    bundle, environment and product are the caller's to check.
    """
    if not jws_value or len(jws_value) > max_length:
        raise EntitlementError("Missing or oversized signed payload.")

    try:
        header = jwt.get_unverified_header(jws_value)
    except Exception:
        raise EntitlementError("Signed payload header could not be read.") from None

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
        return jwt.decode(
            jws_value,
            key=leaf_key,
            algorithms=["ES256"],
            # Apple's payloads carry no aud/iss; subscription expiry is handled
            # by the caller against StoreKit's own `expiresDate` field.
            options={"verify_aud": False, "verify_iss": False, "verify_exp": False},
        )
    except jwt.InvalidSignatureError:
        raise EntitlementError("Signed payload signature is invalid.") from None
    except Exception:
        raise EntitlementError("Signed payload could not be decoded.") from None


def verify_signed_transaction(
    jws_value: str,
    bundle_id: str,
    allowed_product_ids: set[str] | None = None,
    allowed_environments: frozenset[str] | None = None,
    *,
    allow_inactive: bool = False,
) -> Entitlement:
    """Verify a StoreKit 2 JWS and return the entitlement it proves.

    `allow_inactive` returns what the transaction *says* even when it is
    expired or revoked, instead of collapsing to `FREE`. It exists for the
    App Store Server Notification path, which has to be able to record a
    subscription ending — a churn it reported as `FREE` would be
    indistinguishable from one it never heard about. It must never be set on a
    path that grants access: `FREE` is what keeps an expired transaction from
    being Pro, and `Entitlement.is_active` is the check that replaces it.
    """
    payload = verify_apple_jws(jws_value)

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
    purchased_ms = payload.get("originalPurchaseDate")
    original_purchase_at = (int(purchased_ms / 1000)
                            if isinstance(purchased_ms, (int, float)) else None)

    offer_type = payload.get("offerType")
    offer_type = int(offer_type) if isinstance(offer_type, (int, float)) else None
    offer_discount_type = payload.get("offerDiscountType")
    if not isinstance(offer_discount_type, str):
        offer_discount_type = None
    price_milli = payload.get("price")
    price = round(price_milli / 1000, 2) if isinstance(price_milli, (int, float)) else None
    currency = payload.get("currency") if isinstance(payload.get("currency"), str) else None

    revoked_ms = payload.get("revocationDate")
    revoked_at = (int(revoked_ms / 1000)
                  if isinstance(revoked_ms, (int, float)) else None)

    ent = Entitlement(
        tier="pro",
        product_id=product_id,
        expires_at=expires_at,
        original_transaction_id=payload.get("originalTransactionId"),
        environment=environment,
        original_purchase_at=original_purchase_at,
        offer_type=offer_type,
        offer_discount_type=offer_discount_type,
        price=price,
        currency=currency,
        revoked_at=revoked_at,
    )
    if allow_inactive:
        return ent
    if revoked_at is not None:
        log.info("signed transaction was revoked", extra={"product_id": product_id})
        return FREE
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
    def _revoked_key(original_transaction_id: str) -> str:
        return f"entrevoked:{original_transaction_id}"

    async def revoke(self, ent: Entitlement, revoked_at: int | None = None) -> bool:
        """Record that Apple has taken a subscription back.

        A stored proof is a *snapshot*: it carries the revocation state it was
        signed with, and Apple never rewrites it. A refund does not touch
        `expiresDate`, so the pre-refund JWS keeps verifying and keeps saying
        active — and `_rederive` re-cached Pro from it on every miss, for the
        rest of the paid term. A yearly refunded on day three bought unlimited
        scans until the following September.

        The three things that looked like they prevented this all missed. The
        proof-deleting branch in `record` only runs if the *client* presents a
        revoked transaction, and the client never does: it sets `activeJWS`
        only when `revocationDate == nil`, and `currentEntitlements` omits
        revoked transactions entirely. `clear()` had no production caller at
        all. And the notification handler verified the REFUND, sent the
        operator a Telegram message, and touched no entitlement state — while
        with a proof store in place, not revoking *is* granting.

        So the revocation has to live on the access path, keyed by something a
        notification actually carries. A notification has no App Attest
        subject — `notify` only ever stores a one-way pseudonym — so the
        subject's proof key cannot be found from here. `originalTransactionId`
        can, and that is what this is keyed on.

        The tombstone stores the revoked term's own expiry, not just the
        revocation date, because `originalTransactionId` is stable across
        renewals *and* re-subscriptions. Keyed on the id alone, this would
        permanently deny someone who later paid again. A later term always
        expires later, so comparing expiries lets the tombstone kill exactly
        the term that was taken back.
        """
        otid = ent.original_transaction_id
        if not otid:
            # Nothing to key on, and nothing a retry would fix.
            log.warning("a revocation notification carried no "
                        "original transaction id")
            return False
        payload = json.dumps({
            "revoked_at": revoked_at or ent.revoked_at or int(time.time()),
            # None for a non-expiring purchase, which is then treated as fully
            # revoked — there is no later term it could be confused with.
            "expires_at": ent.expires_at,
        })
        # Exceptions propagate. A tombstone that was not written is a
        # subscriber who keeps paid access they were refunded for, so the
        # caller has to know — this is the one write in the entitlement path
        # that must not be best-effort.
        #
        # At least as long as any proof it has to outlive: a proof's TTL is
        # capped at its own expiry plus grace, so this covers every one.
        await self._cache.set(self._revoked_key(otid), payload,
                              ENTITLEMENT_PROOF_TTL)
        log.info("subscription revoked by Apple",
                 extra={"product_id": ent.product_id})
        return True

    async def _is_revoked(self, ent: Entitlement) -> bool:
        """Whether Apple has since taken back the term this entitlement covers.

        Consulted after verification, because the JWS can never carry a
        revocation that post-dates its own signature.
        """
        otid = ent.original_transaction_id
        if not otid:
            return False
        try:
            raw = await self._cache.get(self._revoked_key(otid))
        except Exception as exc:
            # Fail open, loudly. Both callers have already read the proof or
            # the entry from this same cache, so a failure on this one key is
            # not an outage — and refusing Pro here would drop paying
            # subscribers on a transient error. The invariant is re-checked on
            # the next request, which is a bounded window; the alternative is
            # an unbounded one for everyone.
            log.warning("revocation tombstone read failed: %s", exc)
            return False
        if not raw:
            return False
        try:
            tombstone = json.loads(raw)
        except Exception:
            # Unparseable, but present. A tombstone is a tombstone.
            return True
        revoked_expiry = tombstone.get("expires_at")
        if ent.expires_at is None or revoked_expiry is None:
            return True
        # A re-subscription reuses the original transaction id and extends the
        # expiry, so only a term ending at or before the revoked one is dead.
        return ent.expires_at <= revoked_expiry

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

        if ent.tier == "pro" and await self._is_revoked(ent):
            # Apple has taken this term back since the transaction was signed,
            # so the signature proves nothing about entitlement any more.
            log.info("refused an entitlement Apple has revoked",
                     extra={"product_id": ent.product_id})
            await self._cache.set(
                self._key(subject), FREE.to_json(), self._cache_ttl(FREE))
            await self._cache.delete(self._proof_key(subject))
            return FREE

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
            # Only a *device* being pushed out is a sharing signal. A record
            # written before iOS 1.3.4 holds one per-install attest subject
            # per reinstall — six of them for one phone that was reinstalled
            # six times — and the first launch with a stable device id
            # evicts one of those ghosts. That is the migration doing its
            # job, not a seventh phone, and reporting it as sharing would
            # teach the operator to ignore the alert that matters.
            if not _is_legacy_subject(evicted):
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

        Raises `EntitlementsUnavailable` when the durable cache is unreachable.
        This read used to be unqualified, so `ResilientCache` returned `None`
        for an outage exactly as it does for a genuine miss — and every Pro
        subscriber silently read as free for the duration, with `/listing`
        402ing people who had paid. "No record" and "no answer" are different
        facts and the caller has to be able to tell them apart.
        """
        try:
            raw = await self._cache.get(self._key(subject), required=True)
        except CacheUnavailable as exc:
            raise EntitlementsUnavailable(str(exc)) from exc
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
            jws_value = await self._cache.get(self._proof_key(subject), required=True)
        except CacheUnavailable as exc:
            raise EntitlementsUnavailable(str(exc)) from exc
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

        if await self._is_revoked(ent):
            # The loop this breaks: the 24h Pro entry lapses, `current()` falls
            # through to here, the pre-refund JWS re-verifies and still reads
            # active because a refund does not move `expiresDate`, and Pro is
            # re-cached for another 24h. Forever, with no client involvement.
            log.info("entitlement proof discarded: Apple revoked it",
                     extra={"product_id": ent.product_id})
            try:
                await self._cache.delete(self._proof_key(subject))
            except Exception as exc:
                log.warning("could not delete a revoked proof: %s", exc)
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
