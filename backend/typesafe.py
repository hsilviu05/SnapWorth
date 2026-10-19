"""TypeSafe's Jev: one yes/no question, answered as a probability.

The wire format is the one `typesafe-sdk` 0.7.5 generates from
api.typesafe.ai's OpenAPI schema (`SystemOneRequest`, `NoulQuestion`,
`NoulAnswer`). It is called with httpx, which the backend already pins, rather
than by adding the SDK and its dependencies for one request shape.

Shared by the eval's identity judge (`eval/identity.py`) and `/listing`'s
claim check (`listingclaims.py`). Every call fails open: any error is None,
and the caller treats that as "not judged".
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger("snapworth.typesafe")

# The SDK's own environment names, so a key set up for it works here too.
API_KEY_ENV = "TYPESAFE_API_KEY"
BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")
# `jev-latest` moves when TypeSafe ships a build, so callers record the model
# that actually answered; set TYPESAFE_MODEL to pin one.
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")


def api_key() -> str:
    return os.environ.get(API_KEY_ENV, "").strip()


def enabled() -> bool:
    return bool(api_key())


def make_client(timeout: float, key: str | None = None,
                max_connections: int = 10) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL, timeout=timeout,
        headers={"Authorization": f"Bearer {(key or api_key()).strip()}"},
        limits=httpx.Limits(max_connections=max_connections,
                            max_keepalive_connections=max_connections,
                            keepalive_expiry=30.0))


async def post(client: httpx.AsyncClient, body: dict[str, Any]) -> object | None:
    """One `/v1/systemone` call; its decoded JSON, or None on any failure —
    network, timeout, status or body."""
    try:
        response = await client.post("/v1/systemone", json=body)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # The status and the exception type only: the body could echo the key.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning("typesafe call failed: %s%s", type(exc).__name__,
                    f" (HTTP {status})" if status else "")
        return None


def noul(answers: object, name: str) -> float | None:
    """The probability in `answers[name]`, or None when it is not one."""
    answer = answers.get(name) if isinstance(answers, dict) else None
    value = answer.get("noul") if isinstance(answer, dict) else None
    # bool is an int: a `true` here is a malformed body, not probability 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        return None
    return float(value)
