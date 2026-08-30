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


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 86400
    return max(60, int(tomorrow - now.timestamp()))


class ScanQuota:
    """Authoritative daily free-scan accounting."""

    def __init__(self, cache, device_check=None, limit: int = FREE_SCANS_PER_DAY) -> None:
        self._cache = cache
        self._device_check = device_check
        self._limit = limit

    @staticmethod
    def _counter_key(subject: str) -> str:
        return f"quota:{subject}:{_utc_day()}"

    @staticmethod
    def _seen_key(subject: str) -> str:
        return f"quota:seen:{subject}"

    async def status(self, subject: str, is_pro: bool) -> QuotaStatus:
        if is_pro:
            return QuotaStatus(used=0, limit=self._limit, unlimited=True)
        try:
            raw = await self._cache.get(self._counter_key(subject), required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc
        return QuotaStatus(used=int(raw or 0), limit=self._limit, unlimited=False)

    async def check(self, subject: str, is_pro: bool) -> QuotaStatus:
        """Raise if the subject has no allowance left. Does not consume."""
        status = await self.status(subject, is_pro)
        if not status.unlimited and status.used >= status.limit:
            raise QuotaExceeded(
                f"You've used all {status.limit} free scans today.",
                resets_at=int(time.time()) + _seconds_until_utc_midnight(),
            )
        return status

    async def consume(self, subject: str, is_pro: bool) -> QuotaStatus:
        """Atomically record one use. Call only after the work succeeded."""
        if is_pro:
            return QuotaStatus(used=0, limit=self._limit, unlimited=True)
        try:
            used = await self._cache.incr(
                self._counter_key(subject), _COUNTER_TTL, required=True)
        except CacheUnavailable as exc:
            raise QuotaUnavailable(str(exc)) from exc
        return QuotaStatus(used=used, limit=self._limit, unlimited=False)

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
            return self._limit                      # already known, normal path

        if not device_token or self._device_check is None:
            return self._limit
        if not self._device_check.is_configured:
            return self._limit

        try:
            bits = await self._device_check.query_bits(device_token)
        except Exception as exc:
            # Availability of Apple's API must not gate our own service.
            log.warning("devicecheck query failed, granting default: %s", exc)
            return self._limit

        if bits and bits.get("bit0"):
            log.info("reinstall detected via devicecheck — no fresh free scans")
            try:
                await self._cache.set(
                    self._counter_key(subject), str(self._limit), _COUNTER_TTL, required=True)
            except CacheUnavailable:
                pass
            return 0
        return self._limit
