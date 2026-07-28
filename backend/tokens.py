"""Server-issued authentication tokens.

App Attest is expensive (a certificate-chain verification) and its assertion
counter is stateful, so we do it once and exchange the result for a short-lived
bearer token. Every subsequent request carries the token instead.

Design notes:

* **HMAC, not RSA/EC.** The token is issued and verified by the same service,
  so asymmetric signing buys nothing and costs latency.
* **Key rotation is built in from day one.** Tokens carry a key id (`kid`), and
  verification accepts any *active* key while signing always uses the current
  one. Rotating is therefore: add a new key, wait one token lifetime, retire the
  old. No flag day, no mass sign-out.
* **Constant-time comparison** on the signature, so the MAC can't be recovered
  by timing.
* **`jti` + short TTL** gives replay protection when paired with the shared
  cache: a token presented twice inside its own lifetime is rejected if the
  caller opts into single-use semantics.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

log = logging.getLogger("snapworth.tokens")

# Access tokens are deliberately short-lived: a leaked one stops working within
# the hour, and the client can always mint another with a cheap assertion.
DEFAULT_TTL_SECONDS = 3600
MAX_TOKEN_BYTES = 4096            # bound parsing work on hostile input


class TokenError(Exception):
    """Token missing, malformed, expired, or tampered with."""


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


class TokenSigner:
    """Mints and verifies bearer tokens, with support for overlapping keys.

    `keys` maps key id → secret. `current_kid` selects the signing key; every
    key in the map remains valid for verification, which is what makes rotation
    non-disruptive.
    """

    def __init__(self, keys: dict[str, bytes], current_kid: str) -> None:
        if current_kid not in keys:
            raise ValueError("current_kid must be present in keys")
        if not keys[current_kid]:
            raise ValueError("signing key must not be empty")
        self._keys = dict(keys)
        self._current_kid = current_kid

    @property
    def current_kid(self) -> str:
        return self._current_kid

    @property
    def active_kids(self) -> list[str]:
        return sorted(self._keys)

    def mint(
        self,
        subject: str,
        tier: str = "free",
        ttl: int = DEFAULT_TTL_SECONDS,
        extra: dict | None = None,
    ) -> tuple[str, dict]:
        """Return (token, claims)."""
        now = int(time.time())
        claims = {
            "sub": subject,
            "tier": tier,
            "iat": now,
            "exp": now + ttl,
            "jti": secrets.token_urlsafe(12),
            "kid": self._current_kid,
        }
        if extra:
            # Reserved claims win, so a caller can't forge tier/exp via `extra`.
            claims = {**extra, **claims}
        payload = _b64u_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        signature = self._sign(payload, self._current_kid)
        return f"{payload}.{signature}", claims

    def verify(self, token: str, leeway: int = 30) -> dict:
        """Validate signature and expiry; return the claims.

        `leeway` tolerates modest clock skew between instances.
        """
        if not token or len(token) > MAX_TOKEN_BYTES:
            raise TokenError("Missing or oversized token.")
        parts = token.split(".")
        if len(parts) != 2:
            raise TokenError("Malformed token.")
        payload, signature = parts

        try:
            claims = json.loads(_b64u_decode(payload))
        except Exception:
            raise TokenError("Malformed token.") from None
        if not isinstance(claims, dict):
            raise TokenError("Malformed token.")

        kid = claims.get("kid")
        if not isinstance(kid, str) or kid not in self._keys:
            # Unknown kid means a retired key or a forged one — same answer.
            raise TokenError("Token key is not recognised.")

        expected = self._sign(payload, kid)
        if not hmac.compare_digest(expected, signature):
            raise TokenError("Token signature is invalid.")

        exp = claims.get("exp")
        if not isinstance(exp, int) or exp + leeway < int(time.time()):
            raise TokenError("Token has expired.")

        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise TokenError("Malformed token.")
        return claims

    def _sign(self, payload: str, kid: str) -> str:
        mac = hmac.new(self._keys[kid], payload.encode(), hashlib.sha256).digest()
        return _b64u_encode(mac)


def signer_from_env() -> TokenSigner:
    """Build a signer from the environment.

    ``TOKEN_KEYS`` holds ``kid:secret`` pairs, comma-separated; ``TOKEN_CURRENT_KID``
    selects the signing key. In development a random key is generated so the
    service still starts — but every token dies on restart, which is loud enough
    to notice and safe by default.
    """
    raw = os.environ.get("TOKEN_KEYS", "").strip()
    current = os.environ.get("TOKEN_CURRENT_KID", "").strip()

    keys: dict[str, bytes] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        kid, _, secret = pair.partition(":")
        kid, secret = kid.strip(), secret.strip()
        if kid and secret:
            keys[kid] = secret.encode()

    if not keys:
        # In production an ephemeral key means every deploy silently signs out
        # every user, and any multi-replica deployment rejects tokens minted by
        # a sibling. Refuse to start rather than ship that quietly — this mirrors
        # the REQUIRE_APP_ATTEST guard in main._lifespan.
        if os.environ.get("ENVIRONMENT", "").lower() in {"production", "prod"}:
            raise RuntimeError(
                "TOKEN_KEYS must be set in production. Generate one with "
                "`python -c \"import secrets;print('k1:'+secrets.token_urlsafe(32))\"`"
            )
        log.error(
            "TOKEN_KEYS is not set — generating an ephemeral signing key. "
            "All tokens become invalid on restart. Set TOKEN_KEYS in production."
        )
        keys = {"dev": secrets.token_bytes(32)}
        current = "dev"

    if current not in keys:
        current = sorted(keys)[0]
        log.warning("TOKEN_CURRENT_KID unset or unknown — signing with %r", current)

    return TokenSigner(keys, current)
