"""Billing Grace Period: a failed renewal Apple is still retrying keeps Pro.

App Store Connect has it on (16 days, all renewals). During it the app
presents the last, expired transaction and expects Pro; the server used to
answer free.
"""

from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from billing import appstorenotify, entitlements  # noqa: E402
from billing.entitlements import EntitlementService  # noqa: E402
from core.cache import InMemoryCache, ResilientCache  # noqa: E402
from tests.test_appstorenotify import make_notification  # noqa: E402
from tests.test_entitlements import (  # noqa: E402,F401  (pinned_root is a fixture)
    BUNDLE_ID, PRODUCTS, make_jws, pinned_root, valid_payload)

DAY = 86_400


@pytest.fixture
def cache():
    return ResilientCache(None, InMemoryCache())


@pytest.fixture
def service(cache):
    return EntitlementService(cache, BUNDLE_ID, PRODUCTS)


def lapsed(leaf_key, chain, *, hours_ago=5):
    """The term that failed to renew: expired, as StoreKit still presents it."""
    expires = int(time.time()) - hours_ago * 3600
    jws = make_jws(valid_payload(expiresDate=expires * 1000), leaf_key, chain)
    ent = entitlements.verify_signed_transaction(
        jws, BUNDLE_ID, PRODUCTS, allow_inactive=True)
    return jws, ent


class TestAccessPath:
    @pytest.mark.asyncio
    async def test_expired_without_grace_is_free(self, service, pinned_root):
        jws, _ = lapsed(*pinned_root)
        assert (await service.record("s", jws)).tier == "free"

    @pytest.mark.asyncio
    async def test_grace_keeps_pro_until_it_ends(self, service, cache, pinned_root):
        jws, ent = lapsed(*pinned_root)
        until = int(time.time()) + 10 * DAY
        assert await entitlements.record_grace(cache, ent, until)
        got = await service.record("s", jws)
        assert got.tier == "pro" and got.expires_at == until
        assert (await service.current("s")).tier == "pro"

    @pytest.mark.asyncio
    async def test_rederived_from_the_proof_in_grace(self, service, cache, pinned_root):
        jws, ent = lapsed(*pinned_root)
        await entitlements.record_grace(cache, ent, int(time.time()) + 10 * DAY)
        await service.record("s", jws)
        await cache.delete(service._key("s"))          # the short entry lapsed
        assert (await service.current("s")).tier == "pro"

    @pytest.mark.asyncio
    async def test_cleared_grace_is_free_again(self, service, cache, pinned_root):
        jws, ent = lapsed(*pinned_root)
        await entitlements.record_grace(cache, ent, int(time.time()) + 10 * DAY)
        await entitlements.clear_grace(cache, ent)
        assert (await service.record("s", jws)).tier == "free"

    @pytest.mark.asyncio
    async def test_an_ended_grace_is_not_written(self, cache, pinned_root):
        _, ent = lapsed(*pinned_root)
        assert not await entitlements.record_grace(cache, ent, int(time.time()) - 1)

    @pytest.mark.asyncio
    async def test_grace_is_capped_at_apples_longest(self, service, cache, pinned_root):
        jws, ent = lapsed(*pinned_root)
        await entitlements.record_grace(cache, ent, int(time.time()) + 90 * DAY)
        got = await service.record("s", jws)
        assert got.expires_at == ent.expires_at + entitlements.BILLING_GRACE_MAX_SECONDS

    @pytest.mark.asyncio
    async def test_an_older_term_is_not_covered(self, service, cache, pinned_root):
        leaf_key, chain = pinned_root
        _, failed = lapsed(leaf_key, chain, hours_ago=5)
        older, _ = lapsed(leaf_key, chain, hours_ago=24 * 40)
        await entitlements.record_grace(cache, failed, int(time.time()) + 10 * DAY)
        assert (await service.record("s", older)).tier == "free"

    @pytest.mark.asyncio
    async def test_a_refunded_term_gets_no_grace(self, service, cache, pinned_root):
        jws, ent = lapsed(*pinned_root)
        await entitlements.record_grace(cache, ent, int(time.time()) + 10 * DAY)
        await service.revoke(ent, revoked_at=int(time.time()))
        assert (await service.record("s", jws)).tier == "free"


class TestNotification:
    def test_reads_the_grace_expiry(self, pinned_root):
        leaf_key, chain = pinned_root
        until = int(time.time()) + 16 * DAY
        renewal = make_jws({"autoRenewStatus": 1, "gracePeriodExpiresDate": until * 1000,
                            "originalTransactionId": "2000000000000001"}, leaf_key, chain)
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain, notification_type="DID_FAIL_TO_RENEW",
                              subtype="GRACE_PERIOD", renewal=renewal),
            BUNDLE_ID, PRODUCTS)
        assert note.grace_until == until and note.auto_renew is True

    def test_no_grace_without_the_field(self, pinned_root):
        leaf_key, chain = pinned_root
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain, notification_type="DID_FAIL_TO_RENEW",
                              auto_renew=True),
            BUNDLE_ID, PRODUCTS)
        assert note.grace_until is None


class TestRoute:
    @pytest.mark.asyncio
    async def test_failure_starts_and_renewal_ends_the_grace(self, cache, monkeypatch,
                                                             pinned_root):
        import main
        monkeypatch.setattr(main, "_cache", cache)
        _, ent = lapsed(*pinned_root)
        until = int(time.time()) + 16 * DAY

        await main._apply_billing_grace(SimpleNamespace(
            entitlement=ent, is_billing_failure=True, grace_until=until,
            notification_type="DID_FAIL_TO_RENEW"))
        assert await cache.get(entitlements._grace_key(ent))

        await main._apply_billing_grace(SimpleNamespace(
            entitlement=ent, is_billing_failure=False, grace_until=None,
            notification_type="DID_RENEW"))
        assert not await cache.get(entitlements._grace_key(ent))
