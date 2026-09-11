"""The JWT the App Store Server API expects.

A malformed token comes back as a bare 401, which reads as "your key is wrong"
and sends you to regenerate a key that was fine. The `bid` claim in particular
is what separates an App Store Server API token from an App Store Connect one —
omit it and the request authenticates and then fails, with nothing saying why.
"""

from __future__ import annotations

import os
import sys
import time

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from appstore_test_notification import TOKEN_LIFETIME_SECONDS, build_token  # noqa: E402


def _key() -> tuple[str, ec.EllipticCurvePublicKey]:
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return pem, private.public_key()


class TestAppStoreServerToken:

    def test_it_carries_every_claim_apple_requires(self):
        pem, public = _key()
        token = build_token(pem, "KEYID123", "issuer-uuid", "eu.snapworth.app")
        claims = pyjwt.decode(token, public, algorithms=["ES256"],
                              audience="appstoreconnect-v1")
        assert claims["iss"] == "issuer-uuid"
        assert claims["aud"] == "appstoreconnect-v1"
        # The claim that separates this from an App Store Connect API token.
        assert claims["bid"] == "eu.snapworth.app"

    def test_the_key_id_is_in_the_header_not_the_body(self):
        """Apple looks up the signing key by the `kid` header. In the payload
        it is ignored and the signature cannot be checked."""
        pem, _ = _key()
        token = build_token(pem, "KEYID123", "issuer-uuid", "eu.snapworth.app")
        header = pyjwt.get_unverified_header(token)
        assert header["kid"] == "KEYID123"
        assert header["alg"] == "ES256"
        assert header["typ"] == "JWT"

    def test_it_expires_inside_apples_limit(self):
        """Apple rejects a token valid for more than 60 minutes."""
        assert TOKEN_LIFETIME_SECONDS <= 60 * 60
        pem, public = _key()
        token = build_token(pem, "K", "i", "b")
        claims = pyjwt.decode(token, public, algorithms=["ES256"],
                              audience="appstoreconnect-v1")
        assert claims["exp"] - claims["iat"] == TOKEN_LIFETIME_SECONDS
        assert claims["iat"] <= int(time.time()) + 1
