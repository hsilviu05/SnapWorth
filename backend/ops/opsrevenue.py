"""`/revenue`: money over time, which `/subs` cannot show.

`/subs` is a picture of now: MRR from the subscription index as it stands. It
has no memory, and the daily counters behind `/week` and `/paywall` expire
after 35 days, so "is MRR going up?" and "how many paying customers did we
lose last month?" had no answer in the bot.

Two kinds of record, both aggregate — no row here names a device or a
subscription, which is why they can outlive the index's 400 days:

  * A daily snapshot of `/subs`'s summary (`rev:snap:<YYYYMMDD>`), taken once
    a day by the digest. MRR history is read from these.
  * Monthly counters (`rev:<YYYYMM>:<name>`), bumped from App Store Server
    Notifications: what customers paid, what was refunded, and paid
    subscriptions that ended. Each notification reaches here once — the
    handler's idempotency claim runs first — so a redelivery cannot count
    twice.

Money is kept per currency, in cents, because the cache's counters are
integers and Apple charges each customer in their own storefront's currency.
It is the customer price: before Apple's commission and before VAT, so it is
not proceeds. App Store Connect's Sales and Trends has proceeds.

Like the rest of the operator view this never grants or withdraws access, and
every write is best-effort: a lost count never fails Apple's delivery.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from ops import opsformat
from ops import opsindex

if TYPE_CHECKING:
    from core.cache import ResilientCache

log = logging.getLogger("snapworth.notify")

# Aggregates only, so kept long enough for a year-on-year comparison with
# room to spare.
REVENUE_TTL = 60 * 60 * 24 * 800

# How long a counted notification is remembered, so the live route and a
# backfill never count the same one twice. Above Apple's 180-day history.
SEEN_TTL = 60 * 60 * 24 * 200

# How far back `/revenue backfill` asks Apple: the most it keeps in Production.
BACKFILL_DAYS = 180

# Months `/revenue` lists, newest first.
MONTHS_SHOWN = 6

# Apple's EXPIRED subtypes, grouped the way churn is acted on: a customer who
# chose to leave, and a card that stopped working, need different fixes.
VOLUNTARY = "VOLUNTARY"
BILLING_RETRY = "BILLING_RETRY"

_cache: ResilientCache | None = None


def bind(cache: ResilientCache | None) -> None:
    """Point the revenue records at the app's cache. None makes them no-ops."""
    global _cache
    _cache = cache


def month(at: datetime | None = None) -> str:
    return (at or datetime.now(timezone.utc)).strftime("%Y%m")


def month_key(month: str, name: str) -> str:
    return f"rev:{month}:{name}"


def snap_key(day: str) -> str:
    return f"rev:snap:{day}"


def _cents(amount: object) -> int | None:
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
        return None
    return int(round(float(amount) * 100))


def _currency(ent) -> str | None:
    currency = getattr(ent, "currency", None)
    # A currency becomes part of a key. Apple sends ISO 4217; anything else is
    # not stored rather than trusted.
    if isinstance(currency, str) and len(currency) == 3 and currency.isalpha():
        return currency.upper()
    return None


async def _bump(name: str, amount: int = 1, at: datetime | None = None) -> None:
    if _cache is None:
        return
    await _cache.incr(month_key(month(at), name), REVENUE_TTL, amount)


async def _add_money(prefix: str, ent, at: datetime | None = None) -> None:
    cents, currency = _cents(getattr(ent, "price", None)), _currency(ent)
    if cents is None or currency is None:
        return
    await _bump(f"{prefix}:{currency}", cents, at)
    await _add_currency(currency, at)


async def _add_currency(currency: str, at: datetime | None) -> None:
    """Remember which currencies a month has money in. The cache cannot list
    keys, so without this a reader would have to guess the currency codes."""
    if _cache is None:
        return
    key = month_key(month(at), "currencies")
    raw = await _cache.get(key)
    try:
        known = set(json.loads(raw or "[]"))
    except Exception:
        known = set()
    if currency in known:
        return
    known.add(currency)
    await _cache.set(key, json.dumps(sorted(known)), REVENUE_TTL)


async def record_notification(note, at: datetime | None = None) -> bool:
    """Count one App Store Server Notification into this month. Never raises.

    `note` is duck-typed, like everything `notify.subscription_event` takes.
    The caller has already dropped Sandbox and anything not indexed.

    The month is the one Apple signed the notification in, when it says, so
    a backfilled notification lands in its own month rather than today's.
    Returns whether it was counted: False for one already counted, by the
    live route or an earlier backfill.
    """
    if _cache is None:
        return False
    try:
        ent = note.entitlement
        if ent is None:
            return False
        signed = getattr(note, "signed_date", None)
        if at is None and isinstance(signed, (int, float)) and not isinstance(signed, bool):
            at = datetime.fromtimestamp(signed, timezone.utc)
        uuid = getattr(note, "uuid", None)
        if uuid and not await _cache.add(f"rev:n:{uuid}", "1", SEEN_TTL):
            return False
        if note.is_paid_period:
            # Every charge: a direct purchase, a trial's first paid period,
            # an ordinary renewal. They are all money in.
            await _add_money("gross", ent, at)
            await _bump("payments", at=at)
        elif note.is_refund:
            await _add_money("refunds", ent, at)
            await _bump("refund_count", at=at)
        elif note.is_expiry and opsindex.acquisition(ent) == "paid":
            # The last period was paid for, so this is a paying customer gone.
            # A trial that ends unconverted is not churn: it never paid.
            await _bump("churned", at=at)
            subtype = getattr(note, "subtype", None)
            if subtype == VOLUNTARY:
                await _bump("churned_voluntary", at=at)
            elif subtype == BILLING_RETRY:
                await _bump("churned_billing", at=at)
        elif note.is_cancellation and opsindex.acquisition(ent) == "paid":
            await _bump("cancellations", at=at)
        return True
    except Exception as exc:
        log.warning("revenue counter failed for %s: %s",
                    getattr(note, "notification_type", "?"), type(exc).__name__)
        return False


async def backfill(now: datetime | None = None, days: int = BACKFILL_DAYS) -> str:
    """Replay Apple's notification history into the monthly counters.

    The same notifications the live route counts, verified the same way, and
    guarded by the same per-notification key, so running it twice — or after
    the live route has counted some of them — counts nothing twice. What it
    cannot rebuild is the daily MRR snapshot, so a backfilled month has
    revenue, refunds and churn but no churn rate.
    """
    from billing import appstorenotify, appstorestatus
    from billing.entitlements import EntitlementError

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    try:
        credentials = appstorestatus.credentials_from_env()
        if credentials is None:
            raise appstorestatus.StatusNotConfigured(
                "No App Store Server API credentials on this deployment.")
        payloads, truncated = await appstorestatus.notification_payloads(
            int(start.timestamp() * 1000), int(now.timestamp() * 1000))
    except appstorestatus.StatusError as exc:
        return f"📈 Backfill did not run: {exc}"

    counted = already = skipped = rejected = 0
    for payload in payloads:
        try:
            note = appstorenotify.parse_notification(payload, credentials.bundle_id)
        except EntitlementError:
            rejected += 1
            continue
        if (not note.is_indexed or note.entitlement is None
                or opsindex.is_bounded(note.entitlement)):
            skipped += 1
            continue
        if await record_notification(note):
            counted += 1
        else:
            already += 1

    lines = [f"📈 <b>Backfill</b> {start.strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')}: "
             f"{len(payloads)} notifications from Apple",
             f"{counted} counted · {already} already counted · "
             f"{skipped} not revenue (tests, other types)"
             + (f" · {rejected} did not verify" if rejected else "")]
    if truncated:
        lines.append("⚠️ Stopped at the page limit: run it again to continue "
                     "from where the counts now stand.")
    lines.append("Backfilled months have no churn rate: there is no snapshot of "
                 "who was paying on their 1st.")
    return "\n".join(lines)


async def revenue_command(argument: str, rest: str) -> tuple[str, opsformat.Buttons]:
    """`/revenue`, and `/revenue backfill [yes]`: two taps, as every bot
    command that writes."""
    parts = (rest or "").split()
    if argument == "backfill":
        if any(p.lower() == "yes" for p in parts[1:]):
            return await backfill(), []
        return ("📈 <b>Fill in past months from Apple?</b>\n"
                f"Replays the last {BACKFILL_DAYS} days of App Store notifications "
                "into /revenue. Safe to run more than once: nothing is counted twice.",
                [[("✅ Yes, backfill", "revenue backfill yes"), ("Cancel", "revenue")]])
    return await revenue_text(), [[("⏪ Backfill from Apple", "revenue backfill")]]


async def snapshot(now: datetime | None = None) -> bool:
    """Record today's `/subs` summary, once a day. Never raises.

    `add`, so the first writer of the day wins and a second replica or a
    catch-up does nothing. Returns whether this call wrote it.
    """
    if _cache is None:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        doc = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
        if not doc:
            # An unreadable or empty index is not "zero subscribers", and a
            # zero written now would sit in the history as a crash in MRR.
            return False
        active, paid, comped, _, mrr = opsindex.subs_summary(doc)
        row = {"active": active, "paid": paid, "comped": comped,
               "mrr": {c: round(v, 2) for c, v in mrr.items()},
               "capped": len(doc) >= opsindex.SUBS_INDEX_CAP}
        return await _cache.add(snap_key(now.strftime("%Y%m%d")),
                                json.dumps(row, separators=(",", ":")), REVENUE_TTL)
    except Exception as exc:
        log.warning("revenue snapshot failed: %s", type(exc).__name__)
        return False


async def read_snapshot(day: datetime) -> dict | None:
    if _cache is None:
        return None
    try:
        raw = await _cache.get(snap_key(day.strftime("%Y%m%d")))
        doc = json.loads(raw) if raw else None
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


async def _snapshot_near(day: datetime, within_days: int = 3) -> dict | None:
    """The snapshot for `day`, or the nearest later one within a few days: a
    day the digest did not run should not blank a comparison."""
    for offset in range(within_days + 1):
        snap = await read_snapshot(day + timedelta(days=offset))
        if snap is not None:
            return snap
    return None


async def _read_int(key: str) -> int:
    if _cache is None:
        return 0
    try:
        return int(await _cache.get(key) or 0)
    except Exception:
        return 0


async def _money(month_: str, prefix: str) -> dict[str, float]:
    if _cache is None:
        return {}
    try:
        currencies = json.loads(await _cache.get(month_key(month_, "currencies")) or "[]")
    except Exception:
        currencies = []
    out: dict[str, float] = {}
    for currency in currencies if isinstance(currencies, list) else []:
        if not isinstance(currency, str):
            continue
        cents = await _read_int(month_key(month_, f"{prefix}:{currency}"))
        if cents:
            out[currency] = cents / 100
    return out


def _money_text(amounts: dict[str, float]) -> str:
    if not amounts:
        return "0"
    return " + ".join(opsformat.money(v, c) for c, v in sorted(amounts.items()))


def _month_start(at: datetime) -> datetime:
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _previous_month(at: datetime) -> datetime:
    return _month_start(_month_start(at) - timedelta(days=1))


def churn_rate(churned: int, paid_at_start: int | None) -> float | None:
    """Churned paying subscriptions over those paying when the month began.

    None when the start is unknown or zero: a rate over nothing is not 0%.
    """
    if not paid_at_start:
        return None
    return churned / paid_at_start


async def month_line(start: datetime) -> str | None:
    """One month's line, or None for a month with nothing recorded."""
    m = month(start)
    gross = await _money(m, "gross")
    refunds = await _money(m, "refunds")
    payments = await _read_int(month_key(m, "payments"))
    refund_count = await _read_int(month_key(m, "refund_count"))
    churned = await _read_int(month_key(m, "churned"))
    voluntary = await _read_int(month_key(m, "churned_voluntary"))
    billing = await _read_int(month_key(m, "churned_billing"))
    cancellations = await _read_int(month_key(m, "cancellations"))
    if not (gross or refunds or payments or churned or cancellations):
        return None

    parts = [f"<b>{start.strftime('%b %Y')}</b>: {_money_text(gross)} from {payments} "
             f"payment{'s' if payments != 1 else ''}"]
    if refund_count:
        parts.append(f"refunds {_money_text(refunds)} ({refund_count})")
    start_snap = await _snapshot_near(start)
    paid_at_start = start_snap.get("paid") if start_snap else None
    rate = churn_rate(churned, paid_at_start if isinstance(paid_at_start, int) else None)
    churn = f"churned {churned}"
    if rate is not None:
        churn += f" of {paid_at_start} paid ({rate:.1%})"
    if churned:
        why = []
        if voluntary:
            why.append(f"{voluntary} chose to leave")
        if billing:
            why.append(f"{billing} card failed")
        if why:
            churn += f" — {', '.join(why)}"
    parts.append(churn)
    if cancellations:
        parts.append(f"{cancellations} turned off auto-renew")
    return " · ".join(parts)


async def revenue_text(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    doc = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
    _, paid, _, _, mrr = opsindex.subs_summary(doc)
    lines = ["📈 <b>Revenue</b>",
             f"MRR now ≈ {_money_text(mrr) if mrr else 'n/a'} · {paid} paid"]

    history = []
    for label, days in (("7 days ago", 7), ("30 days ago", 30), ("90 days ago", 90)):
        snap = await read_snapshot(now - timedelta(days=days))
        if snap is None:
            continue
        then = snap.get("mrr") if isinstance(snap.get("mrr"), dict) else {}
        history.append(f"{label} {_money_text(then) if then else 'n/a'} · "
                       f"{snap.get('paid', '?')} paid")
    if history:
        lines.extend(history)
    else:
        lines.append("No earlier snapshot yet: the digest takes one a day, so "
                     "comparisons start appearing after a week.")

    months = []
    start = _month_start(now)
    for _ in range(MONTHS_SHOWN):
        line = await month_line(start)
        if line:
            months.append(line)
        start = _previous_month(start)
    lines.append("")
    if months:
        lines.extend(months)
    else:
        lines.append("No App Store notification has been counted yet.")

    lines.append("")
    lines.append("Customer prices, before Apple's commission and VAT. Churn is "
                 "paid subscriptions that ended, over those paying on the 1st. "
                 "Proceeds and retention cohorts: App Store Connect.")
    if len(doc) >= opsindex.SUBS_INDEX_CAP:
        lines.append(f"⚠️ The subscription index is at its {opsindex.SUBS_INDEX_CAP}-row "
                     "cap, so MRR and the paid count leave out the least recently "
                     "seen subscriptions.")
    return "\n".join(lines)
