"""The operator's levers: the free-scan welcome, the paywall's default plan,
and the oldest build still served, with the bot commands that set them.

Split out of notify.py (#230). The app reads all three on its request paths —
`quota.ScanQuota` reads `free_scan_lever` on every free scan, `auth` sends
`paywall_default_plan` with every token, and main refuses builds below
`minimum_build` — so nothing here depends on Telegram. The cache and the
quota's `describe_welcome` are bound once at startup by `notify.configure`;
notify routes `/lever` and `/minbuild` here and adds its menu to the replies.
"""

from __future__ import annotations

import html
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal

import opsformat
import opsstats

if TYPE_CHECKING:
    from cache import ResilientCache
    from quota import WelcomeSetting

# notify's logger, so log filters written before the split still match.
log = logging.getLogger("snapworth.notify")

_cache: ResilientCache | None = None

# What the first-day welcome is, from the `ScanQuota` that grants it: its
# `describe_welcome`, injected by main. None when the app did not wire one,
# and then the bot says so rather than guess — see `welcome_setting`.
_describe_welcome: Callable[[], Awaitable[WelcomeSetting]] | None = None


def bind(cache: ResilientCache | None,
         describe_welcome: Callable[[], Awaitable[WelcomeSetting]] | None = None) -> None:
    """Point the levers at the app's cache, and the welcome at the quota."""
    global _cache, _describe_welcome
    _cache = cache
    _describe_welcome = describe_welcome


# ── The free-scan lever ─────────────────────────────────────────────────────
#
# `/experiment` could report that the lever was not armed and do nothing about
# it. The measurement half of the experiment lives here — `limit_hits`, the
# window, the partial-day handling — and the control half was a Railway
# variable and a redeploy, from a phone.
#
# `quota.ScanQuota` reads `free_scan_lever` on every free scan and falls back
# to the environment when it returns None or raises, so an unreadable lever can
# neither fail a scan nor grant an allowance nobody configured. The value is
# clamped there too: this is a button that spends money.
#
# What a value resolves to — the cap, the daily floor, the environment's say —
# is the quota's to answer, and `welcome_setting` asks it. This module used to
# keep its own copy of each rule. The missing floor is the copy that made
# screens untrue — the lever's confirmation (fixed in 6cae388) and
# /experiment's lever line — while the default and the cap still matched,
# each one quota edit away from not matching.

LEVERS_KEY = "opsstate:levers"
LEVER_CHANGES_CAP = 40
DEFAULT_ARMED_FIRST_DAY = 3


async def welcome_setting() -> WelcomeSetting | None:
    """The welcome as the quota resolves it, or None when it cannot be asked.

    None when main did not wire `describe_welcome` into this process. The bot
    then says it does not know and will not arm: answering from its own copy
    of the rules is what printed `FREE_SCANS_FIRST_DAY=1` — no welcome at a
    daily limit of 1 — as though the lever were armed.
    """
    if _describe_welcome is None:
        return None
    try:
        return await _describe_welcome()
    except Exception as exc:                    # pragma: no cover - defensive
        log.warning("welcome setting unreadable: %s", type(exc).__name__)
        return None


def welcome_summary(setting: WelcomeSetting | None) -> tuple[bool, str, str]:
    """(armed, head, why) — whether a new user gets a first-day welcome now.

    Plain text, so the CSV export can carry it; `welcome_html` marks it up.
    It reports what the quota grants and then what was asked for, because the
    two differ exactly when the operator most needs to know it: a value at or
    below the daily limit is asked for and grants nothing.
    """
    if setting is None:
        return False, "welcome unknown", "the quota is not wired into the bot in this process"
    env = f"FREE_SCANS_FIRST_DAY={setting.environment}"
    chat = setting.override is not None
    if setting.scans:
        why = (f"{setting.scans} first-day scan{'s' if setting.scans != 1 else ''}, "
               + ("set from chat" if chat else f"from {env}"))
        if setting.configured > setting.cap:
            why += f" (asked for {setting.configured}, capped at {setting.cap})"
    elif setting.configured <= 0:
        why = "disarmed from chat" if chat else f"{env}, or unset"
    else:
        asked = f"the lever's {setting.configured}, set from chat," if chat else env
        if setting.configured > setting.daily:
            # Above the daily limit as asked, so the cap is what took it away:
            # clamped to a cap no higher than the daily limit. Saying the
            # value is "not above the daily limit" would be false.
            why = (f"{asked} is capped at {setting.cap}, which is not above the "
                   f"daily limit of {setting.daily}, so no first-day welcome")
        else:
            why = (f"{asked} is not above the daily limit of {setting.daily}, "
                   "so no first-day welcome")
    if chat and setting.environment != setting.override:
        # Worth printing: the environment is what ↩️ Use env hands back to.
        why += f" · env {env}"
    return bool(setting.scans), "lever armed" if setting.scans else "lever not armed", why


def welcome_html(setting: WelcomeSetting | None) -> str:
    armed, head, why = welcome_summary(setting)
    return f"{head if armed else f'<b>{head}</b>'} — {html.escape(why)}"


async def read(*, required: bool = False) -> dict:
    """The levers document. {} when unreadable, unless `required`, which
    raises instead: see `_set_free_scan_lever`."""
    cache = _cache
    try:
        if cache is None:
            raise RuntimeError("no cache bound")
        raw = await cache.get(LEVERS_KEY, required=required)
    except Exception:
        if required:
            raise
        return {}
    try:
        doc = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def lever_value(doc: dict) -> int | None:
    """The welcome allowance a levers document holds, or None for none."""
    value = doc.get("free_scans_first_day")
    return int(value) if isinstance(value, (int, float)) else None


async def free_scan_lever() -> int | None:
    """The operator's welcome allowance, or None to use the environment.

    Injected into `ScanQuota` from main.py — quota must not import this module.
    Raises nothing: `read` swallows, and a missing key reads as None.
    """
    return lever_value(await read())


async def _set_free_scan_lever(value: int | None) -> dict | None:
    """Set or clear the lever, and record the day it changed.

    The record is the point. A measurement window whose lever moved mid-flight
    and does not say so is worse than no window at all — the numbers look
    continuous and are not.

    None, with nothing written, when the document could not be read. Read as
    {} it was written back as just this change, and the record of every
    earlier one was gone.
    """
    try:
        doc = await read(required=True)
    except Exception as exc:
        log.warning("levers unreadable, not changing them: %s", type(exc).__name__)
        return None
    before = doc.get("free_scans_first_day")
    if value is None:
        doc.pop("free_scans_first_day", None)
    else:
        doc["free_scans_first_day"] = int(value)
    changes = [c for c in (doc.get("changes") or []) if isinstance(c, list) and len(c) == 3]
    changes.append([opsstats.day(), before, value])
    doc["changes"] = changes[-LEVER_CHANGES_CAP:]
    cache = _cache
    if cache is None:       # read(required=True) raised already; for pyright
        return None
    await cache.set(LEVERS_KEY, json.dumps(doc))
    return doc


def lever_label(value: int | None) -> str:
    if value is None:
        return "environment default"
    return f"{value} first-day scan{'s' if value != 1 else ''}"


_LEVER_UNREADABLE = ("🎚 Nothing changed: the lever's stored state could not be "
                     "read, and writing over it would lose its change history. "
                     "Try again in a minute.")

_LEVER_UNWIRED = ("🎚 Nothing changed: the bot cannot ask the quota in this process "
                  "what an allowance would grant, and will not arm one blind.")


async def lever_command(argument: str, rest: str) -> tuple[str, opsformat.Buttons]:
    """`/lever`, `/lever arm [n]`, `/lever disarm`, and their confirmations;
    `/lever plan …` is the paywall's default plan (`_plan_command`).

    Two taps, never one. The first names what is about to change and what it
    currently is; the second does it. A single-tap lever on a phone, in a chat
    that also contains the word "Disarm" one row away, is how an experiment
    gets restarted by accident halfway through.
    """
    parts = (rest or "").split()
    action = parts[0].lower() if parts else ""
    if action == "plan":
        return await _plan_command(parts)
    # Anywhere after the action, not at a fixed index: the arm button carries
    # the value it is confirming ("lever arm 3 yes"), so checking parts[1]
    # silently re-showed the confirmation instead of acting on it.
    confirmed = any(token.lower() == "yes" for token in parts[1:])
    current = (await read()).get("free_scans_first_day")

    setting = await welcome_setting()

    if action == "arm":
        if setting is None:
            return _LEVER_UNWIRED, lever_buttons(current)
        wanted = DEFAULT_ARMED_FIRST_DAY
        for token in parts[1:]:
            if token.isdigit():
                wanted = int(token)
        # The quota clamps to its cap *and then* discards anything at or below
        # the daily limit. This used to mirror only the clamp, so arming with 0
        # or 1 replied "Lever armed — 1 first-day scan" and `/lever` went on
        # rendering that override, from a stored document with no TTL. Asked
        # rather than restated now, and refused where the operator can see it
        # rather than clamped silently.
        wanted = min(wanted, setting.cap)
        if not setting.allowance(wanted):
            daily = setting.daily
            smallest = setting.smallest
            fix = (f"Arm <b>{smallest}</b> or more, or use 🔕 Disarm for no "
                   "welcome at all." if smallest is not None else
                   f"The cap is <b>{setting.cap}</b>, so no first-day allowance "
                   "can be larger than that — there is no welcome to arm.")
            return (f"🧪 <b>That is not a welcome.</b>\n"
                    f"Every user already gets <b>{daily}</b> free scan"
                    f"{'s' if daily != 1 else ''} a day, and a first-day "
                    f"allowance is only an allowance above that — the quota "
                    f"discards <b>{wanted}</b> and grants nothing.\n{fix}",
                    lever_buttons(current))
        if not confirmed:
            return (f"🧪 <b>Arm the free-scan lever?</b>\n"
                    f"New users would get <b>{wanted}</b> scan{'s' if wanted != 1 else ''} "
                    f"on their first day. Currently {welcome_html(setting)}.\n"
                    f"This spends money: every extra scan is a model call.",
                    [[("✅ Yes, arm it", f"lever arm {wanted} yes"),
                      ("Cancel", "experiment")]])
        if await _set_free_scan_lever(wanted) is None:
            return _LEVER_UNREADABLE, lever_buttons(current)
        return (f"🧪 Lever armed — <b>{lever_label(wanted)}</b>.",
                [[("🧪 Experiment", "experiment")]])

    if action == "disarm":
        if not confirmed:
            return ("🔕 <b>Disarm the free-scan lever?</b>\n"
                    f"New users would fall back to the daily limit. Currently "
                    f"{welcome_html(setting)}.\n"
                    "The window in /experiment keeps running; only the allowance stops.",
                    [[("✅ Yes, disarm it", "lever disarm yes"),
                      ("Cancel", "experiment")]])
        if await _set_free_scan_lever(0) is None:
            return _LEVER_UNREADABLE, lever_buttons(current)
        return ("🔕 Lever disarmed — new users get the daily limit.",
                [[("🧪 Experiment", "experiment")]])

    if action == "default":
        if not confirmed:
            if setting is None:
                then = "FREE_SCANS_FIRST_DAY would decide again"
            else:
                # What handing back would actually grant, since the variable's
                # value alone does not say: 1 reads like a welcome and is none.
                after = setting.allowance(setting.environment)
                then = (f"FREE_SCANS_FIRST_DAY={setting.environment} would decide "
                        "again — " + (f"<b>{lever_label(after)}</b>" if after
                                      else "<b>no first-day welcome</b>"))
            return ("↩️ <b>Hand the lever back to the environment?</b>\n"
                    f"{then}. Currently {welcome_html(setting)}.",
                    [[("✅ Yes", "lever default yes"), ("Cancel", "experiment")]])
        if await _set_free_scan_lever(None) is None:
            return _LEVER_UNREADABLE, lever_buttons(current)
        return ("↩️ Lever cleared — the environment decides again.",
                [[("🧪 Experiment", "experiment")]])

    lines = [f"🎚 <b>Free-scan lever</b>\nNow: {welcome_html(setting)}",
             f"Override: <b>{lever_label(current)}</b>"]
    if setting is not None:
        lines.append(f"Environment: <code>FREE_SCANS_FIRST_DAY={setting.environment}</code>"
                     f" · daily limit {setting.daily}")
    return "\n".join(lines), lever_buttons(current)


# ── The paywall's default plan (#220) ──────────────────────────────────────
#
# Which plan the paywall preselects, sent to 1.5.2+ in the token response as
# `paywall_default_plan`. Unset means yearly, exactly as every build before
# the field behaves, so the lever exists to run the monthly-default arm
# without a release, and to end it the same way. Kept in the levers document
# beside the free-scan lever, with its own change record: `changes` is the
# free-scan lever's history, which /experiment's export reads, and a plan
# move written there would read as a free-scan change.

PaywallPlan = Literal["yearly", "monthly"]
PAYWALL_PLANS: tuple[PaywallPlan, ...] = ("yearly", "monthly")


def _plan_value(doc: dict) -> PaywallPlan | None:
    value = doc.get("paywall_default_plan")
    for plan in PAYWALL_PLANS:
        if value == plan:
            return plan
    return None


async def paywall_default_plan() -> PaywallPlan | None:
    """The plan the operator set, or None for the app's own default (yearly).

    Read on every token mint. Raises nothing: an unreadable levers document is
    None, which is what every user saw before the lever existed."""
    return _plan_value(await read())


async def _set_paywall_default_plan(value: PaywallPlan | None) -> dict | None:
    """Set or clear the plan lever, recording the day. None, with nothing
    written, when the document could not be read — as `_set_free_scan_lever`."""
    try:
        doc = await read(required=True)
    except Exception as exc:
        log.warning("levers unreadable, not changing the plan: %s", type(exc).__name__)
        return None
    before = _plan_value(doc)
    if value is None:
        doc.pop("paywall_default_plan", None)
    else:
        doc["paywall_default_plan"] = value
    changes = [c for c in (doc.get("plan_changes") or [])
               if isinstance(c, list) and len(c) == 3]
    changes.append([opsstats.day(), before, value])
    doc["plan_changes"] = changes[-LEVER_CHANGES_CAP:]
    cache = _cache
    if cache is None:       # read(required=True) raised already; for pyright
        return None
    await cache.set(LEVERS_KEY, json.dumps(doc))
    return doc


def _plan_label(value: str | None) -> str:
    return f"{value}" if value else "yearly (app default)"


async def _plan_command(parts: list[str]) -> tuple[str, opsformat.Buttons]:
    """`/lever plan`, `/lever plan yearly|monthly|default [yes]`.

    Two taps, like the free-scan lever. It reaches each device at its next
    token mint, within the hour, and only builds that read the field: 1.5.2
    and later."""
    wanted_raw = parts[1].lower() if len(parts) > 1 else ""
    confirmed = any(token.lower() == "yes" for token in parts[2:])
    doc = await read()
    current = _plan_value(doc)
    history = [c for c in (doc.get("plan_changes") or []) if isinstance(c, list) and len(c) == 3]
    buttons: opsformat.Buttons = [[("📅 Yearly", "lever plan yearly"),
                         ("🗓 Monthly", "lever plan monthly"),
                         ("↩️ App default", "lever plan default")]]

    if wanted_raw not in (*PAYWALL_PLANS, "default"):
        lines = ["💳 <b>Paywall default plan</b>",
                 f"Now: <b>{_plan_label(current)}</b>",
                 "Preselected on the paywall by builds 1.5.2 and later, from "
                 "their next token (within an hour). Older builds always "
                 "preselect yearly."]
        if history:
            day, before, after = history[-1]
            lines.append(f"Last change: {day[:4]}-{day[4:6]}-{day[6:]}, "
                         f"{_plan_label(before)} → {_plan_label(after)}")
        return "\n".join(lines), buttons

    # None for "default", the plan itself otherwise (checked just above).
    wanted = _plan_value({"paywall_default_plan": wanted_raw})
    if wanted == current:
        return f"💳 Nothing changed: the default is already <b>{_plan_label(current)}</b>.", buttons
    if not confirmed:
        return (f"💳 <b>Change the paywall's default plan?</b>\n"
                f"<b>{_plan_label(current)}</b> → <b>{_plan_label(wanted)}</b>.\n"
                "This is an experiment arm (#220): change it only at an arm "
                "boundary, and log the date in the growth log.",
                [[("✅ Yes, change it", f"lever plan {wanted_raw} yes"),
                  ("Cancel", "lever plan")]])
    if await _set_paywall_default_plan(wanted) is None:
        return _LEVER_UNREADABLE, buttons
    return (f"💳 Paywall default plan — <b>{_plan_label(wanted)}</b>, "
            "from each 1.5.2+ device's next token.", buttons)


def lever_buttons(current: int | None) -> opsformat.Buttons:
    row = [("🧪 Arm", "lever arm")]
    if current is not None:
        row.append(("↩️ Use env", "lever default"))
    row.append(("🔕 Disarm", "lever disarm"))
    return [row]


# ── The oldest build still served ───────────────────────────────────────────
#
# A bad client release could not be told to update: the server did not know
# which build was calling, and had no switch to act on it if it had.
# `main._refuse_outdated_build` reads this on /scan, /listing and /trends and
# refuses a build below it with `UPDATE_REQUIRED_DETAIL` and the code
# `update_required` — a 426 when the build said so in `X-SnapWorth-Build`,
# a 422 when it was read from the User-Agent. Only /scan and
# /listing show that text; the app fetches /trends with `try?`, so a refusal
# there shows nothing. /auth is never gated, so an old build can still sign in
# and record a purchase.
#
# Off until set, and fails open: an unreadable value serves everyone, because
# a switch that locks out every user when Redis blinks is worse than none.

MIN_BUILD_KEY = "opsstate:minbuild"
MIN_BUILD_MAX = 100_000

#: What a refused build is told. Here rather than in main.py so the bot's
#: confirmation can quote it word for word.
UPDATE_REQUIRED_DETAIL = (
    "This version of SnapWorth is no longer supported. "
    "Update SnapWorth from the App Store to keep using it.")


async def minimum_build() -> int | None:
    """The oldest build /scan, /listing and /trends still serve, or None.

    Raises nothing: anything unreadable is None, which serves every build.
    """
    cache = _cache
    if cache is None:
        return None
    try:
        raw = await cache.get(MIN_BUILD_KEY)
        value = int(raw) if raw else None
    except Exception:
        return None
    return value if value is not None and 0 < value <= MIN_BUILD_MAX else None


async def minbuild_command(argument: str, rest: str) -> tuple[str, opsformat.Buttons, bool]:
    """`/minbuild`, `/minbuild <n>`, `/minbuild off`, and their confirmations.

    Two taps, like `/lever`. The confirmation quotes what refused users are
    told, because it sends them to the App Store: set past the build that is
    actually live there, it tells them to install an update that does not
    exist.

    Returns the text, any buttons of its own, and whether notify should add
    its usual menu under them (only the two status replies).
    """
    parts = (rest or "").split()
    confirmed = any(token.lower() == "yes" for token in parts[1:])
    current = await minimum_build()
    back = [[("📵 Minimum build", "minbuild")]]
    cache = _cache
    if cache is None:  # the bot is wired by `configure`, which sets it first
        return "📵 No store is configured, so there is no minimum build.", back, False

    if argument == "off":
        if current is None:
            return "📵 No minimum build is set — every build is served.", back, False
        if not confirmed:
            return (f"📵 <b>Serve every build again?</b>\n"
                    f"Builds below <b>{current}</b> are refused now.",
                    [[("✅ Yes, serve all", "minbuild off yes"),
                      ("Cancel", "minbuild")]], False)
        await cache.delete(MIN_BUILD_KEY)
        log.warning("minimum build cleared from chat", extra={"previous": current})
        return "📵 Minimum build cleared — every build is served.", back, False

    if argument.isdigit():
        wanted = int(argument)
        if not 0 < wanted <= MIN_BUILD_MAX:
            return f"📵 <b>{wanted}</b> is not a build number.", back, False
        if not confirmed:
            return (f"📵 <b>Refuse builds below {wanted}?</b>\n"
                    f"On /scan and /listing they would be told: "
                    f"<i>{html.escape(UPDATE_REQUIRED_DETAIL)}</i>\n"
                    f"/trends is refused too, but the app drops that error "
                    f"silently and its Trending card just disappears.\n"
                    f"Only do this once build <b>{wanted}</b> is live on the "
                    f"App Store. Builds 7 and older cannot show this text and "
                    f"will see \"Something went wrong\". A build that sends "
                    f"<code>X-SnapWorth-Build</code> is refused with a 426 and "
                    f"shows the app's own update message in the app's "
                    f"language; only the Scan tab's alert adds an App Store "
                    f"button. Sign-in and purchases "
                    f"stay open, and a request that does not say its build is "
                    f"always served. The access log's <code>build</code> field "
                    f"shows who is still on an older one, and "
                    f"<code>snapworth_outdated_build_refused_total</code> "
                    f"counts refusals.\n"
                    f"Currently: <b>{current if current is not None else 'none'}</b>.",
                    [[(f"✅ Yes, require {wanted}", f"minbuild {wanted} yes"),
                      ("Cancel", "minbuild")]], False)
        await cache.set(MIN_BUILD_KEY, str(wanted))
        log.warning("minimum build set from chat",
                    extra={"minimum": wanted, "previous": current})
        return f"📵 Minimum build set to <b>{wanted}</b>.", back, False

    if current is None:
        return ("📵 <b>Minimum build</b>: none — every build is served.\n"
                "<code>/minbuild &lt;n&gt;</code> refuses builds below n on "
                "/scan, /listing and /trends. /scan and /listing tell them to "
                "update; on /trends the Trending card just disappears.",
                [], True)
    return (f"📵 <b>Minimum build</b>: <b>{current}</b>\n"
            f"Builds below it are told to update on /scan and /listing, and "
            f"lose the Trending card, since the app drops a /trends error "
            f"silently. Sign-in and purchases stay open.",
            [[("↩️ Serve every build", "minbuild off")]], True)
