"""Apple App Attest verification.

App Attest proves a request came from a genuine, unmodified build of *our* app
on real Apple hardware. It is the control that closes SEC-01: without it the
Gemini-backed endpoints are callable by anyone who can read a URL.

Two operations, matching Apple's two-step protocol:

  * **Attestation** (once per install) — the client generates a hardware-backed
    key and asks Apple to vouch for it. We verify the certificate chain up to
    Apple's App Attest Root CA, bind it to our App ID, and store the public key.

  * **Assertion** (per request, or per token mint) — the client signs a
    challenge with that key. We verify the signature and that the counter has
    strictly increased, which is what makes replay detectable.

Reference: "Validating Apps That Connect to Your Server", Apple Developer.
"""

from __future__ import annotations

import hashlib
import logging
import struct
from dataclasses import dataclass
from datetime import datetime, timezone

import cbor2
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

log = logging.getLogger("snapworth.appattest")

# Apple App Attest Root CA — G1. Public certificate, safe to embed; pinning it
# here is the trust anchor for the whole scheme.
# https://www.apple.com/certificateauthority/private/
APPLE_APP_ATTEST_ROOT_CA_PEM = b"""-----BEGIN CERTIFICATE-----
MIICITCCAaegAwIBAgIQC/O+DvHN0uD7jG5yH2IXmDAKBggqhkjOPQQDAzBSMSYw
JAYDVQQDDB1BcHBsZSBBcHAgQXR0ZXN0YXRpb24gUm9vdCBDQTETMBEGA1UECgwK
QXBwbGUgSW5jLjETMBEGA1UECAwKQ2FsaWZvcm5pYTAeFw0yMDAzMTgxODMyNTNa
Fw00NTAzMTUwMDAwMDBaMFIxJjAkBgNVBAMMHUFwcGxlIEFwcCBBdHRlc3RhdGlv
biBSb290IENBMRMwEQYDVQQKDApBcHBsZSBJbmMuMRMwEQYDVQQIDApDYWxpZm9y
bmlhMHYwEAYHKoZIzj0CAQYFK4EEACIDYgAERTHhmLW07ATaFQIEVwTtT4dyctdh
NbJhFs/Ii2FdCgAHGbpphY3+d8qjuDngIN3WVhQUBHAoMeQ/cLiP1sOUtgjqK9au
Yen1mMEvRq9Sk3Jm5X8U62H+xTD3FE9TgS41o0IwQDAPBgNVHRMBAf8EBTADAQH/
MB0GA1UdDgQWBBSskRBTM72+aEH/pwyp5frq5eWKoTAOBgNVHQ8BAf8EBAMCAQYw
CgYIKoZIzj0EAwMDaAAwZQIwQgFGnByvsiVbpTKwSga0kP0e8EeDS4+sQmTvb7vn
53O5+FRXgeLhpJ06ysC5PrOyAjEAp5U4xDgEgllF7En3VcE3iexZZtKeYnpqtijV
oyFraWVIyd/dganmrduC1bmTBGwD
-----END CERTIFICATE-----"""

# OID for the App Attest nonce extension inside the leaf certificate.
_NONCE_OID = x509.ObjectIdentifier("1.2.840.113635.100.8.2")

# Apple's aaguid marks which environment produced the attestation. Accepting
# the development value in production would let a debug build call the API.
_AAGUID_PROD = b"appattest\x00\x00\x00\x00\x00\x00\x00"
_AAGUID_DEV = b"appattestdevelop"


class AttestationError(Exception):
    """Attestation or assertion failed verification. Message is user-safe."""


@dataclass(frozen=True)
class AttestationResult:
    key_id: bytes
    public_key_pem: bytes
    receipt: bytes
    counter: int
    environment: str          # "production" | "development"


def _load_root() -> x509.Certificate:
    return x509.load_pem_x509_certificate(APPLE_APP_ATTEST_ROOT_CA_PEM)


def _verify_chain(certs: list[x509.Certificate]) -> x509.Certificate:
    """Verify leaf → intermediate → Apple root. Returns the leaf.

    Signature and validity are both checked at each hop. A chain that merely
    *parses* proves nothing; this is the step that makes the attestation
    meaningful.
    """
    if len(certs) < 2:
        raise AttestationError("Attestation certificate chain is incomplete.")

    leaf, intermediate = certs[0], certs[1]
    root = _load_root()
    now = datetime.now(timezone.utc)

    for cert, name in ((leaf, "leaf"), (intermediate, "intermediate")):
        if cert.not_valid_before_utc > now or cert.not_valid_after_utc < now:
            raise AttestationError(f"Attestation {name} certificate is not valid today.")

    for child, parent, label in (
        (leaf, intermediate, "leaf/intermediate"),
        (intermediate, root, "intermediate/root"),
    ):
        try:
            parent_key = parent.public_key()
            if not isinstance(parent_key, ec.EllipticCurvePublicKey):
                raise AttestationError("Unexpected attestation key type.")
            parent_key.verify(
                child.signature,
                child.tbs_certificate_bytes,
                ec.ECDSA(child.signature_hash_algorithm),
            )
        except InvalidSignature:
            raise AttestationError(f"Attestation chain is not signed by Apple ({label}).") from None
    return leaf


def _parse_auth_data(auth_data: bytes) -> tuple[bytes, int, bytes, bytes]:
    """Return (rp_id_hash, counter, aaguid, credential_id) from authenticatorData."""
    if len(auth_data) < 55:
        raise AttestationError("Malformed attestation data.")
    rp_id_hash = auth_data[0:32]
    counter = struct.unpack(">I", auth_data[33:37])[0]
    aaguid = auth_data[37:53]
    cred_len = struct.unpack(">H", auth_data[53:55])[0]
    credential_id = auth_data[55:55 + cred_len]
    if len(credential_id) != cred_len:
        raise AttestationError("Malformed attestation credential.")
    return rp_id_hash, counter, aaguid, credential_id


def verify_attestation(
    attestation: bytes,
    challenge: bytes,
    key_id: bytes,
    app_id: str,
    allow_development: bool = False,
) -> AttestationResult:
    """Verify an App Attest attestation object.

    `app_id` is "<TeamID>.<BundleID>". `challenge` must be a server-generated,
    single-use nonce — reusing one makes the whole exchange replayable.
    """
    try:
        obj = cbor2.loads(attestation)
    except Exception:
        raise AttestationError("Attestation object could not be decoded.") from None

    if obj.get("fmt") != "apple-appattest":
        raise AttestationError("Unexpected attestation format.")

    stmt = obj.get("attStmt") or {}
    auth_data = obj.get("authData")
    x5c = stmt.get("x5c") or []
    receipt = stmt.get("receipt") or b""
    if not auth_data or not x5c:
        raise AttestationError("Attestation object is missing required fields.")

    try:
        certs = [x509.load_der_x509_certificate(c) for c in x5c]
    except Exception:
        raise AttestationError("Attestation certificates could not be parsed.") from None

    leaf = _verify_chain(certs)

    # 1. nonce = SHA256(authData || SHA256(challenge)) must match the value
    #    Apple embedded in the leaf certificate extension.
    client_data_hash = hashlib.sha256(challenge).digest()
    expected_nonce = hashlib.sha256(auth_data + client_data_hash).digest()
    try:
        ext = leaf.extensions.get_extension_for_oid(_NONCE_OID)
        # The extension wraps the nonce in a small DER structure; the digest is
        # the trailing 32 bytes.
        raw = ext.value.value if hasattr(ext.value, "value") else bytes(ext.value.public_bytes())
        embedded_nonce = raw[-32:]
    except x509.ExtensionNotFound:
        raise AttestationError("Attestation certificate is missing its nonce.") from None
    if embedded_nonce != expected_nonce:
        raise AttestationError("Attestation challenge did not match.")

    # 2. The key id must be the SHA256 of the attested public key, which binds
    #    the credential the client claims to the one Apple signed.
    public_key = leaf.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise AttestationError("Unexpected attestation key type.")
    pub_raw = public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    if hashlib.sha256(pub_raw).digest() != key_id:
        raise AttestationError("Attestation key does not match the supplied key id.")

    # 3. authData must be bound to our App ID, and the credential id must equal
    #    the key id.
    rp_id_hash, counter, aaguid, credential_id = _parse_auth_data(auth_data)
    if rp_id_hash != hashlib.sha256(app_id.encode()).digest():
        raise AttestationError("Attestation was issued for a different app.")
    if credential_id != key_id:
        raise AttestationError("Attestation credential mismatch.")

    if aaguid == _AAGUID_PROD:
        environment = "production"
    elif aaguid == _AAGUID_DEV:
        environment = "development"
        if not allow_development:
            raise AttestationError("Development attestations are not accepted here.")
    else:
        raise AttestationError("Unrecognised attestation environment.")

    # A fresh attestation always starts at 0; anything else is a replayed object.
    if counter != 0:
        raise AttestationError("Attestation counter is not zero.")

    pem = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    log.info("attestation verified", extra={"environment": environment})
    return AttestationResult(
        key_id=key_id,
        public_key_pem=pem,
        receipt=receipt,
        counter=counter,
        environment=environment,
    )


def verify_assertion(
    assertion: bytes,
    challenge: bytes,
    public_key_pem: bytes,
    app_id: str,
    previous_counter: int,
) -> int:
    """Verify a per-request assertion; returns the new counter.

    The counter must strictly increase. That single check is what turns a
    captured request into a detectably-replayed one.
    """
    try:
        obj = cbor2.loads(assertion)
        signature = obj["signature"]
        auth_data = obj["authenticatorData"]
    except Exception:
        raise AttestationError("Assertion could not be decoded.") from None

    if len(auth_data) < 37:
        raise AttestationError("Malformed assertion data.")
    rp_id_hash = auth_data[0:32]
    counter = struct.unpack(">I", auth_data[33:37])[0]

    if rp_id_hash != hashlib.sha256(app_id.encode()).digest():
        raise AttestationError("Assertion was issued for a different app.")
    if counter <= previous_counter:
        # Either a replay or a rolled-back device. Both are rejected.
        raise AttestationError("Assertion counter did not advance.")

    client_data_hash = hashlib.sha256(challenge).digest()
    digest = hashlib.sha256(auth_data + client_data_hash).digest()

    try:
        key = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise AttestationError("Unexpected assertion key type.")
        key.verify(signature, digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    except InvalidSignature:
        raise AttestationError("Assertion signature is invalid.") from None

    return counter
