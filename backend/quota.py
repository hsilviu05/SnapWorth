"""Server-side free-scan quota.

Closes the second half of SEC-02. The counter previously lived in the app's
`AppStorage`, so it was advisory: deleting and reinstalling reset it, and a
jailbroken device could edit it outright.

Here it is authoritative. Three properties matter:

* **Atomic.** The allowance is *reserved* with a single `INCR` whose return
  value is the limit check, so concurrent requests can't both observe "2 used"
  and both proceed. This docstring used to claim that property while `check`
  was a plain `GET` and the `INCR` happened afterwards in `consume` — five
  concurrent scans against a limit of 1 all reached the model. `reserve` is
  the atomic path; `check`/`consume` remain for callers that genuinely want
  to read without claiming.
* **Fails closed.** If the durable cache is unreachable the request is refused
  rather than granted. A quota that fails open is not a quota.
* **Reinstall-resistant.** A brand-new subject is cross-checked against the
  device's DeviceCheck bit before being handed a fresh allowance.

A reservation is released when the work it was claimed for produces nothing:
charging for a failed scan is both unfair and a support burden.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from cache import CacheUnavailable
from devicecheck import DeviceCheckError

log = logging.getLogger("snapworth.quota")

# Holds a reference to each background DeviceCheck write until it finishes: an
# un-awaited task is otherwise collectable mid-flight.
_background: set[asyncio.Task] = set()

# One per day, not three. At three, almost nobody exhausted the allowance, so
# the paywall was never reached and download-to-paid sat at 1.43% while the
# free tier ran at a loss (~$0.0060/scan). Override with FREE_SCANS_PER_DAY.
FREE_SCANS_PER_DAY = 1

# Counter lives slightly longer than a day so a user near midnight in any
# timezone can't gain an extra allowance by straddling the boundary.
_COUNTER_TTL = 60 * 60 * 30

# A first-day allowance, larger than the daily one. Off unless FREE_SCANS_FIRST_DAY
# is set above FREE_SCANS_PER_DAY.
#
# At one scan a day, a new user's first scan is also their last free one: the
# paywall arrives before they have felt the value twice. A welcome allowance
# lets the habit form on day one and returns to the daily limit on day two.
# It is an experiment lever, measured through the client's
# free_scan_limit_hit → paywall_viewed → purchase_started funnel, not a
# permanent widening of the free tier. Granted once per subject, ever, and at
# most once a month per device: DeviceCheck's bit1 marks the hardware when it
# is granted, so a reinstall on that device gets the daily limit instead.
#
# Decided 2026-10-06 (#212): production keeps FREE_SCANS_FIRST_DAY=3. The
# 09-10 → 09-24 window could not settle it on data: 100 free scans in 34
# days, 9 device-days with the lever off, and the server cannot count limit
# hits, because the app stops a spent user before /scan
# (docs/experiments/free-scans-2026-09.md). So it was kept on cost (a few
# cents a month at this volume) and product grounds (two looks at the value
# before the paywall). Revisit with TelemetryDeck's funnel once volume allows,
# and never inside another experiment's window (roadmap calendar). The code
# default stays 0, so an unset variable means no welcome.
FREE_SCANS_FIRST_DAY = 0

# The welcome grant outlives any counter, so a subject can never be welcomed
# twice. Matches the attestation-state horizon.
_WELCOME_TTL = 60 * 60 * 24 * 400

# How long "I have seen this subject before" is remembered. Same horizon as the
# welcome marker, because the two answer the same question about the same
# subject and a shorter one here silently re-opens the welcome.
_SEEN_TTL = _WELCOME_TTL

# Written into the welcome key when the welcome was *refused* (a reinstall
# DeviceCheck recognised). Any value that is not a UTC day string works —
# `_limit_for` compares against today's day — but a named constant says so.
_DENIED = "denied"


class QuotaExceeded(Exception):
    def __init__(self, message: str, resets_at: int) -> None:
        super().__init__(message)
        self.message = message
        self.resets_at = resets_at


class QuotaUnavailable(Exception):
    """Durable state was unreachable; the caller must fail closed."""


@dataclass(frozen=True)
class QuotaStatus:
    used: int
    limit: int
    unlimited: bool
    # The UTC day this status was counted against. A refund has to decrement
    # the day the reservation was *taken from*, not whichever day the failure
    # happened to land in — see `refund`. Empty for a status that is not a
    # reservation, which then means "today".
    day: str = ""

    @property
    def remaining(self) -> int:
        return 2**31 if self.unlimited else max(0, self.limit - self.used)


@dataclass(frozen=True)
class WelcomeSetting:
    """The first-day welcome as `ScanQuota` resolves it, for whoever has to
    report it or change it.

    The ops bot used to work this out for itself: its own default for
    FREE_SCANS_PER_DAY, its own copy of the cap, and not the rule that a
    first-day allowance no larger than the daily one is no welcome at all.
    The missing rule is the copy that went wrong, twice: `/lever arm`
    confirmed "1 first-day scan" that the quota discarded (fixed in 6cae388),
    and `/experiment` printed `FREE_SCANS_FIRST_DAY=1` — nothing, at a daily
    limit of 1 — as though the lever were armed. The default and the cap
    still matched; each was one quota edit away from not matching. It now
    asks the quota that grants the scans, and `allowance` is the only
    statement of the rule.
    """

    daily: int
    # FREE_SCANS_FIRST_DAY as this process was started with it. 0 when unset.
    environment: int
    # The operator's runtime value, or None to leave it to `environment`. An
    # unreadable override is None too: the quota falls back the same way.
    override: int | None
    cap: int

    @property
    def configured(self) -> int:
        """What was asked for, before the rule: the override if there is one."""
        return self.environment if self.override is None else self.override

    @property
    def scans(self) -> int:
        """First-day scans a new subject gets now. 0 is no welcome."""
        return self.allowance()

    def allowance(self, configured: int | None = None) -> int:
        """What a new subject would get if `configured` were asked for — by
        default, what is asked for now. 0 is no welcome.

        Clamped to `cap`, because a lever reachable from a chat must not be
        able to hand out an unbounded allowance; and then a value no larger
        than the daily limit is not a welcome, so it is 0 rather than itself.
        """
        value = self.configured if configured is None else configured
        value = max(0, min(value, self.cap))
        return value if value > self.daily else 0

    @property
    def smallest(self) -> int | None:
        """The smallest value that is a welcome, or None when the cap leaves
        no room above the daily limit."""
        return self.daily + 1 if self.daily + 1 <= self.cap else None


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _utc_month() -> str:
    """"YYYY-MM" — the granularity Apple's DeviceCheck `last_update_time` has.

    Coarse, and the only period signal DeviceCheck gives. It is what lets a
    single bit mean "this month" instead of "forever"; see `starting_balance`.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _exhausted_message(limit: int) -> str:
    """User-facing copy for a spent allowance.

    The client shows it as written: in every language on a build from before
    the error codes, in English on one that reads them (another language gets
    the app's own sentence for `quota_exhausted`). So it has to read correctly
    at every limit. The old f-string hardcoded the plural and, once the free
    tier moved to one scan a day, told everybody "You've used all 1 free scans
    today."
    """
    if limit == 1:
        return "You've used your free scan for today."
    return f"You've used all {limit} free scans today."


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 86400
    return max(60, int(tomorrow - now.timestamp()))


class ScanQuota:
    """Authoritative daily free-scan accounting."""

    def __init__(self, cache, device_check=None, limit: int = FREE_SCANS_PER_DAY,
                 first_day_limit: int = FREE_SCANS_FIRST_DAY,
                 welcome_override=None) -> None:
        self._cache = cache
        self._device_check = device_check
        self._limit = limit
        self._env_first_day = first_day_limit
        # `welcome_override` is an optional async callable returning the
        # operator's runtime value for the welcome allowance, or None to use
        # the environment. It exists so the experiment can be started and
        # stopped from the ops bot instead of a Railway variable and a
        # redeploy — the measurement half of that experiment lives in the bot
        # already, and the control half was in another company's dashboard.
        self._welcome_override = welcome_override

    # A lever reachable from a chat must not be able to hand out an unbounded
    # allowance because of a fat finger. Every scan past the daily limit is
    # real money against the Gemini bill.
    MAX_FIRST_DAY_SCANS = 10

    async def describe_welcome(self) -> WelcomeSetting:
        """The welcome as this quota resolves it, for the ops bot.

        Injected into the bot from main, the way `welcome_override` is
        injected here, so the bot's confirmations and `/experiment` report what
        a new subject is actually granted rather than a re-derivation of it.

        Resolved per call rather than captured at construction, because the
        override is settable at runtime. An unreadable override falls back to
        the environment value: it must never fail the scan, and it must never
        fail *open* to a larger allowance than was configured.
        """
        override: int | None = None
        if self._welcome_override is not None:
            try:
                value = await self._welcome_override()
            except Exception:                       # pragma: no cover - defensive
                value = None
            if value is not None:
                try:
                    override = int(value)
                except (TypeError, ValueError):
                    override = None
        return WelcomeSetting(daily=self._limit, environment=self._env_first_day,
                              override=override, cap=self.MAX_FIRST_DAY_SCANS)

    async def _first_day_limit(self) -> int:
        """Today's welcome allowance, or 0 when the welcome is off.

        This costs one cache read per free scan where the old code short-
        circuited for free when the welcome was off. At a few hundred scans a
        month that is not worth optimising, and the accessor is only consulted
        when one was injected.
        """
        return (await self.describe_welcome()).scans

    @staticmethod
    def _counter_key(subject: str, day: str | None = None) -> str:
        return f"quota:{subject}:{day or _utc_day()}"

    @staticmethod
    def _seen_key(subject: str) -> str:
        return f"quota:seen:{subject}"

    @staticmethod
    def _welcome_key(subject: str) -> str:
        return f"quota:welcome:{subject}"

    async def _limit_for(self, subject: str) -> int:
        """Today's limit for a free subject: the welcome allowance on the day
        it was granted, the daily limit otherwise. Costs nothing while the
        welcome is off."""
        first_day = await self._first_day_limit()
        if not first_day:
            return self._limit
        try:
            granted = await self._cache.get(self._welcome_key(subject), required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc
        return first_day if granted == _utc_day() else self._limit

    async def status(self, subject: str, is_pro: bool) -> QuotaStatus:
        if is_pro:
            return QuotaStatus(used=0, limit=self._limit, unlimited=True)
        limit = await self._limit_for(subject)
        try:
            raw = await self._cache.get(self._counter_key(subject), required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc
        return QuotaStatus(used=int(raw or 0), limit=limit, unlimited=False)

    async def check(self, subject: str, is_pro: bool) -> QuotaStatus:
        """Raise if the subject has no allowance left. Does not consume."""
        status = await self.status(subject, is_pro)
        if not status.unlimited and status.used >= status.limit:
            raise QuotaExceeded(
                _exhausted_message(status.limit),
                resets_at=int(time.time()) + _seconds_until_utc_midnight(),
            )
        return status

    async def reserve(self, subject: str, is_pro: bool) -> QuotaStatus:
        """Claim one use atomically, *before* the work runs.

        The `INCR`'s return value is the check: if it lands above the limit
        this request was not entitled to it, so the claim is handed straight
        back and `QuotaExceeded` is raised. Two requests cannot both receive
        the same number, which is what separates this from `check` + `consume`.

        The caller must `refund` if the work fails.
        """
        if is_pro:
            return QuotaStatus(used=0, limit=self._limit, unlimited=True)
        limit = await self._limit_for(subject)
        # Read once and carried, so the refund cannot land on a different key
        # than the increment did.
        day = _utc_day()
        try:
            used = await self._cache.incr(
                self._counter_key(subject, day), _COUNTER_TTL, required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc

        if used > limit:
            # Refused, so it must not leave the counter raised against the
            # next request — otherwise a burst would push the count
            # arbitrarily far past the limit and delay the reset.
            await self._release(subject, day)
            raise QuotaExceeded(
                _exhausted_message(limit),
                resets_at=int(time.time()) + _seconds_until_utc_midnight(),
            )
        return QuotaStatus(used=used, limit=limit, unlimited=False, day=day)

    async def refund(self, subject: str, is_pro: bool,
                     day: str | None = None) -> None:
        """Return a reservation whose work produced no result.

        `day` is the UTC day the reservation was counted against — carried on
        the `QuotaStatus` that `reserve` returned. Without it the key was
        recomputed from "now", so a scan reserved at 23:59:59 and refunded a
        second later decremented the *new* day's counter. The day it was
        actually taken from kept the use, so the user was charged for a scan
        that produced nothing; and if another scan had already reserved on the
        new day, its live reservation was handed back instead — two scans out
        of one allowance. A scan takes seconds and the boundary is one second
        wide per day, so this was rare and permanent rather than loud.

        Best-effort: a failed refund costs the user one scan, which is the
        same outcome the pre-reservation code had on every failure, so it is
        never worth failing the request over.
        """
        if is_pro:
            return
        await self._release(subject, day)

    async def _release(self, subject: str, day: str | None = None) -> None:
        key = self._counter_key(subject, day)
        try:
            used = await self._cache.incr(
                key, _COUNTER_TTL, amount=-1, required=True)
        except CacheUnavailable as exc:
            log.error("quota refund failed — user charged for nothing: %s", exc)
            return
        if used < 0:
            # Only reachable if the counter was reset underneath a live
            # reservation (an operator clearing it, or a key that expired).
            # No longer reachable by crossing midnight: `key` is the day the
            # reservation was taken from, and that day's counter still holds
            # it. Written back to the same key, not to a freshly computed one.
            try:
                await self._cache.set(key, "0", _COUNTER_TTL, required=True)
            except CacheUnavailable:
                pass

    async def consume(self, subject: str, is_pro: bool) -> QuotaStatus:
        """Atomically record one use. Call only after the work succeeded."""
        if is_pro:
            return QuotaStatus(used=0, limit=self._limit, unlimited=True)
        limit = await self._limit_for(subject)
        try:
            used = await self._cache.incr(
                self._counter_key(subject), _COUNTER_TTL, required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc
        return QuotaStatus(used=used, limit=limit, unlimited=False)

    async def note_exhausted(self, device_token: str | None) -> None:
        """Mark the *physical device* as having spent its allowance.

        Survives reinstall, which the per-install counter cannot. Failures are
        swallowed: this is a hardening signal, not a correctness dependency.

        Apple stamps the write with a month, and `starting_balance` reads that
        stamp — so re-marking an already-marked device is not a no-op, it
        refreshes the month the mark belongs to.

        bit1 is written True, not False. Apple only writes both bits at once,
        and bit1 is the welcome mark: writing False here would wipe it from a
        device that took the welcome and then spent its last scan. A device
        that has spent its allowance has had its welcome anyway.
        """
        if not device_token or self._device_check is None:
            return
        if not self._device_check.is_configured:
            return
        try:
            await self._device_check.update_bits(device_token, bit0=True, bit1=True)
        except Exception as exc:
            log.warning("devicecheck update failed: %s", exc)

    async def starting_balance(self, subject: str, device_token: str | None) -> int:
        """Free scans a *newly seen* subject should start with.

        A fresh App Attest key id normally means a new install. If DeviceCheck
        says this hardware burned its allowance *this month*, the reinstall
        gets nothing back until the next one.

        "This month" is Apple's `last_update_time`, which is the only period
        DeviceCheck can express. It used to say "until the next reset" while no
        reset existed anywhere: `note_exhausted` is the only writer of bit0 in
        the repo and nothing ever cleared it. A bit that is never cleared, set
        for every free user who spends their scan, flags the whole free base
        permanently and denies each of them the first-day welcome on any
        future install of the app on that hardware.

        That reading, and this docstring, used to assume the bit was set on the
        second scan attempt of a day, i.e. on the server's refusal. It almost
        never was. The client stops at zero using the server's own count, so
        the refused request is never sent, and this check read a bit that
        nothing set: delete-and-reinstall handed back a fresh allowance, and
        the welcome with it. `auth.reserve_quota` now marks the device when a
        reservation takes the *last* scan. That does flag nearly every engaged
        free user, so the month scope above is what makes it tolerable.

        The cost falls on any new subject on that hardware in the same month,
        not only a reinstall. A re-attestation on the same phone is one too:
        the server lost the key's state, a restore to the same device, or an
        invalidated Secure Enclave key. Each loses that day's scan and the
        welcome, but not the scans after.

        bit1 is the welcome's own mark. Marking only on the last scan left the
        welcome open: with it armed, the last scan is the first-day
        allowance's, so a user who stopped one short and reinstalled was never
        marked, and every reinstall was welcomed again. `_welcome` now marks
        the hardware when it grants. A new subject on hardware marked only
        this way this month, a re-attestation included, gets the daily limit
        without the welcome.

        Missing tokens, and tokens Apple refuses, get the daily limit and no
        welcome. Only Apple being unreachable gets the benefit of the doubt.
        """
        try:
            # `_SEEN_TTL`, not `_COUNTER_TTL`. "Have I ever seen this subject"
            # is not a 30-hour question, and `cache.add` never refreshes an
            # existing key — `InMemoryCache.add` returns False without touching
            # the expiry and `RedisCache.add` is `SET … NX` — so the marker
            # expired 30 hours after the subject was *first* minted rather than
            # 30 hours after it was last used. Every subject, however active,
            # fell back into this `first_time` branch roughly every 30 hours
            # forever, which also re-ran the DeviceCheck query for the whole
            # active base on that cadence.
            #
            # A reinstall is unaffected either way: App Attest mints a new key
            # id, so a reinstall is a *different* subject with its own seen
            # marker. The short TTL only ever made the same subject look new.
            first_time = await self._cache.add(
                self._seen_key(subject), "1", _SEEN_TTL, required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc

        if not first_time:
            return await self._limit_for(subject)   # already known, normal path

        if self._device_check is None or not self._device_check.is_configured:
            return await self._welcome(subject)
        if not device_token:
            # With DeviceCheck configured, a missing token is not "a device we
            # know nothing about" but a check that did not happen. The token is
            # optional on the wire and not covered by the attestation, so
            # leaving it out used to be a way to skip the check and take the
            # welcome on every reinstall. Without it the daily limit is still
            # granted. The genuine app always sends one unless `DCDevice`
            # fails, and in that rare case the user misses only the welcome.
            log.info("no devicecheck token on a new subject — no welcome")
            return await self._refuse_welcome(subject)

        try:
            bits = await self._device_check.query_bits(device_token)
        except DeviceCheckError as exc:
            if not exc.is_refusal:
                # Unreachable, or a 5xx. Apple's availability must not gate
                # our own service.
                log.warning("devicecheck query failed, granting default: %s", exc)
                # No `device_token`: the bits are unknown, and marking the
                # welcome would overwrite a bit0 this query could not see.
                return await self._welcome(subject)
            # Apple answered and refused: a malformed or forged token (400),
            # or credentials it will not accept (401). That is not an outage,
            # and treating it as one handed the welcome to anyone who sent
            # junk. No welcome, but the daily limit is still granted. A 401
            # means our key is wrong, which `/checkup` reports.
            log.warning("devicecheck refused the query — no welcome: %s", exc)
            return await self._refuse_welcome(subject)
        except Exception as exc:
            # Not Apple's availability. A key that cannot sign lands here,
            # and `/checkup` names it. So could a bug. Neither is a reason to
            # hand out the welcome.
            log.error("devicecheck query could not be made — no welcome: %s", exc)
            return await self._refuse_welcome(subject)

        if bits and (bits.get("bit0") or bits.get("bit1")):
            # The mark is only about the month it was written in. Apple returns
            # that month as `last_update_time`, and it was being discarded.
            stamp = str(bits.get("last_update_time") or "")[:7]
            if stamp != _utc_month():
                # Either an earlier month, or a shape we cannot read. Both are
                # "recency not established", and the branch above already
                # grants when Apple is simply unreachable — a hardening signal
                # must not outweigh a real user on a new phone. Clear it so
                # the next lookup is cheap and unambiguous.
                log.info("devicecheck mark is not from this month — granting",
                         extra={"stamp": stamp or "absent"})
                try:
                    await self._device_check.update_bits(
                        device_token, bit0=False, bit1=False)
                except Exception as exc:
                    log.warning("devicecheck reset failed: %s", exc)
                return await self._welcome(subject, device_token)

            if not bits.get("bit0"):
                # The welcome was taken on this hardware this month, and its
                # allowance not used up. Today's scans are not known to be
                # spent, so the daily limit, but not a second welcome.
                log.info("devicecheck: welcome already taken on this device "
                         "this month — daily limit only")
                return await self._refuse_welcome(subject)

            log.info("reinstall detected via devicecheck — no fresh free scans")
            try:
                await self._cache.set(
                    self._counter_key(subject), str(self._limit), _COUNTER_TTL, required=True)
            except CacheUnavailable:
                pass
            # Burn the welcome marker too. Without this the counter expires
            # with the day and the `seen` marker with it, so the very same
            # reinstall came back ~30h later, looked "new" again, and was
            # handed the FREE_SCANS_FIRST_DAY welcome the branch above just
            # refused. The marker outlives both, so the refusal sticks.
            try:
                await self._cache.add(
                    self._welcome_key(subject), _DENIED, _WELCOME_TTL, required=True)
            except CacheUnavailable:
                pass
            return 0
        return await self._welcome(subject, device_token)

    async def _welcome(self, subject: str, device_token: str | None = None) -> int:
        """Grant the first-day allowance to a genuinely new subject, once.

        The welcome marker outlives every counter, so the allowance is handed
        out exactly once per subject — including a subject first seen while the
        welcome was switched off, which is what the `_DENIED` write below is
        for.

        `device_token` is passed only when DeviceCheck has just said this
        hardware carries no mark this month. A grant then sets bit1, so the
        next install on it this month is not welcomed again.
        """
        first_day = await self._first_day_limit()
        if not first_day:
            # Record the refusal, do not just return. This path used to write
            # no marker at all, so a subject first seen while the welcome was
            # off carried nothing — and the moment the lever was armed, the
            # next recurrence of `first_time` reached the grant below, `add`
            # succeeded for an *existing* subject, and `_limit_for` handed them
            # the first-day allowance for the rest of that UTC day. An existing
            # user who had already spent today's scan got extra paid Gemini
            # scans, once for every subject in the install base.
            #
            # It is not specific to the ops lever: raising FREE_SCANS_FIRST_DAY
            # from 0 as a Railway variable does exactly the same thing, and
            # did. It also contaminated the very funnel the lever exists to
            # measure, because the "first-day cohort" filled with existing
            # users.
            #
            # The mirror-image case — a DeviceCheck-recognised reinstall
            # claiming the welcome — was closed by writing `_DENIED` on the
            # refusal path in `starting_balance`. This is the same fix for the
            # other way in.
            return await self._refuse_welcome(subject)
        try:
            granted = await self._cache.add(
                self._welcome_key(subject), _utc_day(), _WELCOME_TTL, required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc
        if granted:
            log.info("welcome allowance granted", extra={"scans": first_day})
            if device_token:
                self._mark_welcomed(device_token)
            return first_day
        return await self._limit_for(subject)

    def _mark_welcomed(self, device_token: str) -> None:
        """Set bit1 on this hardware, in the background.

        A round trip to Apple on the attestation path, which must not wait for
        it or fail because of it. bit0 is written False because the caller
        has just read it unset for this month, and Apple writes both bits.
        """
        device_check = self._device_check
        if device_check is None:
            return

        async def mark() -> None:
            try:
                await device_check.update_bits(device_token, bit0=False, bit1=True)
            except Exception as exc:
                log.warning("devicecheck welcome mark failed: %s", exc)

        task = asyncio.get_running_loop().create_task(mark())
        _background.add(task)
        task.add_done_callback(_background.discard)

    async def _refuse_welcome(self, subject: str) -> int:
        """The daily limit, with the refusal recorded so it sticks."""
        try:
            await self._cache.add(
                self._welcome_key(subject), _DENIED, _WELCOME_TTL, required=True)
        except CacheUnavailable:
            # Best-effort, like the reinstall refusal: failing a scan over a
            # marker would be worse than the allowance it guards. `add` is a
            # no-op when a marker already exists, so a genuinely new subject
            # arriving after the lever is armed still reaches the grant.
            pass
        return self._limit
