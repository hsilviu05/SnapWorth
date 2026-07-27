"""Shared test wiring.

`TestClient(app)` does not run the lifespan unless used as a context manager, so
`auth.deps` would otherwise be unpopulated and every protected route would fail
with an AttributeError rather than exercising real behaviour. This fixture wires
the same objects startup would, backed by the in-memory cache.

The free-scan limit is set high by default so pre-existing tests (which issue
many scans to probe rate limits) are unaffected. Quota-specific tests build
their own `ScanQuota` with a realistic limit.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
import tokens  # noqa: E402
from cache import InMemoryCache, ResilientCache  # noqa: E402
from entitlements import EntitlementService  # noqa: E402
from quota import ScanQuota  # noqa: E402

TEST_TEAM_ID = "A93FGV7L5Y"
TEST_BUNDLE_ID = "eu.snapworth.app"
TEST_APP_ID = f"{TEST_TEAM_ID}.{TEST_BUNDLE_ID}"

# Deterministic signing key so token tests are reproducible.
TEST_TOKEN_KEYS = {"test": b"test-signing-secret-value-0123456789"}


def build_deps(*, enforce: bool = False, free_scans: int = 1_000_000) -> ResilientCache:
    """Populate `auth.deps` and return the cache backing it."""
    cache = ResilientCache(None, InMemoryCache())
    auth.deps.cache = cache
    auth.deps.signer = tokens.TokenSigner(dict(TEST_TOKEN_KEYS), "test")
    auth.deps.device_check = None
    auth.deps.entitlements = EntitlementService(
        cache, TEST_BUNDLE_ID, {"com.snapworth.monthly", "com.snapworth.yearly"})
    auth.deps.quota = ScanQuota(cache, None, limit=free_scans)

    cfg = auth.AuthConfig()
    cfg.team_id = TEST_TEAM_ID
    cfg.bundle_id = TEST_BUNDLE_ID
    cfg.enforce = enforce
    cfg.allow_development = True
    auth.deps.config = cfg
    return cache


@pytest.fixture(autouse=True)
def _wire_auth_deps():
    """Fresh, permissive deps for every test unless a test overrides them."""
    build_deps()
    yield
    build_deps()
