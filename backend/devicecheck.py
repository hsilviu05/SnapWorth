"""Apple DeviceCheck client.

DeviceCheck stores **two bits per physical device**, scoped to our developer
account, that survive app deletion and reinstall. That persistence is the only
reason it exists here: App Attest key ids are per-*installation*, so a reinstall
mints a new subject and would otherwise reset the free-scan allowance.

Known limitation, stated plainly because it shapes the design: Apple exposes
only a two-bit value plus a **month-granularity** `last_update_time`. It cannot
express "3 scans used today". So the split is:

  * the **daily counter** lives in our cache, keyed by App Attest key id;
  * **bit 0** marks "this device has exhausted its free allowance", and is
    consulted when a *fresh* subject appears, which is exactly the reinstall
    case.

A determined user can still get a new device. That is an acceptable floor.
"""

from __future__ import annotations

import base64
import logging
import os
import time
import uuid

log = logging.getLogger("snapworth.devicecheck")

PRODUCTION_HOST = "https://api.devicecheck.apple.com"
SANDBOX_HOST = "https://api.development.devicecheck.apple.com"

_HTTP_TIMEOUT = 5.0

# Valid base64, certainly not a real device token. Used only by `verify()`,
# which cares about the Authorization header Apple checks first.
_PROBE_TOKEN = base64.b64encode(b"snapworth-devicecheck-probe").decode()


class DeviceCheckError(Exception):
    """DeviceCheck call failed. Never surfaced to the client verbatim."""


class DeviceCheckClient:
    """Queries and updates the two per-device bits.

    Unconfigured is a supported state: `is_configured` is False and callers skip
    the reinstall check rather than failing. That keeps the service bootable
    before the App Store Connect key is provisioned.
    """

    def __init__(
        self,
        team_id: str = "",
        key_id: str = "",
        private_key_pem: str = "",
        use_sandbox: bool = False,
    ) -> None:
        self._team_id = team_id.strip()
        self._key_id = key_id.strip()
        self._private_key = private_key_pem.strip().replace("\\n", "\n")
        self._host = SANDBOX_HOST if use_sandbox else PRODUCTION_HOST
        self._cached_jwt: tuple[str, float] | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._team_id and self._key_id and self._private_key)

    def _auth_jwt(self) -> str:
        """ES256 JWT for the DeviceCheck API, cached until shortly before expiry."""
        now = time.time()
        if self._cached_jwt and self._cached_jwt[1] > now + 60:
            return self._cached_jwt[0]

        import jwt as pyjwt
        token = pyjwt.encode(
            {"iss": self._team_id, "iat": int(now)},
            self._private_key,
            algorithm="ES256",
            headers={"kid": self._key_id},
        )
        # Apple accepts these for up to an hour; refresh at 30 min.
        self._cached_jwt = (token, now + 1800)
        return token

    async def _post(self, path: str, payload: dict) -> tuple[int, str]:
        headers = {"Authorization": f"Bearer {self._auth_jwt()}"}
        client = await _shared_client()
        response = await client.post(f"{self._host}{path}", json=payload, headers=headers)
        return response.status_code, response.text

    async def query_bits(self, device_token: str) -> dict | None:
        """Return the stored bits, or None when the device is not yet known.

        Apple answers an unrecognised-but-valid token with 200 and the body
        "Failed to find bit state", which is a normal first-run state.
        """
        if not self.is_configured:
            return None
        status, body = await self._post("/v1/query_two_bits", {
            "device_token": device_token,
            "transaction_id": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
        })
        if status == 200:
            body_stripped = body.strip()
            if not body_stripped or "Failed to find bit state" in body_stripped:
                return None
            import json
            try:
                return json.loads(body_stripped)
            except ValueError:
                return None
        if status == 401:
            raise DeviceCheckError("DeviceCheck authentication rejected")
        raise DeviceCheckError(f"DeviceCheck query failed ({status})")

    async def update_bits(self, device_token: str, bit0: bool, bit1: bool) -> None:
        if not self.is_configured:
            return
        status, body = await self._post("/v1/update_two_bits", {
            "device_token": device_token,
            "transaction_id": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "bit0": bit0,
            "bit1": bit1,
        })
        if status != 200:
            raise DeviceCheckError(f"DeviceCheck update failed ({status})")

    def _key_problem(self) -> str | None:
        """A shape problem in the configured PEM, named without echoing it.

        Every malformed key raises the same bare `ValueError` from cryptography
        — a flattened one-liner, a body with no BEGIN/END, and a truncated file
        are indistinguishable by exception type. The type alone is useless to
        whoever has to fix it, so check the two shapes that actually go wrong
        when a .p8 is pasted into a hosting panel.
        """
        key = self._private_key
        if "BEGIN" not in key or "END" not in key:
            return ("DEVICECHECK_PRIVATE_KEY has no BEGIN/END lines — paste the whole "
                    ".p8 file, not just the base64 body")
        if "\n" not in key.strip():
            return ("DEVICECHECK_PRIVATE_KEY is on a single line — its newlines were "
                    "lost in transit. Re-paste it with real line breaks, or with a "
                    "literal \\n between them")
        return None

    async def verify(self) -> tuple[bool, str]:
        """Prove the credentials actually sign, without needing a real device.

        `is_configured` only says three environment variables are non-empty. It
        cannot tell a working key from a typo, and every DeviceCheck failure
        degrades open on purpose (see `quota.starting_balance`) — so a wrong key
        looks exactly like a healthy service that keeps handing out free scans.

        Apple reads the Authorization header before the request body, which
        separates the two answers we need:

          * **401** — the JWT was refused: `APPLE_TEAM_ID`, `DEVICECHECK_KEY_ID`
            or the key itself is wrong.
          * **400** — the JWT was *accepted* and Apple got as far as rejecting
            the obviously-fake device token. That is the pass we are after.

        Returns (ok, detail); never raises.
        """
        if not self.is_configured:
            return False, "not configured"

        problem = self._key_problem()
        if problem:
            return False, problem

        # Signing is attempted separately from the request so a key that cannot
        # be read is never reported as though Apple had refused it.
        try:
            self._auth_jwt()
        except Exception as exc:
            return False, (f"private key unreadable — {str(exc)[:120]} "
                           "(it must be the unencrypted P-256 .p8 from the Keys page)")

        try:
            status, body = await self._post("/v1/query_two_bits", {
                "device_token": _PROBE_TOKEN,
                "transaction_id": str(uuid.uuid4()),
                "timestamp": int(time.time() * 1000),
            })
        except Exception as exc:                      # DNS, TLS, timeout
            return False, f"could not reach Apple ({type(exc).__name__})"

        if status == 401:
            return False, ("key rejected — check APPLE_TEAM_ID, DEVICECHECK_KEY_ID "
                           "and that the key has the DeviceCheck capability")
        if status in (200, 400):
            # 200 would mean Apple somehow knew the probe token; either way the
            # signature was good, which is the only thing being asked.
            return True, "credentials accepted by Apple"
        return False, f"unexpected HTTP {status}: {body.strip()[:80]}"

    @staticmethod
    def looks_like_token(value: str) -> bool:
        """Cheap shape check so obvious junk never reaches Apple."""
        if not value or len(value) > 4096:
            return False
        try:
            base64.b64decode(value, validate=True)
            return True
        except Exception:
            return False


def client_from_env() -> DeviceCheckClient:
    return DeviceCheckClient(
        team_id=os.environ.get("APPLE_TEAM_ID", ""),
        key_id=os.environ.get("DEVICECHECK_KEY_ID", ""),
        private_key_pem=os.environ.get("DEVICECHECK_PRIVATE_KEY", ""),
        use_sandbox=os.environ.get("DEVICECHECK_SANDBOX", "").lower() in {"1", "true", "yes"},
    )


# ── Shared HTTP client ───────────────────────────────────────────────────────
# Previously `_post` opened `httpx.AsyncClient()` per call, so every DeviceCheck
# request paid a fresh TCP connect plus a TLS handshake to Apple — roughly
# 100-200 ms of avoidable latency, and a new socket per call. At the volumes a
# launch implies that is both slow and a connection-churn problem on the host.
#
# A module-level pooled client keeps keep-alive connections warm. Created lazily
# under a lock so import stays side-effect free, and closed by the app lifespan.

import asyncio as _asyncio

_client = None
_client_lock = _asyncio.Lock()

# Small pool: DeviceCheck is called on attestation and quota exhaustion, not on
# the hot scan path, so a large pool would hold idle sockets for nothing.
_MAX_CONNECTIONS = int(os.environ.get("DEVICECHECK_MAX_CONNECTIONS", "10"))


async def _shared_client():
    """Lazily create the pooled client. Safe under concurrency."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:                      # re-check inside the lock
            import httpx

            _client = httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT,
                limits=httpx.Limits(
                    max_connections=_MAX_CONNECTIONS,
                    max_keepalive_connections=_MAX_CONNECTIONS,
                    keepalive_expiry=30.0,
                ),
            )
    return _client


async def aclose() -> None:
    """Close the pooled client. Called from the app lifespan on shutdown."""
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.aclose()
