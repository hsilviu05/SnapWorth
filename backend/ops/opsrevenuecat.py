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

`/revenue rcimport users` does the same for devices that never subscribed:
each device in `opsidx:users` not already on a subscription row becomes a
RevenueCat customer, created by fetching it (`GET /v1/subscribers/{id}`
creates one that does not exist). Its ID is the same kind of hash, of the
device pseudonym, so it carries nothing RevenueCat could link to the device
and a second run finds the customers the first created. These customers are
never merged with the ones the SDK makes on the same phones, because nothing
connects the two IDs: from 1.8.1 on a device can appear twice.

Like the rest of the operator view this never grants or withdraws access.
Both calls take the app's public iOS key (`appl_…`), the same one
`Config.swift` ships: RevenueCat refuses a secret key on them (403, code 7243,
"Secret API keys should not be used in your app"). Public by design, so it is
a constant here; `REVENUECAT_PUBLIC_KEY` overrides it if the app's key changes.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
from collections import Counter
from urllib.parse import quote

import httpx

from ops import opsformat
from ops import opsindex

log = logging.getLogger("snapworth.notify")

RECEIPTS_URL = "https://api.revenuecat.com/v1/receipts"
SUBSCRIBERS_URL = "https://api.revenuecat.com/v1/subscribers/"
KEY_ENV = "REVENUECAT_PUBLIC_KEY"
# Not a secret: it is in every copy of the app. Must match `Config.revenueCatAPIKey`.
PUBLIC_KEY = "appl_VmTUodlaqELRoxdSRzuGJfcRbnO"
TIMEOUT_SECONDS = 20.0
# Between subscriptions: two API calls each, and Apple's status endpoint is
# rate limited per key, shared with /sub.
PAUSE_SECONDS = 0.25
APP_USER_PREFIX = "$RCAnonymousID:"


def app_user_id(original_transaction_id: str) -> str:
    """The RevenueCat customer an imported subscription is filed under."""
    digest = hashlib.sha256(f"snapworth-rc-import:{original_transaction_id}".encode())
    return APP_USER_PREFIX + digest.hexdigest()[:32]


def device_app_user_id(pseudonym: str) -> str:
    """The RevenueCat customer a device that never subscribed is filed under."""
    digest = hashlib.sha256(f"snapworth-rc-user:{pseudonym}".encode())
    return APP_USER_PREFIX + digest.hexdigest()[:32]


def free_devices(users: dict, subs: dict) -> list[str]:
    """Device pseudonyms in the users index that no subscription row names,
    oldest first. Those on a row are the subscription import's."""
    subscribed = [d for row in subs.values() if isinstance(row, dict)
                  for d in opsindex.row_devices(row)]
    rows = [(who, row) for who, row in users.items()
            if isinstance(who, str) and who and isinstance(row, dict)
            and not any(opsindex.same_device(d, who) for d in subscribed)]
    rows.sort(key=lambda item: item[1].get("first") or 0)
    return [who for who, _ in rows]


def production_transaction_ids(doc: dict) -> list[str]:
    """The originalTransactionIds worth importing, oldest first."""
    rows = [(otid, row) for otid, row in doc.items()
            if isinstance(otid, str) and otid and isinstance(row, dict)
            and row.get("env") == "Production"]
    rows.sort(key=lambda item: item[1].get("first") or 0)
    return [otid for otid, _ in rows]


async def post_receipt(client: httpx.AsyncClient, secret: str, *,
                       app_user: str, jws: str, product_id: str | None) -> tuple[int, str]:
    """POST one signed transaction. Returns the HTTP status, and RevenueCat's
    own error text when it refused (empty on success)."""
    body: dict = {"app_user_id": app_user, "fetch_token": jws}
    if product_id:
        body["product_id"] = product_id
    response = await client.post(
        RECEIPTS_URL, json=body,
        headers={"Authorization": f"Bearer {secret}", "X-Platform": "ios",
                 "Content-Type": "application/json"})
    if 200 <= response.status_code < 300:
        return response.status_code, ""
    return response.status_code, refusal_detail(response)


async def create_customer(client: httpx.AsyncClient, secret: str,
                          app_user: str) -> tuple[int, str]:
    """Fetch one customer, which creates it if RevenueCat has none. Same
    return as `post_receipt`."""
    response = await client.get(
        SUBSCRIBERS_URL + quote(app_user, safe=""),
        headers={"Authorization": f"Bearer {secret}", "X-Platform": "ios"})
    if 200 <= response.status_code < 300:
        return response.status_code, ""
    return response.status_code, refusal_detail(response)


def api_key_problem() -> tuple[str, str | None]:
    """The public iOS key, or the reply that says why the import cannot run."""
    key = (os.environ.get(KEY_ENV) or "").strip() or PUBLIC_KEY
    if not key.startswith("appl_"):
        return "", (f"🐈 Import did not run: {KEY_ENV} is not the app's public iOS "
                    "key (appl_…). RevenueCat refuses secret keys here.")
    return key, None


def refusal_detail(response: httpx.Response) -> str:
    """RevenueCat's reason, short enough for a chat line. Its error body is
    `{"code": ..., "message": ...}`; anything else falls back to the raw text."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and (body.get("message") or body.get("code")):
        code = body.get("code")
        message = str(body.get("message") or "").strip()
        detail = f"{code}: {message}" if code is not None else message
    else:
        detail = (response.text or "").strip()
    return " ".join(detail.split())[:200]


async def import_subscriptions(*, limit: int | None = None,
                               client: httpx.AsyncClient | None = None) -> str:
    """Send each indexed Production subscription to RevenueCat once.

    Returns the operator's reply. Never raises for a single subscription: a
    lookup or a post that fails is counted and the run goes on, because the
    rest are still worth sending and a second run is safe.
    """
    from billing import appstorestatus

    secret, problem = api_key_problem()
    if problem:
        return problem
    if appstorestatus.credentials_from_env() is None:
        return "🐈 Import did not run: no App Store Server API credentials on this deployment."

    otids = production_transaction_ids(await opsindex.read_index(opsindex.SUBS_INDEX_KEY))
    if limit is not None:
        otids = otids[:limit]

    sent = not_found = lookup_failed = rejected = 0
    reasons: Counter[str] = Counter()
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
                code, detail = await post_receipt(
                    client, secret, app_user=app_user_id(otid),
                    jws=status.signed_transaction or "", product_id=status.product_id)
            except httpx.HTTPError as exc:
                rejected += 1
                reasons[f"unreachable ({type(exc).__name__})"] += 1
                log.warning("rcimport: RevenueCat unreachable (%s)", type(exc).__name__)
                continue
            if code == 429:
                rate_limited = True
                break
            if 200 <= code < 300:
                sent += 1
            else:
                rejected += 1
                reasons[f"HTTP {code}" + (f" — {detail}" if detail else "")] += 1
                log.warning("rcimport: RevenueCat answered HTTP %s: %s", code, detail)
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
    for reason, count in reasons.most_common(3):
        lines.append(f"RevenueCat said ({count}×): {html.escape(reason)}")
    return "\n".join(lines)


async def import_users(*, limit: int | None = None,
                       client: httpx.AsyncClient | None = None) -> str:
    """Create a RevenueCat customer for each indexed device that never
    subscribed. Returns the operator's reply; never raises for one device."""
    secret, problem = api_key_problem()
    if problem:
        return problem
    devices = free_devices(await opsindex.read_index(opsindex.USERS_INDEX_KEY),
                           await opsindex.read_index(opsindex.SUBS_INDEX_KEY))
    if limit is not None:
        devices = devices[:limit]

    sent = rejected = 0
    reasons: Counter[str] = Counter()
    rate_limited = False
    owned = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        for index, who in enumerate(devices):
            if index:
                await asyncio.sleep(PAUSE_SECONDS)
            try:
                code, detail = await create_customer(client, secret, device_app_user_id(who))
            except httpx.HTTPError as exc:
                rejected += 1
                reasons[f"unreachable ({type(exc).__name__})"] += 1
                log.warning("rcimport users: RevenueCat unreachable (%s)", type(exc).__name__)
                continue
            if code == 429:
                rate_limited = True
                break
            if 200 <= code < 300:
                sent += 1
            else:
                rejected += 1
                reasons[f"HTTP {code}" + (f" — {detail}" if detail else "")] += 1
                log.warning("rcimport users: RevenueCat answered HTTP %s: %s", code, detail)
    finally:
        if owned:
            await client.aclose()

    lines = [f"🐈 <b>RevenueCat users import</b>: {len(devices)} devices without a subscription",
             f"{sent} sent" + (f" · {rejected} refused by RevenueCat" if rejected else "")]
    if rate_limited:
        lines.append("⚠️ Stopped early: rate limited. Run it again in a minute; "
                     "devices already sent are found, not duplicated.")
    for reason, count in reasons.most_common(3):
        lines.append(f"RevenueCat said ({count}×): {html.escape(reason)}")
    return "\n".join(lines)


async def rcimport_command(rest: str) -> tuple[str, opsformat.Buttons]:
    """`/revenue rcimport [users] [test|yes]`: two taps, as every bot command that writes.

    `test` sends the oldest subscription only, to check the keys before the
    whole index goes."""
    parts = [p.lower() for p in (rest or "").split()[1:]]
    if "users" in parts:
        if "yes" in parts:
            return await import_users(), []
        if "test" in parts:
            return await import_users(limit=1), [
                [("✅ Send the rest", "revenue rcimport users yes"), ("Cancel", "revenue")]]
        return ("🐈 <b>Send devices without a subscription to RevenueCat?</b>\n"
                "Creates one RevenueCat customer for each device in /users that is not "
                "on a subscription, named by a one-way hash. RevenueCat cannot join these "
                "to the customers the app creates from 1.8.1, so a device can appear "
                "twice. Safe to run again. Try one first.",
                [[("🧪 Send one", "revenue rcimport users test"),
                  ("✅ Send all", "revenue rcimport users yes")],
                 [("Cancel", "revenue")]])
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
             [("👥 Users without a subscription", "revenue rcimport users")],
             [("Cancel", "revenue")]])
