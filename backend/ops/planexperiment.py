"""The paywall default-plan A/B test: half the devices preselect yearly, half
monthly, at the same time.

`/lever plan` could already switch every device between yearly and monthly
(#220), which runs the two arms one after the other. That compares two
different fortnights as much as two plans: a TikTok post, a season or a
release lands on one arm only. Running both at once removes that, and needs no
app change — 1.5.2 (build 22) and later already preselect whatever plan the
token names.

How it works:

  * `/lever plan split` starts an experiment, named by the day it started.
  * At each token mint, a device on build 22+ gets an arm from a hash of the
    experiment and its subject, so it keeps the same arm on every mint, and a
    new experiment reshuffles. Its first mint enrols it and counts it.
    Unknown or older builds are not enrolled and get the app's default,
    because they would preselect yearly whatever the token said.
  * A new subscription from an enrolled device is counted to its arm, once
    per original transaction, and the arm is remembered on the transaction so
    a trial's conversion — which Apple may report with no device at all — can
    be counted to the same arm.
  * `/paywall` shows the counts per arm, and value per device in dollars.
    It gives no verdict until both arms reach a sample size fixed once
    enough starts exist to size it (#396): a p-value read every day and
    acted on the first time it dips under 0.05 finds differences that are
    not there far more often than 5% of the time.

Enrolment is keyed on the audit log's pseudonym, never the raw subject, and
expires with the index's 400 days. Only counts are kept per arm.

None of this grants or withholds anything: both arms see both plans, and only
which card is selected first differs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import TYPE_CHECKING, Literal

from ops import opsformat
from security import auditlog

if TYPE_CHECKING:
    from core.cache import ResilientCache

log = logging.getLogger("snapworth.notify")

Arm = Literal["yearly", "monthly"]
ARMS: tuple[Arm, Arm] = ("yearly", "monthly")

# The first build that reads `paywall_default_plan`: 1.5.2.
MIN_BUILD = 22

# The lever document's value for "both arms at once".
SPLIT = "split"

TTL = 60 * 60 * 24 * 400

# The trial counters `opsindex.acquisition` names, and the two this counts.
TRIAL, PAID = "trial", "paid"

# The fixed horizon (#396). The test is sized to see a start rate 30% higher
# in one arm than the pooled rate, two-sided at 5% with 80% power. The pooled
# rate is unknown when the split starts, so the size is computed once the arms
# have SIZING_STARTS starts between them and then stored, never recomputed:
# a target that moved with the data would be peeking by another name.
MIN_DETECTABLE_LIFT = 0.30
Z_ALPHA = 1.959964      # two-sided 5%
Z_POWER = 0.841621      # 80% power
SIZING_STARTS = 20

_cache: ResilientCache | None = None


def bind(cache: ResilientCache | None) -> None:
    global _cache
    _cache = cache


def arm_for(experiment: str, subject: str) -> Arm:
    """The arm a device is in. Stable for one experiment, reshuffled by the next."""
    digest = hashlib.sha256(f"planab:{experiment}:{subject}".encode()).digest()
    return ARMS[digest[0] & 1]


def active_experiment(levers_doc: dict) -> str | None:
    """The running experiment's id, or None when the lever is not on split."""
    if levers_doc.get("paywall_default_plan") != SPLIT:
        return None
    experiment = levers_doc.get("plan_split_id")
    return experiment if isinstance(experiment, str) and experiment else None


def _device_key(experiment: str, subject: str) -> str:
    return f"planab:dev:{experiment}:{auditlog.pseudonymise(subject)}"


def counter_key(experiment: str, arm: str, name: str) -> str:
    return f"planab:{experiment}:{arm}:{name}"


async def _incr(experiment: str, arm: str, name: str, amount: int = 1) -> None:
    if _cache is not None:
        await _cache.incr(counter_key(experiment, arm, name), TTL, amount)


async def assign(levers_doc: dict, subject: str, build: int | None) -> Arm | None:
    """The plan this device's token should carry while a split runs, or None
    for the app's default. Enrols the device on its first mint. Never raises:
    an assignment that cannot be recorded is still made, because the arm is a
    hash and the next mint will try again."""
    experiment = active_experiment(levers_doc)
    if experiment is None or build is None or build < MIN_BUILD:
        return None
    arm = arm_for(experiment, subject)
    if _cache is not None:
        try:
            if await _cache.add(_device_key(experiment, subject), arm, TTL):
                await _incr(experiment, arm, "devices")
        except Exception as exc:
            log.warning("plan experiment enrolment failed: %s", type(exc).__name__)
    return arm


def _annual_cents(ent) -> tuple[int, str] | None:
    """What a paid period is worth over a year, in cents, with its currency.

    The arms sell different plans, so a monthly's first charge against a
    yearly's would make yearly win on every count. Twelve monthly charges is
    an upper bound for a monthly subscriber; the readout says so.
    """
    price, currency = getattr(ent, "price", None), getattr(ent, "currency", None)
    if (isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0
            or not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha()):
        return None
    yearly = "yearly" in opsformat.plan(getattr(ent, "product_id", None))
    return int(round(float(price) * (1 if yearly else 12) * 100)), currency.upper()


async def _add_value(experiment: str, arm: str, ent) -> None:
    value = _annual_cents(ent)
    if value is None or _cache is None:
        return
    cents, currency = value
    await _incr(experiment, arm, f"value:{currency}", cents)
    key = f"planab:{experiment}:currencies"
    try:
        known = set(json.loads(await _cache.get(key) or "[]"))
    except Exception:
        known = set()
    if currency not in known:
        known.add(currency)
        await _cache.set(key, json.dumps(sorted(known)), TTL)


async def note_new_subscription(levers_doc: dict, subject: str, ent, acq: str) -> None:
    """Count a new trial or direct purchase to the buyer's arm. Never raises."""
    experiment = active_experiment(levers_doc)
    otid = getattr(ent, "original_transaction_id", None)
    if experiment is None or _cache is None or not otid or acq not in (TRIAL, PAID):
        return
    try:
        arm = await _cache.get(_device_key(experiment, subject))
        if arm not in ARMS:
            return                      # not enrolled: an older build, or before the split
        record = json.dumps({"experiment": experiment, "arm": arm})
        if not await _cache.add(f"planab:sub:{otid}", record, TTL):
            return
        await _incr(experiment, arm, "trial_starts" if acq == TRIAL else "paid_direct")
        bought = "yearly" if "yearly" in opsformat.plan(ent.product_id) else "monthly"
        await _incr(experiment, arm, f"bought:{bought}")
        if acq == PAID:
            await _add_value(experiment, arm, ent)
    except Exception as exc:
        log.warning("plan experiment purchase count failed for %s: %s",
                    otid, type(exc).__name__)


async def note_conversion(ent) -> None:
    """Count a trial's first paid period to the arm its trial started in.

    Whichever half sees it first — Apple's notification or the device — and
    once, on its own guard. Counted even after the split ends: the trial was
    started under it. Never raises."""
    otid = getattr(ent, "original_transaction_id", None)
    if _cache is None or not otid:
        return
    try:
        raw = await _cache.get(f"planab:sub:{otid}")
        if not raw:
            return
        record = json.loads(raw)
        experiment, arm = record.get("experiment"), record.get("arm")
        if not isinstance(experiment, str) or arm not in ARMS:
            return
        if not await _cache.add(f"planab:conv:{otid}", "1", TTL):
            return
        await _incr(experiment, arm, "conversions")
        await _add_value(experiment, arm, ent)
    except Exception as exc:
        log.warning("plan experiment conversion count failed for %s: %s",
                    otid, type(exc).__name__)


def two_proportion_p(a: int, n_a: int, b: int, n_b: int) -> float | None:
    """Two-sided p-value for "the two rates are equal", by the normal
    approximation. None when either arm is too small for it to mean much."""
    if n_a < 30 or n_b < 30:
        return None
    pooled = (a + b) / (n_a + n_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    if se == 0:
        return None
    z = abs(a / n_a - b / n_b) / se
    return math.erfc(z / math.sqrt(2))


def planned_devices_per_arm(pooled_rate: float,
                            lift: float = MIN_DETECTABLE_LIFT) -> int | None:
    """Devices each arm needs to detect `lift` (relative) on `pooled_rate`.

    The normal-approximation size for two proportions. None for a rate
    that cannot be sized from (none, or every device)."""
    if not 0 < pooled_rate < 1:
        return None
    p1 = pooled_rate
    p2 = min(p1 * (1 + lift), 0.999)
    if p2 <= p1:
        return None
    n = (Z_ALPHA + Z_POWER) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p2 - p1) ** 2
    return max(30, math.ceil(n))


async def _planned(experiment: str, starts: int, devices: int) -> int | None:
    """The stored sample size, fixing it on the first read that can size it."""
    if _cache is None:
        return None
    key = f"planab:{experiment}:planned"
    try:
        stored = await _cache.get(key)
        if stored:
            return int(stored)
        if starts < SIZING_STARTS or devices <= 0:
            return None
        planned = planned_devices_per_arm(starts / devices)
        if planned is None:
            return None
        await _cache.add(key, str(planned), TTL)
        return int(await _cache.get(key) or planned)
    except Exception as exc:
        log.warning("plan experiment sizing failed: %s", type(exc).__name__)
        return None


async def _read_int(key: str) -> int:
    if _cache is None:
        return 0
    try:
        return int(await _cache.get(key) or 0)
    except Exception:
        return 0


async def readout_text(levers_doc: dict) -> str | None:
    """The `/paywall` block for the running or most recent split, or None."""
    experiment = levers_doc.get("plan_split_id")
    if not isinstance(experiment, str) or not experiment or _cache is None:
        return None
    running = active_experiment(levers_doc) == experiment
    try:
        currencies = json.loads(await _cache.get(f"planab:{experiment}:currencies") or "[]")
    except Exception:
        currencies = []
    started = f"{experiment[:4]}-{experiment[4:6]}-{experiment[6:]}"
    lines = [f"🧪 <b>Default-plan test</b> since {started}, "
             + ("running" if running else "ended")]
    starts: dict[str, tuple[int, int]] = {}
    per_device: dict[str, float] = {}
    for arm in ARMS:
        devices = await _read_int(counter_key(experiment, arm, "devices"))
        trials = await _read_int(counter_key(experiment, arm, "trial_starts"))
        direct = await _read_int(counter_key(experiment, arm, "paid_direct"))
        converted = await _read_int(counter_key(experiment, arm, "conversions"))
        yearly = await _read_int(counter_key(experiment, arm, "bought:yearly"))
        monthly = await _read_int(counter_key(experiment, arm, "bought:monthly"))
        value: dict[str, float] = {}
        for currency in currencies if isinstance(currencies, list) else []:
            if isinstance(currency, str):
                cents = await _read_int(counter_key(experiment, arm, f"value:{currency}"))
                if cents:
                    value[currency] = cents / 100
        starts[arm] = (trials + direct, devices)
        rate = f" ({(trials + direct) / devices:.1%})" if devices else ""
        worth = opsformat.money_total(value)
        usd, missing, _ = opsformat.approx_usd(value)
        device_text = ""
        if devices:
            per_device[arm] = usd / devices
            device_text = (f" · ≈ {opsformat.usd(per_device[arm])}/device"
                           + (f" excl. {', '.join(missing)}" if missing else ""))
        lines.append(
            f"<b>{arm}</b> preselected: {devices} devices · {trials} trials + "
            f"{direct} direct{rate} · {converted} trials paid · bought "
            f"{yearly} yearly, {monthly} monthly · worth {worth}/yr{device_text}")
    (a, n_a), (b, n_b) = starts["yearly"], starts["monthly"]
    planned = await _planned(experiment, a + b, n_a + n_b)
    smaller = min(n_a, n_b)
    if planned is None:
        lines.append(f"Sample size: fixed once the arms have {SIZING_STARTS} trials "
                     f"and purchases between them (now {a + b}). No verdict before then.")
    elif smaller < planned:
        lines.append(f"Sample: {smaller} of {planned} devices per arm, enough to see a "
                     f"{MIN_DETECTABLE_LIFT:.0%} difference in start rate. "
                     + ("No verdict before then: a p-value checked until it dips under "
                        "0.05 finds differences that are not there."
                        if running else
                        "Ended before its sample: inconclusive, whatever the counts say."))
    else:
        p = two_proportion_p(a, n_a, b, n_b)
        if p is not None:
            verdict = "a real difference" if p < 0.05 else "no clear difference"
            lines.append(f"Sample reached ({planned} per arm). Start rate: p = {p:.2f}, "
                         f"{verdict}.")
        if len(per_device) == len(ARMS):
            leader = max(ARMS, key=lambda arm: per_device[arm])
            other = next(arm for arm in ARMS if arm != leader)
            lines.append(f"Decide on value per device: <b>{leader}</b> leads, "
                         f"≈ {opsformat.usd(per_device[leader])} vs "
                         f"{opsformat.usd(per_device[other])}. The start-rate test "
                         "says whether the arms really differ; value per device "
                         "says which one earns more.")
    lines.append("Builds 22+ only. Worth per year counts a monthly as 12 charges, an "
                 "upper bound; a trial adds its value when it converts. Dollar "
                 "figures use a fixed ECB rate.")
    return "\n".join(lines)
