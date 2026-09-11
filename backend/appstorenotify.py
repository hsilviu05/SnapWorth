"""App Store Server Notifications V2 — Apple telling us what the client won't.

The subscription index was built from exactly one source: `/auth/entitlement`,
which only fires when the *app* runs. That made the operator's view of revenue
structurally wrong in two directions at once, and both were live:

  * A 3-day trial converted to a paid yearly on 10 Sep. The backend had last
    heard from that device on 07 Sep, so it was still holding the trial
    transaction — whose expiry had by then passed — and reported the customer
    as churned. A converted trial is precisely the person least likely to open
    the app again, so "wait for the next launch" is weakest exactly where it
    matters most.

  * A monthly subscriber never appeared at all.

Apple will just tell us. A notification is a JWS signed by the same chain as a
StoreKit transaction, with the transaction nested inside it, so this needs no
new trust anchor and no new crypto — `entitlements.verify_apple_jws` is the
same gate the entitlement path already runs on.

**This module never grants access.** It feeds the operator's index and the
Telegram alerts. Entitlement stays where it is: verified per request against
the signed transaction the client presents. That separation is deliberate —
an endpoint anyone on the internet can POST to must not be able to make
someone Pro, and this one cannot, whatever it is sent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import entitlements
from entitlements import Entitlement, EntitlementError

log = logging.getLogger("snapworth.appstorenotify")

# Apple's outer envelope carries a nested transaction and renewal info, so it
# is meaningfully larger than a bare transaction. Still bounded: this endpoint
# is unauthenticated, and an unbounded body is a free denial of service.
MAX_SIGNED_PAYLOAD = 65_536

# The notification types this deployment acts on.
#
# Apple sends many more (CONSUMPTION_REQUEST, RENEWAL_EXTENDED, PRICE_INCREASE
# …). Anything absent here is verified, acknowledged and ignored rather than
# guessed at — an unrecognised type must not be able to write a wrong row.
SUBSCRIBED = "SUBSCRIBED"
DID_RENEW = "DID_RENEW"
EXPIRED = "EXPIRED"
DID_FAIL_TO_RENEW = "DID_FAIL_TO_RENEW"
DID_CHANGE_RENEWAL_STATUS = "DID_CHANGE_RENEWAL_STATUS"
REFUND = "REFUND"
REVOKE = "REVOKE"

INDEXED_TYPES = frozenset({
    SUBSCRIBED, DID_RENEW, EXPIRED, DID_FAIL_TO_RENEW,
    DID_CHANGE_RENEWAL_STATUS, REFUND, REVOKE,
})


@dataclass(frozen=True)
class Notification:
    """One verified App Store Server Notification."""

    notification_type: str
    subtype: str | None
    # Apple's idempotency key. The same notification is redelivered until we
    # answer 2xx, so this is what stops a retry being counted twice.
    uuid: str
    entitlement: Entitlement
    signed_date: int | None = None

    @property
    def is_indexed(self) -> bool:
        return self.notification_type in INDEXED_TYPES

    @property
    def is_paid_period(self) -> bool:
        """This period is being paid for, rather than comped or trialled.

        An absent `offerType` is what separates money from an introductory
        offer, a promo offer or an offer code — the same rule
        `notify._acquisition` applies.

        Deliberately *not* called "conversion": the first paid renewal after a
        trial and the tenth ordinary renewal look identical here, because they
        are identical. Whether this is a conversion depends on what the index
        knew a moment ago, so that judgement belongs to the caller that can see
        the previous row.
        """
        return (self.notification_type in {SUBSCRIBED, DID_RENEW}
                and self.entitlement.offer_type is None
                and self.entitlement.revoked_at is None)

    @property
    def is_loss(self) -> bool:
        """The subscription stopped producing money."""
        return self.notification_type in {EXPIRED, REFUND, REVOKE}

    # The properties below exist so the operator-facing code can branch on what
    # happened without importing this module. `notify` is imported *by*
    # `entitlements`, which this module imports, so a reverse import would
    # close a cycle — and duck-typing the notification matches how
    # `notify.entitlement_recorded` already takes an entitlement.

    @property
    def is_refund(self) -> bool:
        return self.notification_type == REFUND

    @property
    def is_revoke(self) -> bool:
        return self.notification_type == REVOKE

    @property
    def is_expiry(self) -> bool:
        return self.notification_type == EXPIRED

    @property
    def is_cancellation(self) -> bool:
        """Auto-renew switched off. Not a loss yet — the period is paid for —
        but the earliest warning of one that Apple provides."""
        return (self.notification_type == DID_CHANGE_RENEWAL_STATUS
                and self.subtype == "AUTO_RENEW_DISABLED")

    @property
    def is_billing_failure(self) -> bool:
        return self.notification_type == DID_FAIL_TO_RENEW


def parse_notification(
    signed_payload: str,
    bundle_id: str,
    allowed_product_ids: set[str] | None = None,
    allowed_environments: frozenset[str] | None = None,
) -> Notification:
    """Verify Apple's `signedPayload` and return what it says.

    Raises `EntitlementError` for anything that is not a genuine, in-scope
    notification for this app. The caller turns that into a 4xx; Apple retries
    on anything non-2xx, and a forged payload is not something to retry.
    """
    payload = entitlements.verify_apple_jws(
        signed_payload, max_length=MAX_SIGNED_PAYLOAD)

    data = payload.get("data")
    if not isinstance(data, dict):
        raise EntitlementError("Notification carries no data.")

    # Checked here as well as on the nested transaction. The envelope names the
    # app independently, and a notification for someone else's bundle has no
    # business reaching the index even if the transaction inside it is valid.
    if data.get("bundleId") != bundle_id:
        raise EntitlementError("Notification is for a different app.")

    # Read through the module rather than a from-import: the default is built
    # from the environment at import time, and a copy bound here would ignore
    # both a test's monkeypatch and any later change.
    environments = (entitlements.ALLOWED_ENVIRONMENTS if allowed_environments is None
                    else allowed_environments)
    environment = data.get("environment", "Production")
    if environment not in environments:
        # Sandbox notifications are signed by the same chain as production
        # ones. Without this a TestFlight tester's renewals land in the
        # operator's revenue view as real money.
        log.warning("rejected notification from disallowed environment",
                    extra={"environment": environment,
                           "allowed": sorted(environments)})
        raise EntitlementError("Notification is from the wrong environment.")

    signed_transaction = data.get("signedTransactionInfo")
    if not isinstance(signed_transaction, str):
        raise EntitlementError("Notification carries no signed transaction.")

    # `allow_inactive`: an EXPIRED notification is *about* an expired
    # transaction. Collapsing it to FREE would leave churn indistinguishable
    # from a subscription we never heard of — which is the bug this module
    # exists to fix, in the other direction.
    ent = entitlements.verify_signed_transaction(
        signed_transaction, bundle_id, allowed_product_ids, allowed_environments,
        allow_inactive=True)

    notification_type = payload.get("notificationType")
    if not isinstance(notification_type, str) or not notification_type:
        raise EntitlementError("Notification has no type.")

    uuid = payload.get("notificationUUID")
    if not isinstance(uuid, str) or not uuid:
        raise EntitlementError("Notification has no UUID.")

    subtype = payload.get("subtype")
    signed_ms = payload.get("signedDate")

    return Notification(
        notification_type=notification_type,
        subtype=subtype if isinstance(subtype, str) and subtype else None,
        uuid=uuid,
        entitlement=ent,
        signed_date=(int(signed_ms / 1000)
                     if isinstance(signed_ms, (int, float)) else None),
    )
