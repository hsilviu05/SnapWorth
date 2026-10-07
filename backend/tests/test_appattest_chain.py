"""App Attest verification, run end to end on a chain the test builds.

The other App Attest tests feed malformed input, which fails before the checks
that decide anything, so the success path never ran: deleting the nonce
comparison, the key binding or the assertion's signature check left every test
passing (AUDIT-2026-10-07, M2). These build a real attestation and assertion,
signed by a test root standing in for Apple's, and break one thing at a time.
Each test names the check that must refuse it, so removing any check fails one.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import struct
import sys
from pathlib import Path

import cbor2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import appattest  # noqa: E402
from appattest import AttestationError  # noqa: E402

APP_ID = "TEAM123456.eu.snapworth.app"
CHALLENGE = b"a-single-use-server-challenge"
NOW = dt.datetime.now(dt.timezone.utc)
DER = serialization.Encoding.DER


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _cert(subject: str, issuer: str, public_key, signing_key, *, ca: bool,
          extensions: tuple = (), not_after: dt.datetime | None = None) -> x509.Certificate:
    builder = (x509.CertificateBuilder()
               .subject_name(_name(subject))
               .issuer_name(_name(issuer))
               .public_key(public_key)
               .serial_number(x509.random_serial_number())
               .not_valid_before(NOW - dt.timedelta(days=2))
               .not_valid_after(not_after or NOW + dt.timedelta(days=2))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    for extension in extensions:
        builder = builder.add_extension(extension, critical=False)
    return builder.sign(signing_key, hashes.SHA256())


def _nonce_extension(nonce: bytes) -> x509.UnrecognizedExtension:
    # Apple's shape: SEQUENCE { [1] EXPLICIT OCTET STRING nonce }.
    return x509.UnrecognizedExtension(
        appattest._NONCE_OID, bytes([0x30, 0x24, 0xA1, 0x22, 0x04, 0x20]) + nonce)


def _key_id(public_key) -> bytes:
    return hashlib.sha256(public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).digest()


def _auth_data(*, rp_id: str, counter: int, aaguid: bytes, credential_id: bytes) -> bytes:
    return (hashlib.sha256(rp_id.encode()).digest() + b"\x40" + struct.pack(">I", counter)
            + aaguid + struct.pack(">H", len(credential_id)) + credential_id)


@pytest.fixture
def root_key(monkeypatch):
    """A test root, trusted in place of Apple's App Attest root."""
    key = ec.generate_private_key(ec.SECP256R1())
    root = _cert("Test App Attest Root", "Test App Attest Root", key.public_key(), key, ca=True)
    monkeypatch.setattr(appattest, "_load_root", lambda: root)
    return key


def attestation(root_key, *, challenge: bytes = CHALLENGE, rp_id: str = APP_ID,
                counter: int = 0, aaguid: bytes = appattest._AAGUID_PROD,
                credential_id: bytes | None = None, nonce: bytes | None = None,
                with_nonce: bool = True, leaf_not_after: dt.datetime | None = None,
                intermediate_signer=None) -> tuple[bytes, bytes, ec.EllipticCurvePrivateKey]:
    """(attestation object, key id, the device's private key), genuine unless
    a keyword says which one thing to get wrong."""
    device = ec.generate_private_key(ec.SECP256R1())
    key_id = _key_id(device.public_key())
    intermediate_key = ec.generate_private_key(ec.SECP256R1())
    intermediate = _cert("Test App Attest CA", "Test App Attest Root",
                         intermediate_key.public_key(), intermediate_signer or root_key, ca=True)
    auth_data = _auth_data(rp_id=rp_id, counter=counter, aaguid=aaguid,
                           credential_id=key_id if credential_id is None else credential_id)
    expected = hashlib.sha256(auth_data + hashlib.sha256(challenge).digest()).digest()
    leaf = _cert(key_id.hex(), "Test App Attest CA", device.public_key(), intermediate_key,
                 ca=False, not_after=leaf_not_after,
                 extensions=(_nonce_extension(nonce or expected),) if with_nonce else ())
    obj = {"fmt": "apple-appattest",
           "attStmt": {"x5c": [leaf.public_bytes(DER), intermediate.public_bytes(DER)],
                       "receipt": b"receipt"},
           "authData": auth_data}
    return cbor2.dumps(obj), key_id, device


def verify(blob: bytes, key_id: bytes, *, challenge: bytes = CHALLENGE,
           allow_development: bool = False) -> appattest.AttestationResult:
    return appattest.verify_attestation(attestation=blob, challenge=challenge, key_id=key_id,
                                        app_id=APP_ID, allow_development=allow_development)


# ── Attestation ──────────────────────────────────────────────────────────────

class TestAttestation:
    def test_a_genuine_attestation_is_accepted(self, root_key):
        blob, key_id, device = attestation(root_key)
        result = verify(blob, key_id)
        assert (result.key_id, result.counter, result.environment) == (key_id, 0, "production")
        assert result.receipt == b"receipt"
        stored = serialization.load_pem_public_key(result.public_key_pem)
        assert _key_id(stored) == key_id, "the stored key is the attested one"

    def test_a_development_attestation_needs_allowing(self, root_key):
        blob, key_id, _ = attestation(root_key, aaguid=appattest._AAGUID_DEV)
        with pytest.raises(AttestationError, match="Development attestations"):
            verify(blob, key_id)
        assert verify(blob, key_id, allow_development=True).environment == "development"

    def test_an_answer_to_another_challenge_is_refused(self, root_key):
        blob, key_id, _ = attestation(root_key)
        with pytest.raises(AttestationError, match="challenge did not match"):
            verify(blob, key_id, challenge=b"a-different-challenge")

    def test_a_tampered_nonce_is_refused(self, root_key):
        blob, key_id, _ = attestation(root_key, nonce=b"\x00" * 32)
        with pytest.raises(AttestationError, match="challenge did not match"):
            verify(blob, key_id)

    def test_a_leaf_without_its_nonce_is_refused(self, root_key):
        blob, key_id, _ = attestation(root_key, with_nonce=False)
        with pytest.raises(AttestationError, match="missing its nonce"):
            verify(blob, key_id)

    def test_a_key_id_that_is_not_the_attested_key_is_refused(self, root_key):
        blob, _, _ = attestation(root_key)
        with pytest.raises(AttestationError, match="does not match the supplied key id"):
            verify(blob, hashlib.sha256(b"someone else's key").digest())

    def test_another_apps_attestation_is_refused(self, root_key):
        blob, key_id, _ = attestation(root_key, rp_id="OTHERTEAM.com.example.app")
        with pytest.raises(AttestationError, match="different app"):
            verify(blob, key_id)

    def test_a_credential_that_is_not_the_key_is_refused(self, root_key):
        blob, key_id, _ = attestation(root_key, credential_id=b"\x01" * 32)
        with pytest.raises(AttestationError, match="credential mismatch"):
            verify(blob, key_id)

    def test_an_unknown_environment_is_refused(self, root_key):
        blob, key_id, _ = attestation(root_key, aaguid=b"somethingelse!!!")
        with pytest.raises(AttestationError, match="Unrecognised attestation environment"):
            verify(blob, key_id, allow_development=True)

    def test_a_replayed_attestation_with_a_counter_is_refused(self, root_key):
        blob, key_id, _ = attestation(root_key, counter=1)
        with pytest.raises(AttestationError, match="counter is not zero"):
            verify(blob, key_id)

    def test_a_chain_not_rooted_in_the_trusted_root_is_refused(self, root_key):
        impostor = ec.generate_private_key(ec.SECP256R1())
        blob, key_id, _ = attestation(root_key, intermediate_signer=impostor)
        with pytest.raises(AttestationError, match=r"not signed by Apple \(intermediate/root\)"):
            verify(blob, key_id)

    def test_an_expired_leaf_is_refused(self, root_key):
        blob, key_id, _ = attestation(root_key, leaf_not_after=NOW - dt.timedelta(hours=1))
        with pytest.raises(AttestationError, match="leaf certificate is not valid today"):
            verify(blob, key_id)


# ── Assertion ────────────────────────────────────────────────────────────────

def assertion(device, *, challenge: bytes = CHALLENGE, counter: int = 1,
              rp_id: str = APP_ID) -> bytes:
    auth_data = hashlib.sha256(rp_id.encode()).digest() + b"\x00" + struct.pack(">I", counter)
    digest = hashlib.sha256(auth_data + hashlib.sha256(challenge).digest()).digest()
    signature = device.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    return cbor2.dumps({"signature": signature, "authenticatorData": auth_data})


def check(blob: bytes, device, *, challenge: bytes = CHALLENGE, previous: int = 0) -> int:
    pem = device.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return appattest.verify_assertion(assertion=blob, challenge=challenge, public_key_pem=pem,
                                      app_id=APP_ID, previous_counter=previous)


class TestAssertion:
    device = ec.generate_private_key(ec.SECP256R1())

    def test_a_genuine_assertion_returns_its_counter(self):
        assert check(assertion(self.device, counter=7), self.device, previous=6) == 7

    def test_a_signature_by_another_key_is_refused(self):
        forger = ec.generate_private_key(ec.SECP256R1())
        with pytest.raises(AttestationError, match="signature is invalid"):
            check(assertion(forger), self.device)

    def test_a_signature_over_another_challenge_is_refused(self):
        blob = assertion(self.device, challenge=b"an-older-challenge")
        with pytest.raises(AttestationError, match="signature is invalid"):
            check(blob, self.device)

    def test_a_counter_that_did_not_advance_is_refused(self):
        with pytest.raises(AttestationError, match="did not advance"):
            check(assertion(self.device, counter=5), self.device, previous=5)

    def test_another_apps_assertion_is_refused(self):
        blob = assertion(self.device, rp_id="OTHERTEAM.com.example.app")
        with pytest.raises(AttestationError, match="different app"):
            check(blob, self.device)
