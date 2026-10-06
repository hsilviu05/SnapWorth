"""The operator's two indexes, in the shared cache: subscriptions and devices.

Split out of notify.py (#230). `opsidx:subs` is every subscription the server
has seen, keyed on `originalTransactionId`; `opsidx:users` is every device, by
the audit log's pseudonym. `/subs`, `/users`, `/sub`, `/user`, `/costs` and
the digest read them; `/auth/entitlement`, App Store Server Notifications and
each scan write them. This module holds the documents and the rules for
writing them, and needs the cache and nothing else, bound once at startup by
`notify.configure`. How they are shown to the operator stays in notify.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import auditlog
import opsstats

if TYPE_CHECKING:
    from cache import ResilientCache

# notify's logger, so log filters written before the split still match.
log = logging.getLogger("snapworth.notify")

# The two operator tables. Each is one JSON document the cache can hand back
# whole — it cannot enumerate keys — bounded so a write never grows past a
# few hundred kilobytes. There are no accounts: "users" are pseudonymous
# devices, exactly as the audit log identifies them.
SUBS_INDEX_KEY = "opsidx:subs"
USERS_INDEX_KEY = "opsidx:users"
SUBS_INDEX_CAP = 500
USERS_INDEX_CAP = 500
INDEX_TTL = 60 * 60 * 24 * 400
# How long /user can say what the last purchase sync from a device came to.
# A subscriber's app re-syncs at every cold launch, so this is the horizon
# for a device that stopped opening the app.
SYNC_TTL = 60 * 60 * 24 * 90
# Devices remembered per subscription row, most recent last. Above the
# entitlement device cap (MAX_DEVICES_PER_SUBSCRIPTION, 6 by default) so a
# household's phones and the ones they replaced all still resolve.
SUB_DEVICES_CAP = 10

# Apple's offerType values.
OFFER_INTRODUCTORY, OFFER_PROMOTIONAL, OFFER_CODE = 1, 2, 3

_cache: ResilientCache | None = None


def bind(cache: ResilientCache | None) -> None:
    """Point the indexes at the app's cache. None leaves reads empty and
    writes no-ops."""
    global _cache
    _cache = cache


async def read_index(key: str) -> dict:
    if _cache is None:
        return {}
    try:
        doc = json.loads(await _cache.get(key) or "{}")
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


async def write_index(key: str, doc: dict, cap: int, recency: str) -> None:
    # A row goes once it has been untouched for INDEX_TTL, which is the "up to
    # 400 days" the privacy policy states. The document's own TTL cannot do
    # that: every write renews it, so on a service that is used daily a row
    # written once would otherwise stay until the cap pushed it out.
    cutoff = time.time() - INDEX_TTL
    for stale in [k for k, v in doc.items()
                  if not isinstance(v, dict)
                  or not isinstance(v.get(recency), (int, float))
                  or v[recency] < cutoff]:
        doc.pop(stale, None)
    if len(doc) > cap:
        # Drop the least recently seen until it fits.
        for stale in sorted(doc, key=lambda k: doc[k].get(recency, 0))[:len(doc) - cap]:
            doc.pop(stale, None)
    if _cache is None:
        return
    await _cache.set(key, json.dumps(doc, separators=(",", ":")), INDEX_TTL)


def same_device(recorded: str, wanted: str) -> bool:
    """Whether an id someone typed names this recorded device.

    Both are prefixes of one sixteen-character pseudonym, so either may be
    the longer: the operator types six characters from /subs, or pastes all
    sixteen from a support mail — and rows written before the full pseudonym
    was stored hold only six."""
    recorded, wanted = recorded.lower(), wanted.lower()
    return bool(recorded and wanted) and (recorded.startswith(wanted)
                                          or wanted.startswith(recorded))


def with_device(devices: list, who: str) -> list[str]:
    """`devices` with `who` moved to the end (most recent), capped.

    A six-character id kept from an older row is the same device as the full
    pseudonym it begins, and is dropped in its favour."""
    kept = [d for d in devices
            if isinstance(d, str) and d and d != who and not who.startswith(d)]
    return [*kept, who][-SUB_DEVICES_CAP:]


def row_devices(row: dict) -> list[str]:
    """Every device id a subscription row knows, legacy `who` included."""
    found = [d for d in (row.get("devices") or []) if isinstance(d, str) and d]
    who = row.get("who")
    if isinstance(who, str) and who and who not in found:
        found.append(who)
    return found


def is_bounded(ent) -> bool:
    """A Sandbox entitlement production honours on bounded terms: not a customer.

    See `entitlements.SANDBOX_ENTITLEMENTS`. Imported here rather than at the
    top: entitlements imports notify, which imports this module.
    """
    import entitlements
    return entitlements.is_bounded(ent)


def acquisition(ent) -> str:
    """How a subscription was obtained, in the operator's words."""
    offer = getattr(ent, "offer_type", None)
    discount = getattr(ent, "offer_discount_type", None)
    if offer == OFFER_CODE:
        return "offer code"
    if offer == OFFER_PROMOTIONAL:
        return "promo offer"
    if offer == OFFER_INTRODUCTORY:
        return "trial" if discount == "FREE_TRIAL" else "intro offer"
    return "paid"


async def index_subscription(subject: str | None, ent,
                              auto_renew: bool | None = None, *,
                              current: bool = False) -> dict | None:
    """Record what we now know about one subscription. Returns the previous row,
    or None when the index could not be read and nothing was written.

    `subject` is None when App Store Server Notifications told us rather than a
    device checking in. There is no pseudonymised device to attribute it to,
    and — this is the point — the existing `who` must survive: the row may
    already name the device that first synced it, and overwriting that with
    nothing would lose the only link between a payment and a person.

    `auto_renew` is the same shape of argument and for the same reason. It
    lives in Apple's `signedRenewalInfo`, which only two of the three writers
    ever see: a notification carries one, a live status lookup fetches one, and
    `/auth/entitlement` — the highest-volume writer by far — does not, because
    the client presents a signed *transaction* and nothing else. None means
    "this writer cannot see it", so the stored value survives. Without that,
    every app launch would erase a cancellation the moment Apple reported it.

    The previous row is returned because a notification alone cannot say
    whether a paid period is a *conversion*. Only the row it replaces can.

    `current` says `ent` is Apple's word on this term as of now — a live
    status lookup, or a REFUND_REVERSED — rather than a transaction that may
    have been signed before a refund and delivered after it. See the refund
    mark below.

    A bounded Sandbox entitlement is never written, whoever calls. This is the
    one writer every path shares, so the rule lives here as well as at each
    caller: `/subs`, MRR and the digest's subscriber line all read this index,
    and a tester in it is revenue that does not exist.
    """
    if is_bounded(ent):
        return {}
    doc = await opsstats.read_doc_for_update(SUBS_INDEX_KEY)
    if doc is None:
        # Nothing written, and nothing known about the row. `/sub` can repair
        # it once Redis is back.
        return None
    otid = str(ent.original_transaction_id)
    before: dict = row if isinstance(row := doc.get(otid), dict) else {}
    entry: dict = dict(before)
    entry.update({
        "product": ent.product_id, "env": ent.environment,
        "first": getattr(ent, "original_purchase_at", None),
        "expires": ent.expires_at,
        "acq": acquisition(ent),
        "price": getattr(ent, "price", None), "currency": getattr(ent, "currency", None),
        "seen": int(time.time()),
    })
    # Write-once, because `acq` above is not: it is the current transaction's,
    # so a converted trial reads "paid" exactly like a direct purchase and the
    # row alone could not say a trial had converted (#218). A row from before
    # this field takes its last `acq`, which is how it started unless a later
    # kind has already overwritten it.
    if not before.get("started_as"):
        entry["started_as"] = before.get("acq") or entry["acq"]
        if entry["started_as"] == "trial" and "trial_ends" not in before:
            # When the free period runs out, so `/paywall` can ask of the
            # trials that ended in its window how many paid. Only knowable
            # from the trial's own transaction: the next one is the paid
            # period's. None for an older row that has already converted.
            entry["trial_ends"] = (ent.expires_at if entry["acq"] == "trial"
                                   else None)
    if subject is not None:
        # The full pseudonym, and every device that has synced this
        # subscription, not just the last. `who` used to be the first six
        # characters of whichever device synced most recently: a support mail
        # carries all sixteen ("Device 3f2a…" from the in-app form, the
        # `support_id` /auth/token hands the app), and a family's second phone
        # was overwritten by the first on every launch — so /sub refused the
        # id the customer sent and /user told every other device that nothing
        # had ever synced from it.
        who = auditlog.pseudonymise(subject)
        entry["devices"] = with_device(row_devices(entry), who)
        entry["who"] = who
    if auto_renew is not None:
        entry["auto_renew"] = auto_renew
    # The revocation is a tombstone on a *term*, not on the row.
    #
    # This only ever set `revoked` and never cleared it, and the row is keyed
    # on `originalTransactionId` — which Apple keeps stable across renewals
    # *and* re-subscriptions. So one refund tombstoned the row permanently: a
    # customer who refunded in March and paid again in June stayed out of the
    # active count, out of the paid count and out of MRR for the 400-day life
    # of the index, while `/subs` showed their live subscription as `refund`.
    #
    # `entitlements._is_revoked` had to solve exactly this on the access path
    # and stores the revoked term's own expiry so a later, longer-dated term
    # survives the tombstone. The operator's index gets the same rule, rather
    # than clearing on any non-revoked transaction — Apple can redeliver a
    # pre-refund renewal after the REFUND, and that must not resurrect the row.
    #
    # That rule alone left no way to clear the mark on the *same* term, which
    # is exactly what a reversed refund needs: the row said `refund` for a
    # customer paying for that term again, and `/sub` asking Apple could not
    # fix it. A `current` transaction is not a redelivery, so it may.
    revoked = getattr(ent, "revoked_at", None)
    if revoked is not None:
        entry["revoked"] = revoked
        entry["revoked_expires"] = ent.expires_at
    else:
        tombstoned = entry.get("revoked_expires")
        if (entry.get("revoked") is not None
                and ent.expires_at is not None
                and tombstoned is not None
                and (float(ent.expires_at) >= float(tombstoned) if current
                     else float(ent.expires_at) > float(tombstoned))):
            entry.pop("revoked", None)
            entry.pop("revoked_expires", None)
    doc[otid] = entry
    await write_index(SUBS_INDEX_KEY, doc, SUBS_INDEX_CAP, "seen")
    return before


#: Days of per-device Pro scan counts a users-index row keeps: `/costs`' window.
PRO_DAYS_KEPT = 30


async def index_user(who: str, *, tier: str, scanned: bool = False) -> None:
    """Upsert one device's row in the users index.

    `scans` is lifetime and `tier` is only the current one, so neither can say
    what a subscriber costs: a device that scanned 200 times free and then
    subscribed would read as 200 Pro scans. `pro_since` opens a Pro span when
    the device is first seen Pro, `pro_until` closes it when it is next seen
    free, and only scans inside a span count toward `pro_scans` and
    `pro_days` (Pro scans per UTC day, the last PRO_DAYS_KEPT days). A new
    span replaces a closed one: `/costs` looks back 30 days, not further.
    Rows from before these fields existed start their span at the first
    sighting after the deploy, since what came earlier cannot be split.
    """
    doc = await opsstats.read_doc_for_update(USERS_INDEX_KEY)
    if doc is None:
        return
    now = int(time.time())
    entry: dict = row if isinstance(row := doc.get(who), dict) else {"first": now, "scans": 0}
    entry["last"] = now
    pro = tier == "pro"
    entry["tier"] = "pro" if pro else "free"
    if pro and (not entry.get("pro_since") or entry.get("pro_until")):
        entry["pro_since"] = now
        entry.pop("pro_until", None)
    elif not pro and entry.get("pro_since") and not entry.get("pro_until"):
        entry["pro_until"] = now
    if scanned:
        entry["scans"] = int(entry.get("scans", 0)) + 1
        if pro:
            entry["pro_scans"] = int(entry.get("pro_scans", 0)) + 1
            today = opsstats.day()
            oldest = opsstats.day(datetime.now(timezone.utc) - timedelta(days=PRO_DAYS_KEPT - 1))
            days = d if isinstance(d := entry.get("pro_days"), dict) else {}
            days = {d: n for d, n in days.items() if d >= oldest}
            days[today] = int(days.get(today, 0)) + 1
            entry["pro_days"] = days
    doc[who] = entry
    await write_index(USERS_INDEX_KEY, doc, USERS_INDEX_CAP, "last")


def sync_key(who: str) -> str:
    return f"opsstate:sync:{who}"


async def note_sync(subject: str, outcome: str, detail: str | None = None) -> None:
    """Record what `/auth/entitlement` made of this device's last signed
    transaction: "pro", "free" (verified, not entitled) or "rejected" with
    the reason. Never raises.

    The subscription index only ever hears about a transaction that
    verified, so a refused one left no trace the operator could find: the
    customer who paid and was told free had, as far as the bot knew, never
    tried. `/user` shows this line. A key per device rather than a field in
    the devices index, so it is one plain write that cannot lose, or be
    lost to, the index's read-modify-write."""
    if _cache is None:
        return
    try:
        record = [int(time.time()), outcome] + ([detail[:160]] if detail else [])
        await _cache.set(sync_key(auditlog.pseudonymise(subject)), json.dumps(record), SYNC_TTL)
    except Exception as exc:
        log.debug("entitlement sync note failed: %s", type(exc).__name__)
