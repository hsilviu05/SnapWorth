"""`/trials`: how each free trial ended, and why, from what is already kept.

`/paywall` counts trials and the share that paid. It cannot say why the rest
did not: whether they cancelled on day one, used the app and still left, or
meant to pay and had a card declined. Those need different fixes (price or
trial length, the product, Billing Grace Period), so this lists every trial of
the last TRIALS_WINDOW_DAYS with:

  * the outcome: paying, still in trial, cancelled, card failed, in grace;
  * how many hours after starting it auto-renew went off, when it did;
  * the Pro scans its devices made during the free period.

Everything comes from the subscription index (Apple's notifications and the
app's syncs) and the devices index (`pro_days`, the last 30 days of Pro scans
per device). Rows from before `renew_off_at` and `billing_failed_at` were kept
show "?" for the timing rather than a guess.
"""

from __future__ import annotations

import html
import statistics
import time
from datetime import datetime, timezone

from ops import opsformat, opsindex, opsstats

TRIALS_WINDOW_DAYS = 30

# Outcomes, in the order the summary lists them.
PAID, ACTIVE, CANCELLING, GRACE, BILLING, CANCELLED, ENDED = (
    "paid", "in trial", "cancelling", "in grace", "card failed", "cancelled", "ended")


def outcome(row: dict, now: float) -> str:
    """What became of one trial."""
    if row.get("revoked") is not None:
        return CANCELLED
    if row.get("acq") == "paid":
        return PAID
    grace = row.get("grace_until")
    if isinstance(grace, (int, float)) and grace > now and row.get("billing_failed_at"):
        return GRACE
    if opsformat.sub_is_alive(row, now):
        return CANCELLING if row.get("auto_renew") is False else ACTIVE
    if row.get("billing_failed_at"):
        return BILLING
    if row.get("auto_renew") is False:
        return CANCELLED
    # Ended with auto-renew on and no failure recorded: Apple's EXPIRED
    # usually follows a billing retry, but a row from before failures were
    # kept cannot say.
    return ENDED


def hours_to_cancel(row: dict) -> float | None:
    off, first = row.get("renew_off_at"), row.get("first")
    if not isinstance(off, (int, float)) or not isinstance(first, (int, float)):
        return None
    return max(0.0, (off - first) / 3600)


def trial_scans(row: dict, users: dict) -> int | None:
    """Pro scans by the trial's devices between its start and its end.

    None when no device is known (Apple told us before any device synced),
    or the trial is older than the devices index's 30 days of `pro_days`.
    """
    first = row.get("first")
    if not isinstance(first, (int, float)):
        return None
    end = row.get("trial_ends")
    end = end if isinstance(end, (int, float)) else first + 3 * 86400
    start_day = opsstats.day(datetime.fromtimestamp(first, timezone.utc))
    end_day = opsstats.day(datetime.fromtimestamp(end, timezone.utc))
    devices = opsindex.row_devices(row)
    if not devices:
        return None
    total, seen = 0, False
    for who, user in users.items():
        if not isinstance(user, dict) or not any(opsindex.same_device(d, who) for d in devices):
            continue
        days = user.get("pro_days")
        if isinstance(days, dict):
            seen = True
            total += sum(int(n) for d, n in days.items()
                         if start_day <= d <= end_day and isinstance(n, (int, float)))
    return total if seen else None


def _hours(h: float | None) -> str:
    if h is None:
        return "?"
    return f"{h:.0f}h" if h < 48 else f"{h / 24:.0f}d"


async def trials_text(now: float | None = None) -> str:
    now = time.time() if now is None else now
    subs = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
    users = await opsindex.read_index(opsindex.USERS_INDEX_KEY)
    since = now - TRIALS_WINDOW_DAYS * 86400
    trials = [r for r in subs.values()
              if isinstance(r, dict) and r.get("started_as") == "trial"
              and isinstance(r.get("first"), (int, float)) and r["first"] >= since]
    lines = [f"🧪 <b>Trials — last {TRIALS_WINDOW_DAYS} days</b>"]
    if not trials:
        lines.append("No trial started in the window.")
        return "\n".join(lines)
    trials.sort(key=lambda r: -float(r["first"]))

    rows = [(r, outcome(r, now), hours_to_cancel(r), trial_scans(r, users)) for r in trials]
    counts: dict[str, int] = {}
    for _, o, _, _ in rows:
        counts[o] = counts.get(o, 0) + 1
    order = (PAID, ACTIVE, CANCELLING, GRACE, BILLING, CANCELLED, ENDED)
    lines.append(f"{len(rows)} started: " + " · ".join(
        f"{counts[o]} {o}" for o in order if counts.get(o)))

    decided = [r for r in rows if r[1] in (PAID, BILLING, CANCELLED, ENDED)]
    if decided:
        paid = counts.get(PAID, 0)
        lines.append(f"Trial → paid: {paid} of {len(decided)} decided "
                     f"({paid / len(decided):.0%}, n={len(decided)})")
    cancel_hours = [h for _, o, h, _ in rows if o in (CANCELLED, CANCELLING) and h is not None]
    if cancel_hours:
        within_day = sum(1 for h in cancel_hours if h < 24)
        lines.append(f"Cancelled after: median {_hours(statistics.median(cancel_hours))} · "
                     f"{within_day} of {len(cancel_hours)} within the first day")
    for label, wanted in (("paid", (PAID,)), ("cancelled", (CANCELLED, CANCELLING))):
        scans = [s for _, o, _, s in rows if o in wanted and s is not None]
        if scans:
            lines.append(f"Scans in trial, {label}: median {statistics.median(scans):g} "
                         f"(n={len(scans)})")
    failed = counts.get(BILLING, 0) + counts.get(GRACE, 0)
    if failed:
        lines.append(f"💳 {failed} ended on a declined card, not a choice: "
                     "Billing Grace Period gives them 16 days to fix it.")

    header = f"{'start':<7} {'plan':<7} {'outcome':<11} {'off':<4} {'scans':<5} {'id':<6}"
    body = [header]
    for r, o, h, s in rows[:25]:
        body.append(
            f"{opsformat.short_date(int(r['first'])):<7} "
            f"{opsformat.plan(r.get('product')):<7} {o:<11} "
            f"{(_hours(h) if o in (CANCELLED, CANCELLING) else '—'):<4} "
            f"{('?' if s is None else str(s)):<5} {str(r.get('who') or '')[:6]:<6}")
    if len(rows) > 25:
        body.append(f"… and {len(rows) - 25} more")
    lines.append("<pre>" + html.escape("\n".join(body)) + "</pre>")
    lines.append("off = hours from start to auto-renew off · scans = Pro scans during "
                 "the trial (30 days kept) · ? = not recorded for older rows")
    return "\n".join(lines)


async def cancel_detail(otid: str) -> str | None:
    """For the live "auto-renew turned off" alert, when the row is a trial
    still in its free period: how long it ran and what it was used for."""
    subs = await opsindex.read_index(opsindex.SUBS_INDEX_KEY)
    row = subs.get(otid)
    if not isinstance(row, dict) or row.get("started_as") != "trial" or row.get("acq") != "trial":
        return None
    users = await opsindex.read_index(opsindex.USERS_INDEX_KEY)
    scans = trial_scans(row, users)
    return (f"🧪 Trial cancelled after {_hours(hours_to_cancel(row))} · "
            f"{'?' if scans is None else scans} Pro scans so far")
