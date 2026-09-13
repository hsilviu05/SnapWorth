"""App Store Server Notifications V2.

The subscription index used to be written from exactly one place: the client
POSTing `/auth/entitlement`. That made the operator's revenue view wrong in two
directions at once, and both were live on 2026-09-11:

  * a 3-day trial converted to a paid yearly, and the index — still holding the
    trial transaction, whose expiry had passed — reported the customer as
    churned;
  * a monthly subscriber had never synced at all and did not exist in it.

Apple reports both directly. These tests cover the verification that makes an
unauthenticated endpoint safe, and the index transitions that were wrong.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
# The JWS-forging helpers live in the entitlement tests; this reuses them
# rather than keeping a second copy of Apple's certificate shape in sync.
sys.path.insert(0, _HERE)

import appstorenotify  # noqa: E402
import entitlements  # noqa: E402
from entitlements import EntitlementError  # noqa: E402
from test_entitlements import (  # noqa: E402
    BUNDLE_ID,
    PRODUCTS,
    build_chain,
    make_jws,
    valid_payload,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_notification(leaf_key, chain, *, notification_type="DID_RENEW",
                      subtype=None, uuid="11111111-2222-3333-4444-555555555555",
                      bundle_id=BUNDLE_ID, environment="Production",
                      transaction=None, **transaction_overrides) -> str:
    """Build Apple's V2 envelope with a signed transaction nested inside."""
    if transaction is None:
        transaction = make_jws(
            valid_payload(environment=environment, **transaction_overrides),
            leaf_key, chain)
    payload = {
        "notificationType": notification_type,
        "notificationUUID": uuid,
        "version": "2.0",
        "signedDate": int(time.time() * 1000),
        "data": {
            "bundleId": bundle_id,
            "environment": environment,
            "signedTransactionInfo": transaction,
        },
    }
    if subtype:
        payload["subtype"] = subtype
    return make_jws(payload, leaf_key, chain)


@pytest.fixture
def pinned(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    leaf_key, chain = build_chain()
    monkeypatch.setattr(entitlements, "APPLE_ROOT_CA_G3_PEM",
                        chain[-1].public_bytes(serialization.Encoding.PEM))
    return leaf_key, chain


# ── What makes an unauthenticated endpoint safe ─────────────────────────────

class TestNotificationVerification:
    def test_a_genuinely_signed_notification_is_accepted(self, pinned):
        leaf_key, chain = pinned
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain), BUNDLE_ID, PRODUCTS)
        assert note.notification_type == "DID_RENEW"
        assert note.entitlement.product_id == "com.snapworth.yearly"

    def test_a_payload_signed_by_anyone_else_is_rejected(self, pinned):
        """The whole basis for accepting this endpoint without auth. Signed by
        a well-formed chain that simply is not Apple's."""
        _, _ = pinned
        other_key, other_chain = build_chain()
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(
                make_notification(other_key, other_chain), BUNDLE_ID, PRODUCTS)

    def test_a_notification_for_another_app_is_rejected(self, pinned):
        leaf_key, chain = pinned
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(
                make_notification(leaf_key, chain, bundle_id="com.someone.else"),
                BUNDLE_ID, PRODUCTS)

    def test_the_envelope_bundle_is_checked_even_when_the_transaction_is_ours(
            self, pinned):
        """The envelope names the app independently of the transaction inside
        it. Both have to be us."""
        leaf_key, chain = pinned
        ours = make_jws(valid_payload(), leaf_key, chain)
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(
                make_notification(leaf_key, chain, bundle_id="com.someone.else",
                                  transaction=ours),
                BUNDLE_ID, PRODUCTS)

    def test_sandbox_is_rejected_when_production_only(self, pinned):
        """A TestFlight tester's renewals are signed by the same chain as real
        ones. Without this they land in the revenue view as money."""
        leaf_key, chain = pinned
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(
                make_notification(leaf_key, chain, environment="Sandbox"),
                BUNDLE_ID, PRODUCTS,
                allowed_environments=frozenset({"Production"}))

    def test_sandbox_is_accepted_where_it_is_allowed(self, pinned):
        leaf_key, chain = pinned
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain, environment="Sandbox"),
            BUNDLE_ID, PRODUCTS, allowed_environments=frozenset({"Sandbox"}))
        assert note.entitlement.environment == "Sandbox"

    def test_a_product_we_do_not_sell_is_rejected(self, pinned):
        leaf_key, chain = pinned
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(
                make_notification(leaf_key, chain, productId="com.snapworth.gold"),
                BUNDLE_ID, PRODUCTS)

    def test_a_notification_without_a_transaction_is_rejected(self, pinned):
        leaf_key, chain = pinned
        envelope = make_jws({
            "notificationType": "DID_RENEW", "notificationUUID": "u",
            "data": {"bundleId": BUNDLE_ID, "environment": "Production"},
        }, leaf_key, chain)
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(envelope, BUNDLE_ID, PRODUCTS)

    def test_a_notification_without_a_uuid_is_rejected(self, pinned):
        """The UUID is the only thing that makes Apple's retries idempotent."""
        leaf_key, chain = pinned
        transaction = make_jws(valid_payload(), leaf_key, chain)
        envelope = make_jws({
            "notificationType": "DID_RENEW",
            "data": {"bundleId": BUNDLE_ID, "environment": "Production",
                     "signedTransactionInfo": transaction},
        }, leaf_key, chain)
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(envelope, BUNDLE_ID, PRODUCTS)

    def test_an_oversized_payload_is_refused_before_any_crypto(self, pinned):
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(
                "x" * (appstorenotify.MAX_SIGNED_PAYLOAD + 1), BUNDLE_ID, PRODUCTS)


# ── Reading an expired transaction, which the access path refuses to ────────

class TestExpiredAndRevoked:
    def test_an_expired_transaction_still_reports_its_facts(self, pinned):
        """`verify_signed_transaction` collapses an expired transaction to FREE
        so it cannot grant Pro. On the reporting path that would make churn
        indistinguishable from a subscription we never heard of — which is the
        bug this module exists to fix, in the other direction."""
        leaf_key, chain = pinned
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain, notification_type="EXPIRED",
                              expiresDate=int((time.time() - 86_400) * 1000)),
            BUNDLE_ID, PRODUCTS)
        assert note.entitlement.product_id == "com.snapworth.yearly"
        assert note.entitlement.is_active is False
        assert note.is_loss

    def test_the_access_path_is_unchanged_by_that(self, pinned):
        """The flag must stay off everywhere access is decided."""
        leaf_key, chain = pinned
        jws = make_jws(
            valid_payload(expiresDate=int((time.time() - 86_400) * 1000)),
            leaf_key, chain)
        assert entitlements.verify_signed_transaction(
            jws, BUNDLE_ID, PRODUCTS).tier == "free"

    def test_a_revoked_transaction_is_never_active(self, pinned):
        leaf_key, chain = pinned
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain, notification_type="REFUND",
                              revocationDate=int(time.time() * 1000)),
            BUNDLE_ID, PRODUCTS)
        assert note.entitlement.revoked_at is not None
        assert note.entitlement.is_active is False
        assert note.is_refund

    def test_a_revoked_transaction_still_grants_nothing(self, pinned):
        leaf_key, chain = pinned
        jws = make_jws(valid_payload(revocationDate=int(time.time() * 1000)),
                       leaf_key, chain)
        assert entitlements.verify_signed_transaction(
            jws, BUNDLE_ID, PRODUCTS).tier == "free"


# ── What the notification means ─────────────────────────────────────────────

class TestSemantics:
    def test_a_renewal_with_no_offer_is_a_paid_period(self, pinned):
        leaf_key, chain = pinned
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain, notification_type="DID_RENEW"),
            BUNDLE_ID, PRODUCTS)
        assert note.is_paid_period

    def test_a_trial_start_is_not_a_paid_period(self, pinned):
        """offerType 1 + FREE_TRIAL. Calling this money is exactly how the
        paywall bug read too."""
        leaf_key, chain = pinned
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain, notification_type="SUBSCRIBED",
                              subtype="INITIAL_BUY", offerType=1,
                              offerDiscountType="FREE_TRIAL"),
            BUNDLE_ID, PRODUCTS)
        assert not note.is_paid_period

    def test_an_offer_code_redemption_is_not_a_paid_period(self, pinned):
        leaf_key, chain = pinned
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain, notification_type="SUBSCRIBED",
                              offerType=3),
            BUNDLE_ID, PRODUCTS)
        assert not note.is_paid_period

    def test_auto_renew_off_is_a_cancellation_not_a_loss(self, pinned):
        leaf_key, chain = pinned
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain,
                              notification_type="DID_CHANGE_RENEWAL_STATUS",
                              subtype="AUTO_RENEW_DISABLED"),
            BUNDLE_ID, PRODUCTS)
        assert note.is_cancellation
        assert not note.is_loss

    def test_apples_own_test_notification_is_accepted(self, pinned):
        """`TEST` carries no transaction — there is no purchase behind it. It
        has to be recognised before anything reads signedTransactionInfo, or
        the one mechanism Apple gives for proving the endpoint works reports it
        as broken while real notifications are fine."""
        leaf_key, chain = pinned
        envelope = make_jws({
            "notificationType": "TEST",
            "notificationUUID": "test-uuid",
            "version": "2.0",
            "signedDate": int(time.time() * 1000),
            # Exactly what Apple sends: no signedTransactionInfo.
            "data": {"bundleId": BUNDLE_ID, "environment": "Production"},
        }, leaf_key, chain)
        note = appstorenotify.parse_notification(envelope, BUNDLE_ID, PRODUCTS)
        assert note.is_test
        assert note.entitlement is None
        assert note.environment == "Production"
        assert not note.is_indexed, "nothing to record"
        assert not note.is_paid_period, "must not read a missing entitlement"

    def test_a_test_notification_is_still_verified(self, pinned):
        """Carrying no transaction does not make it a free pass: it is still
        signed by Apple, for our bundle, in an allowed environment."""
        _, _ = pinned
        other_key, other_chain = build_chain()
        envelope = make_jws({
            "notificationType": "TEST", "notificationUUID": "t",
            "data": {"bundleId": BUNDLE_ID, "environment": "Production"},
        }, other_key, other_chain)
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(envelope, BUNDLE_ID, PRODUCTS)

    def test_a_test_notification_for_another_app_is_rejected(self, pinned):
        leaf_key, chain = pinned
        envelope = make_jws({
            "notificationType": "TEST", "notificationUUID": "t",
            "data": {"bundleId": "com.someone.else", "environment": "Production"},
        }, leaf_key, chain)
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(envelope, BUNDLE_ID, PRODUCTS)

    def test_a_non_test_type_still_requires_its_transaction(self, pinned):
        """The TEST branch must not become a way to skip the transaction."""
        leaf_key, chain = pinned
        envelope = make_jws({
            "notificationType": "DID_RENEW", "notificationUUID": "t",
            "data": {"bundleId": BUNDLE_ID, "environment": "Production"},
        }, leaf_key, chain)
        with pytest.raises(EntitlementError):
            appstorenotify.parse_notification(envelope, BUNDLE_ID, PRODUCTS)

    def test_an_unrecognised_type_is_parsed_but_not_indexed(self, pinned):
        """Verified and acknowledged; never allowed to write a guessed row."""
        leaf_key, chain = pinned
        note = appstorenotify.parse_notification(
            make_notification(leaf_key, chain,
                              notification_type="CONSUMPTION_REQUEST"),
            BUNDLE_ID, PRODUCTS)
        assert not note.is_indexed
        assert not note.is_paid_period

    def test_a_renewal_extension_summary_is_acknowledged_not_rejected(self, pinned):
        """`summary` instead of `data`, which the parser used to refuse.

        Apple sends RENEWAL_EXTENSION with subtype SUMMARY when a server-wide
        renewal extension finishes, and its envelope has no `data` member at
        all — it carries `summary`. Requiring `data` raised before the type was
        even read, the endpoint turned that into a 400, and Apple redelivered
        the same notification for about three days. It is genuine, signed, for
        this bundle, and there is nothing in it to record: exactly the standing
        of CONSUMPTION_REQUEST above.
        """
        leaf_key, chain = pinned
        envelope = make_jws({
            "notificationType": "RENEWAL_EXTENSION",
            "subtype": "SUMMARY",
            "notificationUUID": "99999999-8888-7777-6666-555555555555",
            "version": "2.0",
            "signedDate": int(time.time() * 1000),
            "summary": {
                "bundleId": BUNDLE_ID,
                "environment": "Production",
                "productId": "com.snapworth.pro.monthly",
                "requestIdentifier": "fdb6-4ca4",
                "storefrontCountryCodes": ["US", "GB"],
                "succeededCount": 12,
                "failedCount": 0,
            },
        }, leaf_key, chain)

        note = appstorenotify.parse_notification(envelope, BUNDLE_ID, PRODUCTS)

        assert note.notification_type == "RENEWAL_EXTENSION"
        assert note.subtype == "SUMMARY"
        assert note.entitlement is None
        assert not note.is_indexed, "nothing to record, so nothing is recorded"
        assert note.environment == "Production"

    def test_an_external_purchase_token_is_acknowledged_too(self, pinned):
        """The other data-less shape. No `environment`, so it defaults."""
        leaf_key, chain = pinned
        envelope = make_jws({
            "notificationType": "EXTERNAL_PURCHASE_TOKEN",
            "subtype": "UNREPORTED",
            "notificationUUID": "77777777-6666-5555-4444-333333333333",
            "version": "2.0",
            "signedDate": int(time.time() * 1000),
            "externalPurchaseToken": {
                "bundleId": BUNDLE_ID,
                "externalPurchaseId": "ext-1",
                "tokenCreationDate": int(time.time() * 1000),
                "appAppleId": 1234567890,
            },
        }, leaf_key, chain)

        note = appstorenotify.parse_notification(envelope, BUNDLE_ID, PRODUCTS)

        assert note.entitlement is None
        assert not note.is_indexed

    def test_the_data_less_shapes_are_still_checked_for_the_bundle(self, pinned):
        """The gates moved onto the member; they did not stop running."""
        leaf_key, chain = pinned
        envelope = make_jws({
            "notificationType": "RENEWAL_EXTENSION",
            "subtype": "SUMMARY",
            "notificationUUID": "99999999-8888-7777-6666-555555555555",
            "version": "2.0",
            "signedDate": int(time.time() * 1000),
            "summary": {"bundleId": "com.someone.else",
                        "environment": "Production"},
        }, leaf_key, chain)
        with pytest.raises(EntitlementError, match="different app"):
            appstorenotify.parse_notification(envelope, BUNDLE_ID, PRODUCTS)

        sandbox = make_jws({
            "notificationType": "RENEWAL_EXTENSION",
            "subtype": "SUMMARY",
            "notificationUUID": "99999999-8888-7777-6666-555555555556",
            "version": "2.0",
            "signedDate": int(time.time() * 1000),
            "summary": {"bundleId": BUNDLE_ID, "environment": "Sandbox"},
        }, leaf_key, chain)
        with pytest.raises(EntitlementError, match="wrong environment"):
            appstorenotify.parse_notification(
                sandbox, BUNDLE_ID, PRODUCTS, allowed_environments={"Production"})

    def test_a_shape_with_no_known_member_names_the_type_it_refused(self, pinned):
        """So the WARNING the endpoint logs says which type arrived.

        Under the old order the type had not been read when this raised, so
        the operator's log line was a bare "carries no data" with no way to
        tell a malformed DID_RENEW from a shape Apple has newly added.
        """
        leaf_key, chain = pinned
        envelope = make_jws({
            "notificationType": "SOMETHING_NEW",
            "notificationUUID": "66666666-5555-4444-3333-222222222222",
            "version": "2.0",
            "signedDate": int(time.time() * 1000),
        }, leaf_key, chain)
        with pytest.raises(EntitlementError, match="SOMETHING_NEW carries no data"):
            appstorenotify.parse_notification(envelope, BUNDLE_ID, PRODUCTS)
