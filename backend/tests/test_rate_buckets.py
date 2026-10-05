"""/scan, /listing and /trends each spend their own per-device bucket.

They used to share one: 20 an hour across all three, with no tier split. A
Pro reseller who scanned an item and drafted its listing was stopped after
about ten items on a plan sold as "Unlimited scans", and every /trends fetch
from My Finds spent a scan. The per-IP backstop is deliberately unchanged —
one bucket for every route — so each test that is about a device bucket sends
its requests from distinct addresses, and the one about the IP bucket sends
them from one.
"""

from __future__ import annotations

import io
import json
from itertools import count
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
import ratelimit
from tests.images import padded_image_bytes
from tests.test_main import (MOCK_LISTING_JSON, MOCK_RESPONSE_JSON, _listing_body,
                             _pro_headers, client)

_addresses = count(1)


def _fresh_ip() -> dict:
    """A caller address no other request in this run has used.

    `client_ip` keys the IP bucket on the forwarded client address, so this
    keeps the shared IP cap out of a test that is about a device bucket."""
    n = next(_addresses)
    return {"x-forwarded-for": f"198.51.{n // 250}.{n % 250 + 1}"}


def _reply(payload: dict) -> MagicMock:
    reply = MagicMock()
    reply.text = json.dumps(payload)
    return reply


def _scan(headers: dict, ip: dict | None = None):
    with patch("main._model") as model:
        model.generate_content_async = AsyncMock(return_value=_reply(MOCK_RESPONSE_JSON))
        return client.post(
            "/scan", headers=headers | (ip if ip is not None else _fresh_ip()),
            files={"file": ("s.jpg", io.BytesIO(padded_image_bytes("JPEG", 1024)),
                            "image/jpeg")})


def _listing(headers: dict, ip: dict | None = None):
    with patch("main._model") as model:
        model.generate_content_async = AsyncMock(return_value=_reply(MOCK_LISTING_JSON))
        return client.post("/listing", json=_listing_body(),
                           headers=headers | (ip if ip is not None else _fresh_ip()))


def _trends(headers: dict, ip: dict | None = None):
    return client.get("/trends", headers=headers | (ip if ip is not None else _fresh_ip()))


@pytest.fixture(autouse=True)
def _empty_limits():
    main._rate_store.clear()
    main._ip_rate_store.clear()
    yield
    main._rate_store.clear()
    main._ip_rate_store.clear()


def _pro(name: str) -> dict:
    return {"x-device-id": name} | _pro_headers(f"pro-{name}")


class TestTheBucketsAreSeparate:
    def test_a_full_hour_of_drafts_leaves_scanning_alone(self):
        headers = _pro("drafts-then-scan")
        for _ in range(ratelimit.LISTING_RATE_MAX_REQUESTS):
            assert _listing(headers).status_code == 200
        assert _listing(headers).status_code == 429, "the listing bucket has a cap"
        assert _scan(headers).status_code == 200, "a draft spent a scan"
        assert _trends(headers).status_code == 200

    def test_a_full_hour_of_scans_leaves_drafting_and_trends_alone(self):
        headers = _pro("scans-then-draft")
        for _ in range(ratelimit.PRO_SCAN_RATE_MAX_REQUESTS):
            assert _scan(headers).status_code == 200
        assert _scan(headers).status_code == 429
        assert _listing(headers).status_code == 200, "a scan spent a draft"
        assert _trends(headers).status_code == 200, "a scan spent a trends fetch"

    def test_trends_never_spends_a_scan(self):
        headers = {"x-device-id": "trends-then-scan"}
        for _ in range(ratelimit.RATE_MAX_REQUESTS):
            assert _trends(headers).status_code == 200
        # The free tier's scan cap is the smallest bucket there is; a fetch
        # of the Trending card used to take one slot of it.
        assert _scan(headers).status_code == 200

    def test_trends_has_a_cap_of_its_own(self):
        headers = {"x-device-id": "trends-loop"}
        for _ in range(ratelimit.TRENDS_RATE_MAX_REQUESTS):
            assert _trends(headers).status_code == 200
        r = _trends(headers)
        assert r.status_code == 429
        assert r.json()["detail"] == (
            f"Rate limit: {ratelimit.TRENDS_RATE_MAX_REQUESTS} requests/hour.")


class TestTheScanCapFollowsTheTier:
    def test_free_is_unchanged(self):
        headers = {"x-device-id": "free-cap"}
        for _ in range(ratelimit.RATE_MAX_REQUESTS):
            assert _scan(headers).status_code == 200
        r = _scan(headers)
        assert r.status_code == 429
        assert r.json()["detail"] == f"Rate limit: {ratelimit.RATE_MAX_REQUESTS} requests/hour."
        assert int(r.headers["Retry-After"]) > 0

    def test_pro_goes_past_the_free_cap_and_stops_at_its_own(self):
        headers = _pro("pro-cap")
        for n in range(ratelimit.PRO_SCAN_RATE_MAX_REQUESTS):
            assert _scan(headers).status_code == 200, f"Pro refused at scan {n + 1}"
        r = _scan(headers)
        assert r.status_code == 429
        assert r.json()["detail"] == (
            f"Rate limit: {ratelimit.PRO_SCAN_RATE_MAX_REQUESTS} requests/hour.")

    def test_the_sizes(self):
        # Free unchanged, and Pro's fair-use ceiling, as the owner chose.
        # Listings at the address cap rather than the 20 they had: see
        # TestADraftIsRefusedOnlyWithScanning.
        assert ratelimit.RATE_MAX_REQUESTS == 20
        assert ratelimit.PRO_SCAN_RATE_MAX_REQUESTS == 60
        assert ratelimit.LISTING_RATE_MAX_REQUESTS == 60
        assert ratelimit.TRENDS_RATE_MAX_REQUESTS > ratelimit.RATE_MAX_REQUESTS


class TestADraftIsRefusedOnlyWithScanning:
    """Every build of the app says "You've hit the scan limit." for any 429,
    a draft's included, and Haul pauses both of its queues on either one's.
    That holds only if a Pro user's draft is never refused while scanning
    still has room. So /listing's own bucket is not smaller than the address
    bucket, which counts scans and drafts together and so fills first."""

    def test_a_reseller_who_drafts_every_item_is_stopped_on_both_at_once(self):
        ip = {"x-forwarded-for": "203.0.113.90"}
        headers = _pro("scan-and-draft")
        # A scan and a draft per item, from one address, until it is full.
        for n in range(ratelimit.IP_RATE_MAX_REQUESTS // 2):
            assert _scan(headers, ip).status_code == 200, f"scan {n + 1} refused"
            assert _listing(headers, ip).status_code == 200, f"draft {n + 1} refused"
        # Past the old 20 drafts, and now refused together, as the app says.
        assert _listing(headers, ip).status_code == 429
        assert _scan(headers, ip).status_code == 429

    def test_the_draft_bucket_is_not_below_the_address_bucket(self):
        assert ratelimit.LISTING_RATE_MAX_REQUESTS >= ratelimit.IP_RATE_MAX_REQUESTS


class TestTheIpBackstopIsStillShared:
    def test_one_address_is_capped_across_every_route(self):
        """The per-IP bucket is the real backstop — device ids are cheap to
        mint — so splitting the device buckets must not split it."""
        ip = {"x-forwarded-for": "203.0.113.77"}
        for n in range(ratelimit.IP_RATE_MAX_REQUESTS):
            # A different device each time: only the address is shared.
            assert _trends({"x-device-id": f"ip-shared-{n}"}, ip).status_code == 200
        assert _scan({"x-device-id": "ip-shared-scan"}, ip).status_code == 429
        assert _listing(_pro("ip-shared-draft"), ip).status_code == 429


class TestTheDistributedPathKeys:
    """What Redis sees. The scan bucket keeps the `dev:` key it had when it
    was the only bucket, so a deploy neither resets nor re-keys anyone."""

    class _Recording:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        async def check(self, key: str, limit: int, window: int = 3600) -> None:
            self.calls.append((key, limit))

    @pytest.fixture
    def limiters(self, monkeypatch):
        device, ip = self._Recording(), self._Recording()
        monkeypatch.setattr(main, "_device_limiter", device)
        monkeypatch.setattr(main, "_ip_limiter", ip)
        return device, ip

    def test_each_route_names_its_bucket_and_size(self, limiters):
        device, ip = limiters
        pro = _pro("keys")
        assert _scan(pro, {"x-forwarded-for": "192.0.2.1"}).status_code == 200
        assert _scan({"x-device-id": "keys-free"},
                     {"x-forwarded-for": "192.0.2.1"}).status_code == 200
        assert _listing(pro, {"x-forwarded-for": "192.0.2.1"}).status_code == 200
        assert _trends(pro, {"x-forwarded-for": "192.0.2.1"}).status_code == 200
        assert device.calls == [
            ("dev:pro-keys", ratelimit.PRO_SCAN_RATE_MAX_REQUESTS),
            ("dev:legacy:keys-free", ratelimit.RATE_MAX_REQUESTS),
            ("listing:pro-keys", ratelimit.LISTING_RATE_MAX_REQUESTS),
            ("trends:pro-keys", ratelimit.TRENDS_RATE_MAX_REQUESTS),
        ]
        assert ip.calls == [("ip:192.0.2.1", ratelimit.IP_RATE_MAX_REQUESTS)] * 4
