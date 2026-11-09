"""`/revenue rcimport`: hand RevenueCat the subscriptions it has not seen.

The app reports a purchase to RevenueCat itself from 1.8.1 on, and a
subscriber who opens 1.8.1 is picked up by the SDK at launch. What neither
covers is a subscriber who has not updated, or whose subscription ended before
1.8.1 existed. Their charts would start empty and fill in only as people
update, so this replays them once, from the operator chat.

For every Production row of `opsidx:subs`, Apple's App Store Server API is
asked for the subscription's current signed transaction, and that JWS goes to
RevenueCat's `POST /v1/receipts`. RevenueCat verifies it against Apple itself
(it holds the In-App Purchase key), so nothing here vouches for it. Rows are
the operator index's, so this reaches what the server has seen in the last
400 days, capped at its 500 rows.

The app user ID is `$RCAnonymousID:` plus a hash of the originalTransactionId.
Anonymous on purpose: when the same purchase later arrives from the SDK under
the phone's own anonymous ID, RevenueCat aliases the two customers rather than
transferring the purchase between them. A fixed function of the transaction,
so a second run posts to the same customers and RevenueCat deduplicates it.

Like the rest of the operator view this never grants or withdraws access.
Needs `REVENUECAT_SECRET_KEY`, a v1 secret key, which never leaves the server.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os

import httpx

from ops import opsformat
from ops import opsindex

log = logging.getLogger("snapworth.notify")

RECEIPTS_URL = "https://api.revenuecat.com/v1/receipts"
SECRET_ENV = "REVENUECAT_SECRET_KEY"
TIMEOUT_SECONDS = 20.0
# Between subscriptions: two API calls each, and Apple's status endpoint is
# rate limited per key, shared with /sub.
PAUSE_SECONDS = 0.25
APP_USER_PREFIX = "$RCAnonymousID:"


def app_user_id(original_transaction_id: str) -> str:
    """The RevenueCat customer an imported subscription is filed under."""
    digest = hashlib.sha256(f"snapworth-rc-import:{original_transaction_id}".encode())
    return APP_USER_PREFIX + digest.hexdigest()[:32]


def production_transaction_ids(doc: dict) -> list[str]:
    """The originalTransactionIds worth importing, oldest first."""
    rows = [(otid, row) for otid, row in doc.items()
            if isinstance(otid, str) and otid and isinstance(row, dict)
            and row.get("env") == "Production"]
    rows.sort(key=lambda item: item[1].get("first") or 0)
    return [otid for otid, _ in rows]


async def post_receipt(client: httpx.AsyncClient, secret: str, *,
                       app_user: str, jws: str, product_id: str | None) -> int:
    """POST one signed transaction. Returns the HTTP status."""
    body: dict = {"app_user_id": app_user, "fetch_token": jws}
    if product_id:
        body["product_id"] = product_id
    response = await client.post(
        RECEIPTS_URL, json=body,
        headers={"Authorization": f"Bearer {secret}", "X-Platform": "ios",
                 "Content-Type": "application/json"})
    return response.status_code


async def import_subscriptions(*, limit: int | None = None,
                               client: httpx.AsyncClient | None = None) -> str:
    """Send each indexed Production subscription to RevenueCat once.

    Returns the operator's reply. Never raises for a single subscription: a
    lookup or a post that fails is counted and the run goes on, because the
    rest are still worth sending and a second run is safe.
    """
    from billing import appstorestatus

    secret = (os.environ.get(SECRET_ENV) or "").strip()
    if not secret:
        return (f"🐈 Import did not run: no {SECRET_ENV} on this deployment. "
                "Add a v1 secret key from RevenueCat → Project settings → API keys.")
    if not secret.startswith("sk_"):
        return f"🐈 Import did not run: {SECRET_ENV} is not a secret key (sk_…)."
    if appstorestatus.credentials_from_env() is None:
        return "🐈 Import did not run: no App Store Server API credentials on this deployment."

    otids = production_transaction_ids(await opsindex.read_index(opsindex.SUBS_INDEX_KEY))
    if limit is not None:
        otids = otids[:limit]

    sent = not_found = lookup_failed = rejected = 0
    rate_limited = False
    owned = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        for index, otid in enumerate(otids):
            if index:
                await asyncio.sleep(PAUSE_SECONDS)
            try:
                statuses = await appstorestatus.lookup(otid)
            except appstorestatus.SubscriberNotFound:
                not_found += 1
                continue
            except appstorestatus.StatusRateLimited:
                rate_limited = True
                break
            except appstorestatus.StatusError as exc:
                lookup_failed += 1
                log.warning("rcimport: Apple lookup failed: %s", exc)
                continue

            mine = [s for s in statuses
                    if s.signed_transaction and s.environment == "Production"
                    and s.entitlement.original_transaction_id == otid]
            if not mine:
                not_found += 1
                continue
            status = mine[0]
            try:
                code = await post_receipt(
                    client, secret, app_user=app_user_id(otid),
                    jws=status.signed_transaction or "", product_id=status.product_id)
            except httpx.HTTPError as exc:
                rejected += 1
                log.warning("rcimport: RevenueCat unreachable (%s)", type(exc).__name__)
                continue
            if code == 429:
                rate_limited = True
                break
            if 200 <= code < 300:
                sent += 1
            else:
                rejected += 1
                log.warning("rcimport: RevenueCat answered HTTP %s", code)
    finally:
        if owned:
            await client.aclose()

    lines = [f"🐈 <b>RevenueCat import</b>: {len(otids)} subscriptions from the index",
             f"{sent} sent"
             + (f" · {not_found} unknown to Apple" if not_found else "")
             + (f" · {lookup_failed} Apple lookups failed" if lookup_failed else "")
             + (f" · {rejected} refused by RevenueCat" if rejected else "")]
    if rate_limited:
        lines.append("⚠️ Stopped early: rate limited. Run it again in a minute; "
                     "anything already sent is deduplicated.")
    if rejected:
        lines.append("Refusals are usually a missing In-App Purchase key in RevenueCat, "
                     "or a v2 key in REVENUECAT_SECRET_KEY.")
    return "\n".join(lines)


async def rcimport_command(rest: str) -> tuple[str, opsformat.Buttons]:
    """`/revenue rcimport [test|yes]`: two taps, as every bot command that writes.

    `test` sends the oldest subscription only, to check the keys before the
    whole index goes."""
    parts = [p.lower() for p in (rest or "").split()[1:]]
    if "yes" in parts:
        return await import_subscriptions(), []
    if "test" in parts:
        return await import_subscriptions(limit=1), [
            [("✅ Import the rest", "revenue rcimport yes"), ("Cancel", "revenue")]]
    return ("🐈 <b>Send existing subscriptions to RevenueCat?</b>\n"
            "Asks Apple for each Production subscription in /subs and posts its "
            "signed transaction to RevenueCat, once. Safe to run again: RevenueCat "
            "deduplicates. Try one first.",
            [[("🧪 Send one", "revenue rcimport test"),
              ("✅ Send all", "revenue rcimport yes")],
             [("Cancel", "revenue")]])
