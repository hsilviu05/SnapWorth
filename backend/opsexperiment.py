"""`/experiment` and `/paywall`: the operator's readouts of the free-scan
experiment and of which paywall starts trials and purchases.

Split out of notify.py (#230). Both read the daily counters and the levers
document and send nothing themselves; the export reads its counters straight
from the cache with `required=True`, so it is bound once at startup by
`notify.configure`, and notify routes the commands here and adds its menu.
"""

from __future__ import annotations

import dataclasses
import html
import os
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import levers
import opsindex
import opsstats
from opsstats import STATS_TTL

if TYPE_CHECKING:
    from cache import ResilientCache

_cache: ResilientCache | None = None


def bind(cache: ResilientCache | None) -> None:
    """Point the export's counter reads at the app's cache."""
    global _cache
    _cache = cache


# The free-scan experiment, A-6 in docs/AUDIT-2026-09.md. `FREE_SCANS_FIRST_DAY`
# was armed 2026-09-07, but until 1.3.6 shipped a spent allowance was filed as a
# scan failure (I-23) and the client's own count could be wrong three separate
# ways (I-3, I-4, I-5) — so the window opens on approval day, not arming day.
EXPERIMENT_START_DAY = os.environ.get("EXPERIMENT_START_DAY", "20260910")
EXPERIMENT_END_DAY = os.environ.get("EXPERIMENT_END_DAY", "20260924")
# `limit_hits` only began counting at 18:29 UTC on this day, so that column
# covers about five and a half hours of it while every other column is a whole
# day. Marked in the table rather than dropped: the row is real, and the mark is
# what stops it being read as a full day's figure.
EXPERIMENT_PARTIAL_DAY = os.environ.get("EXPERIMENT_PARTIAL_DAY", "20260910")

# `/paywall`'s window. Inside STATS_TTL, so every day in it is still readable.
PAYWALL_WINDOW_DAYS = 28


def _parse_day(day: str) -> datetime | None:
    try:
        return datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _day_span(start: datetime, end: datetime) -> list[str]:
    """Every day from start to end inclusive, oldest first."""
    out, cur = [], start
    while cur <= end:
        out.append(opsstats.day(cur))
        cur += timedelta(days=1)
    return out


# The partial day's footnote, shared by the table and the export so the two
# cannot disagree about what that row is.
EXPERIMENT_PARTIAL_NOTE = ("limit hits counted from 18:29 UTC that day only — the "
                           "counter shipped mid-day")

# The counters `/experiment` shows, in its column order. The export's header
# uses these names as they are, so a kept copy can be traced back to the code.
#
# `new_subs` stays beside the three that split it (#218): before the split it
# is the only record of a day's subscriptions, and a kept copy without it
# would read those days as none.
EXPERIMENT_COUNTERS = ("active_users", "scans_free", "limit_hits",
                       "trial_starts", "trial_conversions", "paid_direct", "new_subs")


def _stat_expired(day: str, now: datetime) -> bool:
    """Whether a day's counters are past STATS_TTL, so a 0 read for it is an
    absence rather than a count."""
    dt = _parse_day(day)
    return dt is not None and (now - dt).days >= STATS_TTL // 86400


def _stat_expires_on(day: str) -> datetime:
    """When `_stat_expired` starts to hold for `day`, a YYYYMMDD from
    `_day_span`."""
    return (datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc)
            + timedelta(days=STATS_TTL // 86400))


def _lever_changes_in(days: list[str], doc: dict) -> list[list]:
    """The changes recorded in levers document `doc` that fall on one of
    `days`, oldest first.

    Handed the document rather than reading it, because the two callers read
    it differently: the table best-effort, the export `required`.
    """
    return [c for c in (doc.get("changes") or [])
            if isinstance(c, list) and len(c) == 3 and c[0] in days]


async def experiment_text(now: datetime | None = None) -> str:
    """The experiment's server-side half, whole window at once.

    The digest reports one day at a time, which answers "what happened
    yesterday" and not "is this working" — for that the operator would have to
    scroll back through a fortnight of messages and add them up by hand. This is
    the running total, and since issue #125 was closed it is the only automated
    read of the experiment: the client's half is a dashboard someone has to
    remember to open.

    Deliberately not a conversion claim. Trial starts and purchases beside
    `limit_hits` are a coincidence within a window, not an attribution — nothing here knows whether
    the person who subscribed is the one who hit the limit. TelemetryDeck holds
    the per-user path. These are the totals, and the value of two instruments is
    that they can disagree.
    """
    now = now or datetime.now(timezone.utc)
    start, end = _parse_day(EXPERIMENT_START_DAY), _parse_day(EXPERIMENT_END_DAY)
    if start is None or end is None or end < start:
        return ("\U0001F9EA <b>Free-scan experiment</b>\n"
                "Window misconfigured — EXPERIMENT_START_DAY and "
                "EXPERIMENT_END_DAY must both be YYYYMMDD, end on or after start.")

    today = opsstats.day(now)
    if today < EXPERIMENT_START_DAY:
        off = (start.date() - now.date()).days
        return (f"\U0001F9EA <b>Free-scan experiment</b>\nWindow opens "
                f"{start:%d %b} — {off} day{'s' if off != 1 else ''} from now. "
                "Nothing counted yet.")

    span = _day_span(start, end)
    shown = [d for d in span if d <= today]
    # A day's counters carry STATS_TTL from their last write, so a window read
    # long after it closed reports zeros that are really absences. Say which.
    ttl_days = STATS_TTL // 86400

    # Literal spaces between the columns, not just field widths: a number wider
    # than its column would otherwise run into its neighbour and the row would
    # be unreadable without anything reporting a problem.
    rows = [f"<code>{'day':<6}{'act':>6} {'free':>5} {'hit':>5} "
            f"{'trial':>5} {'paid':>5}</code>"]
    hits = trials = paid = started = unsplit = free_scans = expired = 0
    partial = False
    for d in shown:
        label = f"{d[4:6]}-{d[6:]}"
        if _stat_expired(d, now):
            expired += 1
            rows.append(
                f"<code>{label:<6}{'—':>6} {'—':>5} {'—':>5} {'—':>5} {'—':>5}</code>")
            continue
        act = await opsstats.read_stat(d, "active_users")
        fr = await opsstats.read_stat(d, "scans_free")
        hi = await opsstats.read_stat(d, "limit_hits")
        tr, cv, dr = await opsstats.sub_counts([d])
        # A day from before #218 has `new_subs` and nothing splitting it, so
        # its subscriptions are in neither column. `new_subs` is every first
        # sighting since, so any excess over its parts is that day's.
        rest = max(0, await opsstats.read_stat(d, "new_subs") - tr - dr
                   - await opsstats.read_stat(d, opsstats.OFFER_STARTS))
        hits += hi
        trials += tr
        paid += cv + dr
        started += tr + dr + rest
        unsplit += rest
        free_scans += fr
        mark = ""
        if d == EXPERIMENT_PARTIAL_DAY:
            partial, mark = True, " *"
        if rest:
            mark += " †"
        rows.append(
            f"<code>{label:<6}{act:>6} {fr:>5} {hi:>5} {tr:>5} {cv + dr:>5}</code>{mark}")

    closed = today > EXPERIMENT_END_DAY
    left = max(0, (end.date() - now.date()).days)
    head = ("\U0001F9EA <b>Free-scan experiment</b> — "
            + (f"closed after {len(span)} days" if closed
               else f"day {len(shown)} of {len(span)}"))
    # What the quota grants a new user now, asked of the quota. This read the
    # environment only, so arming from chat left it saying "lever not armed"
    # for the whole window; then it read both and printed them raw, so
    # FREE_SCANS_FIRST_DAY=1 — no welcome at a daily limit of 1 — read as armed.
    lever = levers.welcome_html(await levers.welcome_setting())
    window = (f"{start:%d %b} → {end:%d %b}"
              + ("" if closed else f" · {left} day{'s' if left != 1 else ''} left")
              + f" · {lever}")

    # Every total is over the days that could actually be read. Saying "no limit
    # hits" about a day whose counters have expired would be a claim the data
    # cannot support — and the footnote contradicting the headline is worse than
    # either alone.
    readable = len(shown) - expired
    scope = (f" across {readable} readable day{'s' if readable != 1 else ''}"
             if expired else "")
    if hits:
        total = (f"<b>{hits} limit hit{'s' if hits != 1 else ''} · {trials} "
                 f"trial start{'s' if trials != 1 else ''} · {paid} paid "
                 f"({100.0 * started / hits:.0f}%)</b>{scope}")
    elif not readable:
        total = ("<b>Nothing readable</b> — every day in the window is past the "
                 f"{ttl_days}-day counter TTL.")
    elif free_scans and not partial:
        total = (f"<b>No limit hits</b>{scope} — {free_scans} free scan"
                 f"{'s' if free_scans != 1 else ''}, none of which spent the "
                 "day's allowance.")
    elif free_scans:
        # "None spent the allowance" is an inference from hits == 0, and it only
        # holds if hits were counted over the same hours as the scans. On the
        # partial day they were not: the scans are a whole day and the hits are
        # the tail of one, so an allowance spent that morning would print as
        # nobody spending one. Report the count and let the mark carry the rest.
        total = (f"<b>No limit hits recorded</b>{scope} — {free_scans} free scan"
                 f"{'s' if free_scans != 1 else ''} in the window.")
    else:
        total = f"<b>No limit hits</b>{scope} — and no free scans recorded yet."

    notes = []
    # A window whose lever moved mid-flight and does not say so is worse than
    # no window: the numbers look continuous and are not.
    for day_changed, before, after in _lever_changes_in(shown, await levers.read())[-4:]:
        notes.append(f"⚠️ lever changed on {day_changed[4:6]}-{day_changed[6:]}: "
                     f"{levers.lever_label(before)} → {levers.lever_label(after)}")
    if partial:
        notes.append(f"* {EXPERIMENT_PARTIAL_NOTE}. Every other column is a whole day.")
    if unsplit:
        notes.append(f"† {unsplit} trial start{'s' if unsplit != 1 else ''} or "
                     "purchase" + ("s" if unsplit != 1 else "") + " from before "
                     "the server split them (#218): in neither column, and in the %.")
    if hits:
        notes.append("% is trial starts and direct purchases ÷ limit hits "
                     "across the window — coincidence, not attribution.")
    if expired:
        notes.append(f"— {expired} day{'s' if expired != 1 else ''} older than the "
                     f"{ttl_days}-day counter TTL: those figures are gone, not zero.")
    if readable:
        # The record deletes itself a day at a time; say when, while there is
        # still something to keep.
        oldest = next(d for d in shown if not _stat_expired(d, now))
        gone = _stat_expires_on(oldest)
        notes.append(f"💾 The {oldest[4:6]}-{oldest[6:]} counters expire on "
                     f"{gone:%d %b} — /experiment export gives a copy to keep.")

    return "\n".join([head, window, *rows, total, *notes])


def _csv_comment(text: str) -> str:
    """A `#` line for the export that a CSV parser reads as one field.

    CSV has no comment syntax, so a comma in a note split it into cells — the
    welcome line has several — and the block saved as a .csv read as ragged
    rows ahead of its real header. The notes stay inside the block, because
    the partial day and any lever move have to travel with the rows, and lose
    their commas instead: to " · ", and semicolons too, which a spreadsheet
    in a comma-decimal locale splits on. Not quoted: a quoted line starts with
    `"`, and a reader told to skip `#` lines would no longer skip it.
    """
    return "# " + re.sub(r"\s*[,;]\s*", " · ", text)


async def experiment_export(now: datetime | None = None) -> str:
    """`/experiment export`: the window's table as CSV, to keep.

    The counters behind `/experiment` carry STATS_TTL, so the server's record
    of the window deletes itself a day at a time — for the default window,
    20260910's row goes on 2026-10-15 and the rest over the fortnight after —
    and the daily digests that reported it are one day each. This is the one
    form of it that outlives the cache: a block to copy into `docs/`.

    Read `required`, unlike the table: the counters and the lever's record
    both. A zero in a kept copy is a claim that nothing happened, and so is a
    copy with no lever move in it, so an unreadable cache refuses the export
    rather than writing either. An expired day has empty cells, not zeros, for
    the reason the table prints "—".
    """
    now = now or datetime.now(timezone.utc)
    start, end = _parse_day(EXPERIMENT_START_DAY), _parse_day(EXPERIMENT_END_DAY)
    if start is None or end is None or end < start:
        return ("💾 Nothing exported — the window is misconfigured: "
                "EXPERIMENT_START_DAY and EXPERIMENT_END_DAY must both be "
                "YYYYMMDD, end on or after start.")
    today = opsstats.day(now)
    shown = [d for d in _day_span(start, end) if d <= today]
    if not shown:
        return f"💾 Nothing to export — the window opens {start:%d %b}."

    ttl_days = STATS_TTL // 86400
    rows = [",".join(["day", *EXPERIMENT_COUNTERS, "note"])]
    cache = _cache
    try:
        if cache is None:
            raise RuntimeError("no cache bound")
        # The lever's record as well as the counters. `levers.read()` on its own
        # turns a failed read into {}, and a kept copy built from that shows no
        # lever move: the window that `levers._set_free_scan_lever` records changes
        # so as never to produce.
        lever_doc = await levers.read(required=True)
        for d in shown:
            iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            if _stat_expired(d, now):
                rows.append(iso + "," * len(EXPERIMENT_COUNTERS)
                            + f",expired: past the {ttl_days}-day counter TTL")
                continue
            values = []
            for name in EXPERIMENT_COUNTERS:
                raw = await cache.get(opsstats.stat_key(d, name), required=True)
                values.append(str(int(raw or 0)))
            note = EXPERIMENT_PARTIAL_NOTE if d == EXPERIMENT_PARTIAL_DAY else ""
            rows.append(",".join([iso, *values, note]))
    except Exception as exc:
        return ("💾 <b>Nothing exported</b> — the counters or the lever's record "
                f"could not be read ({html.escape(type(exc).__name__)}), and a "
                "copy without them would say nothing happened. Try again in a "
                "minute.")

    setting = await levers.welcome_setting()
    if setting is not None:
        # The quota reads the lever best-effort, as a scan must, and an
        # unreadable one reads as the environment's value. In a kept copy that
        # would be the environment's welcome while the lever said otherwise.
        # So the override is the one just read `required`, through the parse
        # `levers.free_scan_lever` hands the quota; what it grants is still the
        # quota's `allowance`.
        setting = dataclasses.replace(setting, override=levers.lever_value(lever_doc))
    _, head, why = levers.welcome_summary(setting)
    lines = [_csv_comment(f"SnapWorth free-scan experiment · {start:%Y-%m-%d} to "
                          f"{end:%Y-%m-%d} · exported {now:%Y-%m-%d %H:%M} UTC"
                          + ("" if today > EXPERIMENT_END_DAY
                             else " while the window was open")),
             _csv_comment(f"welcome at export: {head} — {why}")]
    for day_changed, before, after in _lever_changes_in(shown, lever_doc):
        lines.append(_csv_comment(
            f"lever changed {day_changed[:4]}-{day_changed[4:6]}-{day_changed[6:]}: "
            f"{levers.lever_label(before)} -> {levers.lever_label(after)}"))
    lines.extend(rows)

    kept = [d for d in shown if not _stat_expired(d, now)]
    expiry = (f"The {kept[0][4:6]}-{kept[0][6:]} counters expire on "
              f"{_stat_expires_on(kept[0]):%d %b}, the rest a day at a time after."
              if kept else "Every day in it is already past the counter TTL.")
    csv = html.escape("\n".join(lines))
    return ("💾 <b>Free-scan experiment — export</b>\n"
            f"Copy the block into <code>docs/</code> to keep it. {expiry}\n"
            f"<pre>{csv}</pre>")


# ── Paywall readout ──────────────────────────────────────────────────────────

async def paywall_text(now: datetime | None = None) -> str:
    """`/paywall`: the server's half of reading which paywall sells (#218).

    Trial starts and direct purchases per trigger over PAYWALL_WINDOW_DAYS,
    and the trial-to-paid rate for the trials whose free period ended in that
    window. Per trigger is counts only: the trigger is never on the row, so
    trial-to-paid *by trigger* cannot be read here (#218, Notes).

    Counts, not significance: at a few scans a day every figure here is small,
    which is why each rate carries its n. TelemetryDeck's
    `paywall_viewed → purchase_started → purchase_completed` is the other
    instrument, and it has the views this cannot see.
    """
    now = now or datetime.now(timezone.utc)
    days = [opsstats.day(now - timedelta(days=i)) for i in range(PAYWALL_WINDOW_DAYS)]
    trials, conversions, direct = await opsstats.sub_counts(days)

    per: list[tuple[str, int, int]] = []
    for trigger in opsstats.PAYWALL_TRIGGERS:
        t = await opsstats.sum_stat(days, f"{opsstats.START_COUNTERS['trial']}:{trigger}")
        p = await opsstats.sum_stat(days, f"{opsstats.START_COUNTERS['paid']}:{trigger}")
        if t or p:
            per.append((trigger, t, p))
    per.sort(key=lambda row: (-(row[1] + row[2]), row[0]))

    lines = [f"💳 <b>Paywall — last {PAYWALL_WINDOW_DAYS} days</b>",
             f"Trial starts: {trials} · direct purchases: {direct} · "
             f"converted trials: {conversions}"]
    if per:
        lines.append(f"<code>{'trigger':<17}{'trial':>6}{'direct':>7}</code>")
        lines += [f"<code>{html.escape(t):<17}{ts:>6}{pd:>7}</code>"
                  for t, ts, pd in per]
    untagged_t = max(0, trials - sum(r[1] for r in per))
    untagged_p = max(0, direct - sum(r[2] for r in per))
    if untagged_t or untagged_p:
        lines.append(f"<code>{'no trigger':<17}{untagged_t:>6}{untagged_p:>7}</code>")
        lines.append("No trigger: a build before 1.5.2, a sync that was not the "
                     "one after the paywall, or Apple reported it first.")
    elif not per:
        lines.append("No trial starts or direct purchases in the window.")

    # The rate is read off the subscription index rather than the counters:
    # a trial that ends in the window may have started before it, and only
    # its row says when its free period ran out and whether it paid.
    start = (now - timedelta(days=PAYWALL_WINDOW_DAYS)).timestamp()
    ended = converted = 0
    for row in (await opsindex.read_index(opsindex.SUBS_INDEX_KEY)).values():
        if not isinstance(row, dict) or row.get("started_as") != "trial":
            continue
        ends = row.get("trial_ends")
        if not isinstance(ends, (int, float)) or not start <= ends < now.timestamp():
            continue
        ended += 1
        if row.get("acq") == "paid":
            converted += 1
    if ended:
        lines.append(f"Trial → paid: {converted} of {ended} trials that ended "
                     f"({100.0 * converted / ended:.0f}%, n={ended})")
        lines.append("A trial that ended in the last day or two may still be in "
                     "Apple's billing retry.")
    else:
        lines.append("Trial → paid: no trial ended in the window (n=0)")
    return "\n".join(lines)


