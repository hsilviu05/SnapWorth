"""`/sub` and `/user`: one subscription live from Apple, and one device.

Split out of notify.py (#230). These answer a support mail: what Apple says a
subscription is doing now, the refund block the access path holds for it, and
what the server has recorded about one device. The cache is bound once at
startup by `notify.configure`; notify routes the commands here and adds its
menu to the replies.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from typing import TYPE_CHECKING

import opsformat
import opsindex

if TYPE_CHECKING:
    from cache import ResilientCache

# notify's logger, so log filters written before the split still match.
log = logging.getLogger("snapworth.notify")

_cache: ResilientCache | None = None


def bind(cache: ResilientCache | None) -> None:
    """Point /sub's refund-block reads and /user's sync line at the app's
    cache."""
    global _cache
    _cache = cache


# ── One subscription, live from Apple ────────────────────────────────────────

async def _resolve_transaction_id(wanted: str) -> tuple[str | None, str | None]:
    """Turn what the operator typed into an originalTransactionId.

    Returns `(transaction_id, error_message)` — exactly one is not None.

    Two kinds of input here, because the operator has two kinds of id to hand
    and only one of them is Apple's (the third, an order id off the customer's
    receipt, is `apple_order_id`'s):

      * an originalTransactionId, as `/subs` keys its rows and as Apple's
        own console shows it — all digits, and long;
      * a device id: the six-character `id` column from `/subs` and `/users`,
        the eight `/user` prints, or all sixteen from a support mail's
        "Device …" line — any prefix of the pseudonym.

    The device id is **not** a truncated transaction id and cannot be turned
    back into one by itself: it is `sha256(AUDIT_SALT + subject)[:16]`, a
    deliberately one-way pseudonym (`auditlog.pseudonymise`) so the audit log
    is not a device registry. What makes the lookup possible is that the subs
    index is keyed by the *full* transaction id with the devices that synced
    it stored in the row, so this is a scan, not a decode — the same reverse
    lookup `user_text` does.

    Two consequences worth stating, because both look like bugs otherwise:
    a device whose subscription only ever arrived by notification is not on
    the row at all and is unreachable this way, and a short prefix of a
    sixteen-character hash can collide.
    """
    wanted = opsformat.device_argument(wanted)
    if not wanted:
        return None, ("Usage: /sub &lt;id&gt; — an originalTransactionId, the "
                      "order ID from the customer's Apple receipt, or a device "
                      "id: the id column from /subs, or the 16 characters after "
                      "\"Device\" in a support mail.")

    # Apple's transaction ids are long decimal strings. Anything of that shape
    # is passed through untouched: the index may well not have it, which is
    # the whole point of asking Apple directly.
    #
    # Except at sixteen characters, which is also a full pseudonym. It is hex,
    # and about one device in 1,845 has one made only of digits; sent to Apple
    # as a transaction id it came back "a typo", and the index — which does
    # have it — was never asked. So a sixteen-digit id is looked for among the
    # devices first, unless it is a transaction id the index already keys, and
    # is Apple's only when no device matches.
    #
    # Matched whole, not by prefix. Nearly every real transaction id begins
    # 200000 or 100000, so one older row holding a six-character device id of
    # that shape would otherwise catch every unindexed transaction id pasted
    # here and answer with that customer's subscription.
    looks_like_transaction = wanted.isdigit() and len(wanted) >= 10
    if looks_like_transaction and len(wanted) != 16:
        return wanted, None

    doc = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
    if looks_like_transaction and wanted in doc:
        return wanted, None
    matches: dict[str, dict] = {}
    devices: set[str] = set()
    for otid, row in doc.items():
        if not isinstance(row, dict):
            continue
        hits = [d for d in opsindex.row_devices(row)
                if (d.lower() == wanted if looks_like_transaction else opsindex.same_device(d, wanted))]
        if hits:
            matches[otid] = row
            devices.update(d.lower() for d in hits)
    if not matches and looks_like_transaction:
        return wanted, None
    if not matches:
        return None, (f"💳 Nothing in the index has a device id starting "
                      f"<code>{html.escape(wanted)}</code>. If you have Apple's "
                      "originalTransactionId, or the order ID from the "
                      "customer's receipt, pass that instead — neither needs "
                      "to be in the index. /user shows whether this device "
                      "ever tried to sync a purchase, and what happened.")

    # Several rows for one device is normal — a resubscribe, or a plan change —
    # and harmless, because Apple returns every subscription belonging to the
    # customer behind whichever id we send. Several *devices* is a genuine
    # collision and the operator has to disambiguate. A six-character id kept
    # from an older row is the same device as the full one it begins.
    distinct = {d for d in devices
                if not any(o != d and o.startswith(d) for o in devices)}
    if len(distinct) > 1:
        return None, (f"💳 {len(distinct)} devices start with "
                      f"<code>{html.escape(wanted)}</code> — give more "
                      "characters: " + ", ".join(
                          html.escape(d[:8]) for d in sorted(distinct)[:6]))
    return max(matches, key=lambda otid: float(matches[otid].get("seen") or 0)), None


# An App Store order ID, as printed on the customer's receipt email ("Order
# ID: MK5TTTV8JH"): upper-case letters and digits. Checked after the other
# shapes, so an all-digit transaction id or an all-hex device id never lands
# here.
_ORDER_ID = re.compile(r"[A-Z0-9]{8,20}")


def apple_order_id(argument: str | None) -> str | None:
    """The argument as an Apple order ID, or None if it is not shaped like one.

    The one id a customer can always find: it is on the receipt Apple emails
    for every purchase, and it is what they paste when they have nothing
    else. Apple's Look Up Order ID resolves it to their transactions."""
    value = (argument or "").strip().upper()
    if (not _ORDER_ID.fullmatch(value) or value.isdigit()
            or re.fullmatch(r"[0-9A-F]+", value)):
        return None
    return value


def _status_lines(status) -> list[str]:
    """One subscription, as the operator reads it."""
    ent = status.entitlement
    lines = [f"<b>{html.escape(opsformat.plan(ent.product_id))}</b> — "
             f"{html.escape(status.state)}"]

    detail = [html.escape(ent.environment), opsindex.acquisition(ent)]
    if isinstance(ent.price, (int, float)) and ent.price > 0:
        detail.append(opsformat.money(ent.price, ent.currency))
    lines.append(" · ".join(detail))

    if status.offer_identifier:
        # Apple's offerIdentifier is the code the customer typed or the promo
        # offer's id. `opsindex.acquisition` above already says which kind it was.
        lines.append(f"Offer: <code>{html.escape(status.offer_identifier)}</code>")

    if ent.expires_at:
        # Not `.capitalize()`: it lowercases everything after the first
        # character, which turns "12 Apr 2027" into "12 apr 2027".
        phrase = opsformat.renewal_phrase(ent.expires_at, status.auto_renew)
        lines.append(phrase[0].upper() + phrase[1:])
    if status.auto_renew is False:
        lines.append("Auto-renew is <b>off</b> — this one is leaving.")
    elif status.auto_renew is None:
        lines.append("Auto-renew: Apple sent no renewal info.")
    if status.auto_renew_product_id:
        lines.append("Next period switches to "
                     f"<b>{html.escape(opsformat.plan(status.auto_renew_product_id))}</b>.")

    if ent.revoked_at:
        lines.append(f"Revoked {opsformat.date(ent.revoked_at)}.")
    if ent.original_purchase_at:
        lines.append(f"First purchased {opsformat.date(ent.original_purchase_at)}.")
    if ent.original_transaction_id:
        lines.append(f"<code>{html.escape(ent.original_transaction_id)}</code>")
    return lines


async def sub_command(rest: str) -> tuple[str, opsformat.Buttons, bool]:
    """`/sub <id>`, and `/sub <otid> lift [yes]` for a stale refund block.

    Returns the text, any buttons of its own, and whether notify should add
    its usual menu under them (every reply but the lift confirmation)."""
    parts = (rest or "").split()
    if len(parts) >= 2 and parts[1].lower() == "lift":
        confirmed = any(token.lower() == "yes" for token in parts[2:])
        return await _lift_refund_block(parts[0], confirmed)
    text, offers = await _sub_text(rest)
    return text, offers, True


async def _ask_apple(transaction_id: str) -> tuple[list | None, str | None]:
    """`appstorestatus.lookup`, with each failure in the operator's words.

    Returns `(statuses, error_message)` — exactly one is not None.
    """
    import appstorestatus

    try:
        return await appstorestatus.lookup(transaction_id), None
    except appstorestatus.StatusError as exc:
        return None, _apple_problem(exc)


async def _ask_apple_order(order_id: str) -> tuple[str | None, str | None]:
    """The transaction id behind the Order ID on a customer's App Store
    receipt, through Apple's Look Up Order ID, for `_ask_apple`.

    Returns `(transaction_id, error_message)` — exactly one is not None.
    """
    import appstorestatus

    try:
        return (await appstorestatus.lookup_order(order_id))[0], None
    except appstorestatus.StatusError as exc:
        return None, _apple_problem(exc)


def _apple_problem(exc: Exception) -> str:
    """One of `appstorestatus`'s failures, as the operator reads it."""
    import appstorestatus
    import entitlements

    if isinstance(exc, appstorestatus.OrderNotFound):
        return (f"💳 {html.escape(str(exc))}\n\nRead as an Apple order ID — the "
                "\"Order ID\" on the customer's App Store receipt email. A "
                "transaction id is all digits; a device id is hex.")
    if isinstance(exc, appstorestatus.SubscriberNotFound):
        return (f"💳 {html.escape(str(exc))}\n\nChecked Production and Sandbox. "
                "An id Apple does not recognise is usually a transactionId from "
                "a different app, or a typo.")
    if isinstance(exc, appstorestatus.StatusRetryLater):
        # Right after a purchase — when the support mail is written — Apple
        # answers "not found, retry". Reported as that, not as a typo, and
        # without Sandbox's "never heard of it" standing in for it.
        text = (f"💳 <b>Not yet — retry in a few minutes</b>\n{html.escape(str(exc))}\n\n"
                "Not necessarily a typo: Apple marks this not-found as "
                "retryable, which is what a purchase from the last few minutes "
                "looks like. ")
        if exc.environment == "Production":
            return text + "Sandbox was not asked."
        # Production's not-found was definite, so the lookup went on to
        # Sandbox, and it is Sandbox saying "not yet". That Production has
        # nothing is the useful half: this is a TestFlight or App Review
        # purchase, which this server may refuse whatever Sandbox says next.
        text += (f"Production has nothing under this id; it is "
                 f"{html.escape(exc.environment)} that says not yet.")
        if exc.environment not in entitlements.ALLOWED_ENVIRONMENTS:
            text += (f"\n⚠️ <b>This server refuses {html.escape(exc.environment)} "
                     "purchases</b> (ALLOWED_STOREKIT_ENVIRONMENTS): even once Apple "
                     "has it, the app is told free for this one. TestFlight and "
                     "App Review buy in Sandbox.")
        return text
    if isinstance(exc, appstorestatus.StatusNotConfigured):
        return (f"💳 {html.escape(str(exc))}\n\nThe rest of the bot is "
                "unaffected — /subs still reports what notifications have said.")
    if isinstance(exc, appstorestatus.StatusRateLimited):
        return f"💳 {html.escape(str(exc))}"
    if isinstance(exc, appstorestatus.StatusCredentialsRejected):
        return f"💳 <b>Credentials refused</b>\n{html.escape(str(exc))}"
    # StatusUnavailable and anything added later. Still named, still not
    # silent — this branch exists so a new subclass cannot become a
    # mystery empty reply.
    return f"💳 <b>Could not ask Apple</b>\n{html.escape(str(exc))}"


def _block_denies(tombstone: dict, ent) -> bool:
    """Whether a refund block denies `ent`'s term — `_is_revoked`'s rule."""
    blocked_until = tombstone.get("expires_at")
    if ent.expires_at is None or not isinstance(blocked_until, (int, float)):
        return True
    return ent.expires_at <= blocked_until


async def _refund_block_lines(statuses) -> tuple[list[str], opsformat.Buttons]:
    """The access path's refund blocks for these subscriptions, if any.

    A REFUND writes `entrevoked:{originalTransactionId}`, and the access path
    denies the term it names for up to 400 days. Nothing showed it: a customer
    whose refund Apple had reversed read as free on every sync, this command
    said they were paying, `/subs` said `refund`, and finding the cause took
    `redis-cli` against production. The block is shown next to what Apple
    says now, and when Apple says the term is not refunded, lifting it is
    offered — as two taps, see `_lift_refund_block`.
    """
    import entitlements

    lines: list[str] = []
    offers: opsformat.Buttons = []
    latest: dict[str, object] = {}
    for status in statuses:
        otid = status.entitlement.original_transaction_id
        if otid:
            latest.setdefault(otid, status.entitlement)
    for otid, ent in latest.items():
        code = f"<code>{html.escape(otid)}</code>"
        try:
            tombstone = await entitlements.read_revocation(_cache, otid)
        except Exception as exc:
            lines.append(f"Refund block for {code}: could not read the store "
                         f"({html.escape(type(exc).__name__)}).")
            continue
        if tombstone is None:
            continue
        until = tombstone.get("expires_at")
        what = (f"denies terms ending by {opsformat.date(int(until))}"
                if isinstance(until, (int, float)) else "denies every term")
        lines.append(f"🚫 <b>Refund block</b> on {code}: {what}.")
        if getattr(ent, "revoked_at", None) is not None:
            lines.append("Apple still shows this term refunded, so the block is right.")
        elif _block_denies(tombstone, ent):
            lines.append("Apple shows this term <b>not</b> refunded, so the block "
                         "is denying Pro to someone paying for it.")
            offers.append([("🔓 Lift refund block", f"sub {otid} lift")])
        else:
            lines.append("It does not cover the current term.")
    return lines, offers


async def _lift_refund_block(otid: str, confirmed: bool) -> tuple[str, opsformat.Buttons, bool]:
    """Delete one refund block — only once Apple says the refund is gone.

    Two taps, like `/lever`: the first names the block, the second lifts it.
    And the second asks Apple again rather than trusting the first. Lifting a
    block on a term Apple still shows refunded would let the server re-derive
    Pro from the pre-refund proof it holds, which is the bug the block exists
    to stop.
    """
    import entitlements

    otid = otid.strip()
    code = f"<code>{html.escape(otid)}</code>"
    if not (otid.isdigit() and len(otid) >= 10):
        return ("Usage: /sub &lt;originalTransactionId&gt; lift — the full id "
                "from /sub, not the short one.", [], True)
    try:
        tombstone = await entitlements.read_revocation(_cache, otid)
    except Exception as exc:
        return (f"🚫 Could not read the refund block for {code} "
                f"({html.escape(type(exc).__name__)}). Nothing was changed.",
                [], True)
    if tombstone is None:
        return f"🚫 No refund block is held for {code}.", [], True

    if not confirmed:
        until = tombstone.get("expires_at")
        what = (f"denies terms ending by {opsformat.date(int(until))}"
                if isinstance(until, (int, float)) else "denies every term")
        return (f"🔓 <b>Lift the refund block on {code}?</b>\n"
                f"It {what}. Apple is asked again first, and it is lifted only "
                "if Apple no longer shows the term refunded.",
                [[("✅ Yes, lift it", f"sub {otid} lift yes"),
                  ("Cancel", f"sub {otid}")]], False)

    statuses, problem = await _ask_apple(otid)
    if problem is not None:
        return problem + "\n\nThe refund block was left in place.", [], True
    assert statuses is not None
    mine = [st for st in statuses if st.entitlement.original_transaction_id == otid]
    if not mine:
        return (f"🚫 Apple returned nothing under {code}, so the block was left "
                "in place.", [], True)
    if any(getattr(st.entitlement, "revoked_at", None) is not None for st in mine):
        return (f"🚫 Apple still shows {code} refunded. The block was left in "
                "place: lifting it would let the server re-derive Pro from the "
                "proof it holds.", [], True)
    try:
        await entitlements.clear_revocation(_cache, otid)
    except Exception as exc:
        return (f"🚫 Could not lift the block on {code} "
                f"({html.escape(type(exc).__name__)}). Try again.", [], True)
    return (f"🔓 Refund block lifted on {code}. Pro comes back at the app's "
            "next sync, if not sooner.", [], True)


async def _sub_text(argument: str) -> tuple[str, opsformat.Buttons]:
    """Ask Apple what one subscription is doing, right now.

    Everything else the bot knows about subscriptions is a cache of what it was
    told — by a device at launch, or by a notification that may have arrived
    while the endpoint was down. This is the one command that goes and asks,
    which makes it the one worth trusting when a customer disagrees with the
    index.

    Every failure is reported in the operator's words rather than swallowed:
    "no result" and "the key is wrong" are the two answers that must never look
    alike, because one sends you to App Store Connect and the other to the
    hosting panel.
    """
    # Imported here, not at module scope. `entitlements` imports *this* module
    # (entitlements.py:38) to report what it verifies, and `appstorestatus`
    # imports `entitlements` — so either at the top of this file closes an
    # import cycle. The same reason `appstorenotify`'s header gives for
    # duck-typing the notification it is handed.
    import entitlements

    via: list[str] = []
    order_id = apple_order_id(argument)
    if order_id is None:
        transaction_id, problem = await _resolve_transaction_id(argument or "")
    else:
        transaction_id, problem = await _ask_apple_order(order_id)
        if transaction_id is not None:
            via = [f"Order <code>{html.escape(order_id)}</code> → "
                   f"<code>{html.escape(transaction_id)}</code>"]
    if problem is not None:
        return problem, []
    assert transaction_id is not None

    statuses, problem = await _ask_apple(transaction_id)
    if problem is not None:
        return problem, []
    assert statuses is not None

    lines = [f"💳 <b>Live from Apple</b> — {len(statuses)} subscription"
             f"{'s' if len(statuses) != 1 else ''}", *via]
    # The lookup asks Production first and moves on to Sandbox only when
    # Production definitely has nothing, so a Sandbox answer means both were
    # asked. Said outright: it used to be one word on the detail line.
    if statuses and all(status.environment == "Sandbox" for status in statuses):
        lines.append("Production: nothing under this id. The answer below is Sandbox's.")
    for status in statuses:
        lines.append("")
        lines.extend(_status_lines(status))
        environment = status.entitlement.environment
        if environment not in entitlements.ALLOWED_ENVIRONMENTS:
            # Apple says "active"; this server says free. `verify_signed_
            # transaction` refuses any environment outside the allowed set —
            # which is what keeps a free Sandbox tester from being production
            # Pro — so a TestFlight or App Review purchase never unlocks Pro
            # here, and "active" alone would send the operator looking for a
            # bug that is a policy.
            lines.append(
                f"⚠️ <b>This server refuses {html.escape(environment)} purchases</b> "
                "(ALLOWED_STOREKIT_ENVIRONMENTS): the app is told free for this "
                "one, and it is not indexed. TestFlight and App Review buy in Sandbox.")

    block_lines, offers = await _refund_block_lines(statuses)
    if block_lines:
        lines.append("")
        lines.extend(block_lines)

    # Fold what Apple just said back into the index. This is the only writer
    # that can correct a row which drifted — a notification that never arrived
    # leaves no trace to repair, and the device path cannot see auto-renew at
    # all.
    #
    # Gated on the environment, exactly as the notification path is: a Sandbox
    # subscription is signed identically to a production one, and writing a
    # TestFlight tester into the index puts their free renewals into /subs's
    # revenue figures.
    indexed = 0
    for status in statuses:
        if status.entitlement.environment not in entitlements.ALLOWED_ENVIRONMENTS:
            continue
        if not status.entitlement.original_transaction_id:
            continue
        try:
            # None: the index could not be read, so nothing was written, and
            # the line below must not say otherwise. `current`: this is
            # Apple's word now, so a refund it no longer shows is cleared.
            if await opsindex.index_subscription(
                    None, status.entitlement, status.auto_renew,
                    current=True) is not None:
                indexed += 1
        except Exception:
            # The answer above is the point of the command; failing to cache it
            # must not lose it.
            log.warning("could not index a live status result", exc_info=True)
    if indexed:
        lines.append("")
        lines.append(f"Index updated from this lookup ({indexed} row"
                     f"{'s' if indexed != 1 else ''}).")
    return "\n".join(lines), offers


# ── One device, for a support email ──────────────────────────────────────────

#: How /user words the last `/auth/entitlement` result it recorded.
_SYNC_WORDS = {
    "pro": "verified as Pro",
    "free": "verified, but not Pro — refunded, revoked or expired",
    "rejected": "REJECTED",
}


async def user_text(argument: str) -> str:
    wanted = opsformat.device_argument(argument)
    if not wanted:
        return ("Usage: /user &lt;id&gt; — the id column from /users or /subs, or "
                "the 16 characters after \"Device\" in a support mail.")
    users = await opsindex.read_index(opsindex.USERS_INDEX_KEY)
    matches = [(who, e) for who, e in users.items() if who.lower().startswith(wanted)]
    if not matches:
        return f"👤 No device seen with an id starting <code>{html.escape(wanted)}</code>."
    if len(matches) > 1:
        return (f"👤 {len(matches)} devices start with <code>{html.escape(wanted)}</code> — "
                "give more characters: " + ", ".join(html.escape(w[:8]) for w, _ in matches[:6]))
    (who, e), = matches
    now = time.time()
    lines = [f"👤 <b>Device {html.escape(who[:8])}</b> — {'Pro' if e.get('tier') == 'pro' else 'free'}"]
    if e.get("first"):
        lines.append(f"First seen {opsformat.date(int(e['first']))}")
    if e.get("last"):
        ago = int(now - float(e["last"]))
        when = (f"{ago // 3600}h ago" if ago < 86400 else f"{ago // 86400}d ago")
        lines.append(f"Last seen {opsformat.date(int(e['last']))} ({when})")
    lines.append(f"Scans since the bot started watching: {int(e.get('scans', 0))}")
    try:
        sync = (json.loads(await _cache.get(opsindex.sync_key(who)) or "null")
                if _cache is not None else None)
    except Exception:
        sync = None
    if isinstance(sync, list) and len(sync) >= 2:
        # What the server made of the last signed transaction this device
        # sent. When it was refused, this is the only place that says so —
        # nothing is indexed for a transaction that did not verify.
        ago = int(now - float(sync[0]))
        when = (f"{ago // 3600}h ago" if ago < 86400 else f"{ago // 86400}d ago")
        what = _SYNC_WORDS.get(str(sync[1]), html.escape(str(sync[1])))
        if len(sync) > 2 and sync[2]:
            what += f" — {html.escape(str(sync[2]))}"
        lines.append(f"Last purchase sync: {opsformat.date(int(sync[0]))} ({when}) — {what}")
    subs = [(otid, s) for otid, s in (await opsindex.read_index(opsindex.SUBS_INDEX_KEY)).items()
            if isinstance(s, dict) and any(opsindex.same_device(d, who) for d in opsindex.row_devices(s))]
    for otid, s in subs:
        # `_sub_is_alive`, like the two readers of this same index in `/subs`.
        # This one tested expiry alone, so a refunded subscription — which
        # keeps its expiry — read "renews 12 Mar 2027" here while `/subs`
        # showed the same row as `refund`.
        alive = opsformat.sub_is_alive(s, now)
        renews = opsformat.date(int(s["expires"])) if s.get("expires") else "never"
        if s.get("revoked") is not None:
            state = f"refunded or revoked {opsformat.date(int(s['revoked']))}"
        elif alive and s.get("auto_renew") is False:
            # Same distinction the digest now draws, for the same reason: this
            # subscription is paid up and leaving. Saying "renews" here is the
            # single most misleading thing this command could print during the
            # support mail it exists for.
            state = f"ends {renews} (auto-renew off)"
        elif alive:
            state = f"renews {renews}"
        else:
            state = f"ended {renews}"
        # The transaction id is what Apple, App Store Connect and /sub all
        # take, so it is printed where it can be tapped and copied.
        lines.append(f"Subscription: {opsformat.plan(s.get('product'))} · "
                     f"{s.get('acq') or '?'} · {state} · <code>{html.escape(str(otid))}</code>")
    if not subs:
        lines.append("No subscription has synced from this device.")
    lines.append("Devices, not people — this is the audit log's pseudonym.")
    return "\n".join(lines)
