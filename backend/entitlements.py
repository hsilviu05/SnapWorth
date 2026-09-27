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
from dataclasses import dataclass, replace
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

# Which StoreKit environments this deployment trusts *fully*: Pro for as long
# as the subscription runs, a stored proof to re-derive it from, six devices,
# and a place in the operator's revenue view.
#
# Sandbox transactions are signed by the *same* Apple chain as production ones,
# so every signature/bundle/product check below passes for a Sandbox JWS. Listed
# here, a free Sandbox tester account would grant production Pro indefinitely.
#
# Comma-separated; defaults to Production only, and production keeps it that
# way. That no longer shuts Sandbox out: `SANDBOX_ENTITLEMENTS` below is the
# separate, bounded treatment it gets instead. Listing Sandbox *here* is for a
# staging deployment that wants TestFlight treated exactly like a customer —
# never production, where it would hand every tester the unbounded version.
def _parse_environments(raw: str) -> frozenset[str]:
    values = {v.strip() for v in raw.split(",") if v.strip()}
    return frozenset(values or {"Production"})


ALLOWED_ENVIRONMENTS = _parse_environments(
    os.environ.get("ALLOWED_STOREKIT_ENVIRONMENTS", "Production"))

SANDBOX_ENVIRONMENT = "Sandbox"

# How a Sandbox transaction is treated when ALLOWED_ENVIRONMENTS does not trust
# it fully. `bounded` (the default) or `off`.
#
# App Review buys in Sandbox, and so does every TestFlight build — against the
# one backend the app has (Config.swift). With Production the only environment
# accepted, a reviewer who bought Pro got a 400 here, then the paywall again,
# a 402 on /listing and an empty "Why this price": Guideline 2.1 / 3.1.1,
# "purchased content not delivered" (AUDIT-2026-09-26). It had not bitten only
# because no reviewer had bought.
#
# `bounded` honours Sandbox on terms that make a tester's transaction worth
# very little to anyone but the tester:
#
#   * only for a caller holding an App Attest-backed token — never the legacy
#     unauthenticated path, whose subject is a header the caller chose;
#   * for at most SANDBOX_ENTITLEMENT_TTL, and never past the transaction's own
#     expiry plus the grace every entitlement gets;
#   * with no stored proof, so nothing re-derives it once that entry lapses —
#     the device has to present a live transaction again;
#   * on one device per originalTransactionId at a time. The most recent device
#     to present it takes it over, and the one it displaces reads as free from
#     its next request. Replacing rather than refusing, because a reviewer
#     moving from an iPhone to an iPad on one Sandbox account is exactly the
#     case this exists for, and refusing would recreate the rejection;
#   * outside every operator figure: no subscriber-index row, no new-sub count,
#     no "New Pro" or trial alert, no referral reward (`is_bounded`).
#
# `off` is the old behaviour: a Sandbox transaction is refused with a 400, and
# any Sandbox entitlement already cached reads as free from its next request.
# Anything else is read as `off`, loudly — a typo must not be what widens
# access.
SANDBOX_BOUNDED = "bounded"
SANDBOX_OFF = "off"


def _parse_sandbox_mode(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value in ("", SANDBOX_BOUNDED):
        return SANDBOX_BOUNDED
    if value != SANDBOX_OFF:
        log.warning("SANDBOX_ENTITLEMENTS=%r is neither %r nor %r; treating it as %r",
                    raw, SANDBOX_BOUNDED, SANDBOX_OFF, SANDBOX_OFF)
    return SANDBOX_OFF


SANDBOX_ENTITLEMENTS = _parse_sandbox_mode(os.environ.get("SANDBOX_ENTITLEMENTS"))

# The longest a bounded Sandbox entitlement lives without the device presenting
# its transaction again. The same day a Pro entry gets, for the same reason —
# only the client can refresh it, at cold launch, purchase, restore and
# `Transaction.updates` — and it is the ceiling, not the usual case: Sandbox
# renews a monthly plan every few minutes, so the transaction's own expiry is
# almost always the shorter of the two.
SANDBOX_ENTITLEMENT_TTL = 86_400

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
    An *honest* client's two never collide, which is what lets the sharing
    alert tell a pre-1.3.4 ghost apart from a phone. A dishonest one chooses
    its own `device_id`, so `_bind_device` prefixes any device id of this shape
    with `dev:` before storing it — the classification cannot be inferred from
    a string the caller controls.
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
    # Apple's offerIdentifier: for an offer code, the offer's *reference name*
    # from App Store Connect, never the individual code redeemed. That is why
    # referrals attribute by an in-app claim rather than by code (referral.py).
    offer_identifier: str | None = None
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
            "offer_identifier": self.offer_identifier,
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
            offer_identifier=d.get("offer_identifier"),
            price=d.get("price"), currency=d.get("currency"),
            revoked_at=d.get("revoked_at"),
        )


FREE = Entitlement("free", None, None, None, "Production")


def is_bounded(ent: Entitlement) -> bool:
    """Whether `ent` is a Sandbox entitlement held on the bounded terms.

    The question every consumer outside this module needs answered is "is this
    a customer?", and for a bounded entitlement the answer is no: it is Pro for
    the device holding it and nothing else. The operator's index, counts and
    alerts and the referral reward all skip it.

    Keyed on trust rather than on the word "Sandbox", so a staging deployment
    that lists Sandbox in ALLOWED_STOREKIT_ENVIRONMENTS sees its testers exactly
    as it always has. Covers the inactive `FREE` a bounded transaction
    collapses to as well — see `verify_signed_transaction` — which is what
    keeps an expired tester transaction from reading as a churned customer.
    """
    return (getattr(ent, "environment", None) == SANDBOX_ENVIRONMENT
            and SANDBOX_ENVIRONMENT not in ALLOWED_ENVIRONMENTS)


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


def entitlement_from_payload(payload: dict, *, environment: str) -> Entitlement:
    """Build an `Entitlement` from a decoded StoreKit transaction payload.

    Separated from `verify_signed_transaction` because there are now two ways
    to arrive at a verified payload: this module's own `verify_apple_jws`, and
    Apple's `SignedDataVerifier` in `appstorestatus.py`, which the App Store
    Server API path uses. Both end up holding the same JSON, and the fiddly
    part is not the signature — it is that every timestamp is milliseconds and
    `price` is milliunits, so a second transcription is a second chance to
    divide by the wrong thousand and report someone's £39.99 yearly as £39,990.

    Takes `environment` separately rather than reading `payload["environment"]`
    because the caller has already had to decide whether that value is allowed.
    Re-reading it here would let a payload disagree with the gate that admitted
    it.

    Says nothing about whether the entitlement is *live* — that is
    `Entitlement.is_active`, and the tier is unconditionally "pro" because a
    signed transaction exists. Collapsing an expired or revoked one to FREE is
    the caller's decision, and only the access path makes it.
    """
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
    offer_identifier = payload.get("offerIdentifier")
    if not isinstance(offer_identifier, str):
        offer_identifier = None
    price_milli = payload.get("price")
    price = round(price_milli / 1000, 2) if isinstance(price_milli, (int, float)) else None
    currency = payload.get("currency") if isinstance(payload.get("currency"), str) else None

    revoked_ms = payload.get("revocationDate")
    revoked_at = (int(revoked_ms / 1000)
                  if isinstance(revoked_ms, (int, float)) else None)

    return Entitlement(
        tier="pro",
        product_id=payload.get("productId"),
        expires_at=expires_at,
        original_transaction_id=payload.get("originalTransactionId"),
        environment=environment,
        original_purchase_at=original_purchase_at,
        offer_type=offer_type,
        offer_discount_type=offer_discount_type,
        offer_identifier=offer_identifier,
        price=price,
        currency=currency,
        revoked_at=revoked_at,
    )


def verify_signed_transaction(
    jws_value: str,
    bundle_id: str,
    allowed_product_ids: set[str] | None = None,
    allowed_environments: frozenset[str] | None = None,
    *,
    allow_inactive: bool = False,
    allow_bounded_sandbox: bool = False,
) -> Entitlement:
    """Verify a StoreKit 2 JWS and return the entitlement it proves.

    `allow_inactive` returns what the transaction *says* even when it is
    expired or revoked, instead of collapsing to `FREE`. It exists for the
    App Store Server Notification path, which has to be able to record a
    subscription ending — a churn it reported as `FREE` would be
    indistinguishable from one it never heard about. It must never be set on a
    path that grants access: `FREE` is what keeps an expired transaction from
    being Pro, and `Entitlement.is_active` is the check that replaces it.

    `allow_bounded_sandbox` also admits a Sandbox transaction the environments
    do not trust, when SANDBOX_ENTITLEMENTS is `bounded`. Nothing on the result
    says "bounded" except its `environment`; `is_bounded` is how a caller tells.
    Only `EntitlementService.record` passes it, and only for an App
    Attest-authenticated caller, because that is where the bounds are applied —
    any other path that admitted Sandbox would admit it unbounded.
    """
    payload = verify_apple_jws(jws_value)

    if payload.get("bundleId") != bundle_id:
        raise EntitlementError("Signed transaction is for a different app.")

    # Environment gate. Must run before the entitlement is built: a Sandbox JWS
    # is cryptographically indistinguishable from a production one, so this is
    # the *only* thing separating a free tester account from paid Pro.
    environments = ALLOWED_ENVIRONMENTS if allowed_environments is None else allowed_environments
    environment = payload.get("environment", "Production")
    bounded = (environment not in environments
               and allow_bounded_sandbox
               and environment == SANDBOX_ENVIRONMENT
               and SANDBOX_ENTITLEMENTS == SANDBOX_BOUNDED)
    if environment not in environments and not bounded:
        log.warning("rejected transaction from disallowed environment",
                    extra={"environment": environment,
                           "allowed": sorted(environments)})
        raise EntitlementError("Signed transaction is from the wrong environment.")

    product_id = payload.get("productId")
    if allowed_product_ids and product_id not in allowed_product_ids:
        raise EntitlementError("Signed transaction is for an unrecognised product.")

    ent = entitlement_from_payload(payload, environment=environment)
    revoked_at = ent.revoked_at
    if allow_inactive:
        return ent
    # A bounded transaction that has ended still says which environment it came
    # from. `FREE` claims Production, and `record` would take that as Apple's
    # word on this subject — deleting a real subscriber's stored proof and
    # alerting the operator to a churn — on the strength of a tester's expiry.
    free = replace(FREE, environment=environment) if bounded else FREE
    if revoked_at is not None:
        log.info("signed transaction was revoked", extra={"product_id": product_id})
        return free
    if not ent.is_active:
        log.info("signed transaction has expired", extra={"product_id": product_id})
        return free
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

    @staticmethod
    def _sandbox_key(original_transaction_id: str) -> str:
        """The one device holding a bounded Sandbox transaction right now."""
        return f"entsandbox:{original_transaction_id}"

    @classmethod
    def _tombstone_key(cls, ent: Entitlement) -> str:
        """Where the revocation of `ent`'s term is recorded.

        Sandbox gets a namespace of its own. Apple does not promise that a
        Sandbox transaction id can never equal a Production one, and a tester
        can have Apple sign a REFUND for their own Sandbox purchase whenever
        they like — so an unscoped key would let one deny a paying customer.
        """
        otid = ent.original_transaction_id or ""
        if ent.environment == SANDBOX_ENVIRONMENT:
            return f"entrevoked:sandbox:{otid}"
        return cls._revoked_key(otid)

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
        await self._cache.set(self._tombstone_key(ent), payload,
                              ENTITLEMENT_PROOF_TTL)
        if ent.environment == SANDBOX_ENVIRONMENT:
            # A bounded entitlement has no proof for the tombstone to stop
            # re-deriving; what keeps it alive is the claim, which `current()`
            # checks on every read. Dropping it withdraws the access at the
            # holder's next request rather than when their entry lapses. The
            # tombstone still matters: it refuses the pre-refund JWS if it is
            # presented again.
            await self._cache.delete(self._sandbox_key(otid))
        log.info("subscription revoked by Apple",
                 extra={"product_id": ent.product_id,
                        "environment": ent.environment})
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
            raw = await self._cache.get(self._tombstone_key(ent))
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
                     device_id: str | None = None, *,
                     authenticated: bool = False) -> Entitlement:
        """Verify, device-bind, and cache. Raises `EntitlementError` if invalid.

        `device_id` is the client's stable per-device identifier (Keychain-
        backed from iOS 1.3.4). When present, the subscription is bound to it
        rather than to `subject`, so reinstalls on one phone occupy one slot.

        `authenticated` is `Principal.authenticated`: the caller presented a
        token this service minted after App Attest. It is the only thing that
        admits a Sandbox transaction on the bounded terms (SANDBOX_ENTITLEMENTS)
        and it defaults to False, so a caller that does not say is refused
        Sandbox exactly as before. Raises `EntitlementsUnavailable` only on
        that bounded path — see `_claim_sandbox`.
        """
        ent = verify_signed_transaction(jws_value, self._bundle_id, self._allowed,
                                        allow_bounded_sandbox=authenticated)
        if is_bounded(ent):
            return await self._record_bounded(subject, ent, device_id)

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

    # ── Sandbox, on bounded terms ────────────────────────────────────────
    #
    # Everything below serves SANDBOX_ENTITLEMENTS. It shares the revocation
    # tombstone with the path above and nothing else: no proof, no `txn:`
    # device binding, no six-device allowance.

    @staticmethod
    def _bounded_ttl(ent: Entitlement) -> int:
        """Lifetime of a bounded entitlement: SANDBOX_ENTITLEMENT_TTL, or less.

        Capped at the transaction's expiry *plus the grace* `is_active` gives
        every entitlement, unlike `_cache_ttl`. A Production entry can stop at
        the bare expiry because the proof re-derives it through the grace
        hour; a bounded one has no proof, and Sandbox renews a monthly plan
        every few minutes. Stopping at the bare expiry would drop a reviewer to
        the free tier in the gap between each renewal and the client
        re-presenting it — the rejection this path exists to prevent.
        """
        ttl = SANDBOX_ENTITLEMENT_TTL
        if ent.expires_at:
            ttl = max(60, min(ttl, ent.expires_at + EXPIRY_GRACE_SECONDS
                              - int(time.time())))
        return ttl

    async def _record_bounded(self, subject: str, ent: Entitlement,
                              device_id: str | None) -> Entitlement:
        """Record a Sandbox entitlement on the bounded terms.

        Writes the short entitlement entry and the one-device claim, and never
        a proof: when the entry lapses the device has to present a live
        transaction again. See SANDBOX_ENTITLEMENTS for the whole rule set.
        """
        if ent.tier == "pro" and await self._is_revoked(ent):
            log.info("refused a sandbox entitlement Apple has revoked",
                     extra={"product_id": ent.product_id})
            ent = replace(FREE, environment=ent.environment)

        if ent.tier != "pro":
            # Ends this subject's Sandbox access and nothing else. Unlike the
            # Production path this leaves any stored proof alone: Sandbox never
            # writes one, so a proof here is a real subscription, and a
            # tester's expiry is not Apple's word on it. `current()` reads this
            # entry as inactive and re-derives from that proof if there is one.
            await self._cache.set(
                self._key(subject), ent.to_json(), self._cache_ttl(ent))
            return ent

        otid = ent.original_transaction_id
        if not otid:
            # The one-device rule is keyed on it. Without one there is nothing
            # to bound, and an unbounded Sandbox grant is the one thing this
            # path must never produce.
            raise EntitlementError(
                "Sandbox transaction carries no original transaction id.")

        ttl = self._bounded_ttl(ent)
        await self._claim_sandbox(subject, otid, device_id, ttl)
        await self._cache.set(self._key(subject), ent.to_json(), ttl)
        log.info("sandbox entitlement recorded on bounded terms",
                 extra={"product_id": ent.product_id, "ttl": ttl})
        return ent

    @staticmethod
    def _decode_claim(raw: str | None) -> dict | None:
        if not raw:
            return None
        try:
            claim = json.loads(raw)
        except Exception:
            return None
        return claim if isinstance(claim, dict) else None

    async def _claim_sandbox(self, subject: str, otid: str,
                             device_id: str | None, ttl: int) -> None:
        """Make this device the one holder of a Sandbox transaction.

        **The rule is replace, not refuse.** The most recent device to present
        the transaction holds it, and `current()` reads every other subject's
        entry for it as free. So at most one device is Pro on it at any
        moment, and sharing one buys nothing but taking turns. Refusing would
        instead lock the transaction to whichever device got there first for
        up to a day, and the case this path exists for — App Review — is also
        the one likeliest to move between an iPhone and an iPad on one
        Sandbox account.

        The identity compared is `device_id` when the client sends one, so a
        reinstall on the same phone takes the claim over without it counting
        as a move; the subject stored is what `current()` checks.

        Unlike `_bind_device`, this is an authorisation boundary — the only
        thing keeping one tester transaction from being Pro on any number of
        phones — so it fails closed: an unreachable durable store raises
        `EntitlementsUnavailable` rather than granting without a claim.
        """
        key = self._sandbox_key(otid)
        identity = device_id or subject
        try:
            previous = self._decode_claim(await self._cache.get(key, required=True))
            if previous is not None and previous.get("device") != identity:
                # Two devices on one Sandbox account. Normal for a reviewer
                # testing iPad layout; a steady stream of these is sharing.
                log.info("sandbox entitlement moved to another device")
            await self._cache.set(key, json.dumps({
                "subject": subject, "device": identity, "at": int(time.time()),
            }), ttl, required=True)
        except CacheUnavailable as exc:
            raise EntitlementsUnavailable(str(exc)) from exc

    async def _holds_sandbox_claim(self, subject: str, ent: Entitlement) -> bool:
        """Whether `subject` may still use the bounded entitlement it cached.

        No, once SANDBOX_ENTITLEMENTS is `off`, once another device has taken
        the transaction over, and once a Sandbox refund has dropped the claim.
        Checked on every read of a bounded entry, which costs one cache read
        for testers and reviewers only, and is what lets all three take effect
        at the next request rather than when the entry lapses.
        """
        if SANDBOX_ENTITLEMENTS != SANDBOX_BOUNDED:
            return False
        otid = ent.original_transaction_id
        if not otid:
            return False
        try:
            raw = await self._cache.get(self._sandbox_key(otid), required=True)
        except CacheUnavailable as exc:
            raise EntitlementsUnavailable(str(exc)) from exc
        except Exception as exc:
            # Closed, unlike the tombstone read: this is the bound itself, and
            # a subject that cannot show it holds the claim does not.
            log.warning("sandbox claim read failed: %s", exc)
            return False
        claim = self._decode_claim(raw)
        return claim is not None and claim.get("subject") == subject

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
        if device_id and _is_legacy_subject(device_id):
            # A device id shaped like an attest subject reads as a pre-1.3.4
            # ghost at the eviction below, and silences the sharing alert on
            # the one eviction it exists to report. Measured against the real
            # function with the cap at 6 and 20 sharers: 14 evictions and 14
            # alerts with UUID ids, 14 evictions and *zero* alerts when the
            # same 20 ids are 64-char lowercase hex. The shape of the id is
            # the client's to choose; this prefix is not, because the wire
            # pattern `^[A-Za-z0-9._-]+$` admits no colon.
            identity = f"dev:{device_id}"
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

        # `> 0` because this is a *detection* threshold and 0 means "do not
        # detect" — the same reading `SAFETY_BLOCKS_BEFORE_PAUSE <= 0` gets in
        # main.py, and the one `.env.example` invites: "a *detection*
        # threshold, not an authorisation boundary". Without it, an operator
        # who set 0 to turn the sharing alert off broke every purchase
        # instead: `len({}) >= 0` is true on a first-ever binding, so
        # `min({})` raised ValueError, which is outside both try blocks here
        # and outside the `EntitlementError` the endpoint catches — a 500 on
        # /auth/entitlement for every subscriber, with no entitlement recorded
        # and no proof stored, on every retry until the variable changed back.
        #
        # Clamping the constant to `max(1, …)` instead — which is what the
        # audit entry proposed — would read an operator's "stop detecting" as
        # the tightest cap the system can express, evicting on every second
        # device. That is further from the intent than the crash was.
        if (self._max_devices > 0
                and identity not in bindings
                and len(bindings) >= self._max_devices):
            evicted = min(bindings, key=lambda s: bindings[s])
            idle_seconds = now - bindings.pop(evicted)
            # Worth seeing: on a genuinely shared subscription this is steady
            # churn, which is the signal the cap exists to surface. A short idle
            # time means the evicted device is still in use — real sharing —
            # while a long one is just a device that was replaced.
            #
            # `devices` is the count *after* the eviction plus the one about to
            # be written, which is the number actually holding the
            # subscription. It used to be `self._max_devices` — the same value
            # as `max` on every line ever emitted — so the one figure that
            # distinguishes a household at the limit from twenty strangers
            # churning through it was never recorded.
            log.info("device binding evicted to make room", extra={
                "devices": len(bindings) + 1, "max": self._max_devices,
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
                if not is_bounded(ent):
                    return ent
                if await self._holds_sandbox_claim(subject, ent):
                    return ent
                # Displaced, refunded or switched off. Whatever else this
                # subject holds — a real subscription's proof — is found the
                # ordinary way; a bounded entitlement has no proof to find.
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
            # Never `allow_bounded_sandbox`: a bounded entitlement is not
            # re-derived, by design, and a Sandbox proof left over from a
            # deployment that trusted Sandbox fully must not become one.
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
