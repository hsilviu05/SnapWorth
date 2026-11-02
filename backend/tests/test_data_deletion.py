"""Delete my server data (`DELETE /me/data`).

Pinned here: only an attested device can ask, it deletes that device's own
index rows, sync note and (when not subscribed) subscription record and
nobody else's, it keeps the anti-abuse markers the privacy policy says it
keeps, and it says so when it could not delete rather than claiming it did.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from security import auditlog, auth  # noqa: E402
from ops import opsindex, opsstats  # noqa: E402
from main import app  # noqa: E402

client = TestClient(app)

SUBJECT = "attested-key-forget"
OTHER = "attested-key-other"


def run(coro):
    return asyncio.run(coro)


def bearer(subject: str = SUBJECT) -> dict:
    token, _ = auth.deps.signer.mint(subject)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _bind_indexes():
    cache = auth.deps.cache
    opsindex.bind(cache)
    opsstats.bind(cache)
    yield
    opsindex.bind(None)
    opsstats.bind(None)


def seed() -> tuple[str, str]:
    cache = auth.deps.cache
    who, other = auditlog.pseudonymise(SUBJECT), auditlog.pseudonymise(OTHER)
    now = 2_000_000_000
    users = {who: {"first": now, "last": now, "scans": 3, "tier": "free"},
             other: {"first": now, "last": now, "scans": 1, "tier": "free"}}
    subs = {"otid-1": {"seen": now, "who": who, "devices": [who[:6], other]}}
    run(cache.set(opsindex.USERS_INDEX_KEY, json.dumps(users)))
    run(cache.set(opsindex.SUBS_INDEX_KEY, json.dumps(subs)))
    run(cache.set(opsindex.sync_key(who), json.dumps([now, "free"])))
    run(cache.set(opsindex.sync_key(other), json.dumps([now, "free"])))
    run(cache.set(f"ent:{SUBJECT}", "{}"))
    run(cache.set(f"entproof:{SUBJECT}", "proof"))
    run(cache.set(f"quota:welcome:{SUBJECT}", "1"))
    return who, other


def index(key: str) -> dict:
    return json.loads(run(auth.deps.cache.get(key)) or "{}")


@pytest.fixture
def far_future_index():
    # Rows are dropped once 400 days old; the seeded timestamps are in 2033.
    with patch("ops.opsindex.time.time", return_value=2_000_000_000):
        yield


def test_refuses_an_unattested_caller():
    r = client.delete("/me/data", headers={"x-device-id": "someone-else"})
    assert r.status_code == 401


def test_deletes_this_devices_records_and_nobody_elses(far_future_index):
    who, other = seed()
    r = client.delete("/me/data", headers=bearer())
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "subscription_kept": False}

    users = index(opsindex.USERS_INDEX_KEY)
    assert who not in users and other in users
    row = index(opsindex.SUBS_INDEX_KEY)["otid-1"]
    assert row["devices"] == [other], "the six-character legacy id is the same device"
    assert "who" not in row
    cache = auth.deps.cache
    assert run(cache.get(opsindex.sync_key(who))) is None
    assert run(cache.get(opsindex.sync_key(other))) is not None
    assert run(cache.get(f"ent:{SUBJECT}")) is None
    assert run(cache.get(f"entproof:{SUBJECT}")) is None


def test_keeps_the_markers_that_stop_a_free_allowance_resetting(far_future_index):
    seed()
    assert client.delete("/me/data", headers=bearer()).status_code == 200
    assert run(auth.deps.cache.get(f"quota:welcome:{SUBJECT}")) == "1"


def test_keeps_an_active_subscriptions_record(far_future_index):
    who, _ = seed()

    async def pro(_subject):
        return SimpleNamespace(tier="pro")

    with patch.object(auth.deps.entitlements, "current", pro):
        r = client.delete("/me/data", headers=bearer())
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "subscription_kept": True}
    assert run(auth.deps.cache.get(f"entproof:{SUBJECT}")) == "proof"
    assert who not in index(opsindex.USERS_INDEX_KEY)


def test_says_so_when_the_index_cannot_be_read(far_future_index):
    seed()

    async def unreadable(_key):
        return None

    with patch.object(opsstats, "read_doc_for_update", unreadable):
        r = client.delete("/me/data", headers=bearer())
    assert r.status_code == 503
