"""Server-side free-scan quota.

Closes the second half of SEC-02. The counter previously lived in the app's
`AppStorage`, so it was advisory: deleting and reinstalling reset it, and a
jailbroken device could edit it outright.

Here it is authoritative. Three properties matter:

* **Atomic.** The increment and the limit check are one operation, so
  concurrent requests can't both observe "2 used" and both proceed.
* **Fails closed.** If the durable cache is unreachable the request is refused
  rather than granted. A quota that fails open is not a quota.
* **Reinstall-resistant.** A brand-new subject is cross-checked against the
  device's DeviceCheck bit before being handed a fresh allowance.

Consumption happens *after* a scan succeeds. Charging for a failed scan is both
unfair and a support burden.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from cache import CacheUnavailable

log = logging.getLogger("snapworth.quota")

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
# permanent widening of the free tier. Granted once per subject, ever; a
# reinstall that DeviceCheck recognises gets nothing, exactly as today.
FREE_SCANS_FIRST_DAY = 0

# The welcome grant outlives any counter, so a subject can never be welcomed
# twice. Matches the attestation-state horizon.
_WELCOME_TTL = 60 * 60 * 24 * 400


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

    @property
    def remaining(self) -> int:
        return 2**31 if self.unlimited else max(0, self.limit - self.used)


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _exhausted_message(limit: int) -> str:
    """User-facing copy for a spent allowance.

    The message is echoed to the user verbatim by the client, so it has to read
    correctly at every limit. The old f-string hardcoded the plural and, once
    the free tier moved to one scan a day, told everybody "You've used all 1
    free scans today."
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
                 first_day_limit: int = FREE_SCANS_FIRST_DAY) -> None:
        self._cache = cache
        self._device_check = device_check
        self._limit = limit
        # A first-day limit no larger than the daily one is not a welcome.
        self._first_day = first_day_limit if first_day_limit > limit else 0

    @staticmethod
    def _counter_key(subject: str) -> str:
        return f"quota:{subject}:{_utc_day()}"

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
        if not self._first_day:
            return self._limit
        try:
            granted = await self._cache.get(self._welcome_key(subject), required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc
        return self._first_day if granted == _utc_day() else self._limit

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
        """
        if not device_token or self._device_check is None:
            return
        if not self._device_check.is_configured:
            return
        try:
            await self._device_check.update_bits(device_token, bit0=True, bit1=False)
        except Exception as exc:
            log.warning("devicecheck update failed: %s", exc)

    async def starting_balance(self, subject: str, device_token: str | None) -> int:
        """Free scans a *newly seen* subject should start with.

        A fresh App Attest key id normally means a new install. If DeviceCheck
        says this hardware already burned its allowance, the reinstall gets
        nothing back until the next reset.
        """
        try:
            first_time = await self._cache.add(
                self._seen_key(subject), "1", _COUNTER_TTL, required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc

        if not first_time:
            return await self._limit_for(subject)   # already known, normal path

        if not device_token or self._device_check is None:
            return await self._welcome(subject)
        if not self._device_check.is_configured:
            return await self._welcome(subject)

        try:
            bits = await self._device_check.query_bits(device_token)
        except Exception as exc:
            # Availability of Apple's API must not gate our own service.
            log.warning("devicecheck query failed, granting default: %s", exc)
            return await self._welcome(subject)

        if bits and bits.get("bit0"):
            log.info("reinstall detected via devicecheck — no fresh free scans")
            try:
                await self._cache.set(
                    self._counter_key(subject), str(self._limit), _COUNTER_TTL, required=True)
            except CacheUnavailable:
                pass
            return 0
        return await self._welcome(subject)

    async def _welcome(self, subject: str) -> int:
        """Grant the first-day allowance to a genuinely new subject, once.

        The `seen` marker above expires with the counters, so "first time"
        recurs for anyone who stays away for a day; the welcome marker does
        not, so the allowance is handed out exactly once per subject.
        """
        if not self._first_day:
            return self._limit
        try:
            granted = await self._cache.add(
                self._welcome_key(subject), _utc_day(), _WELCOME_TTL, required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc
        if granted:
            log.info("welcome allowance granted", extra={"scans": self._first_day})
            return self._first_day
        return await self._limit_for(subject)
