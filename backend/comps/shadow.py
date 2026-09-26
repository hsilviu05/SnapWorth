"""Shadow mode: run the comps engine beside every scan, and show no one (#39).

The engine was built and tested but called from nowhere. This connects it to
the scan path in the one way that cannot hurt a user: after the response is
built, as a background task the request never waits for. The result is logged
and counted, and nothing about it reaches the response — `valuation_source`
stays "model" whatever the engine finds.

Why a background task rather than running inside the request
-------------------------------------------------------------
The engine needs the model's identification, so it cannot start until the
model has answered, and anything awaited after that is added to the scan
wholesale. Even under the 800 ms fan-out budget that is up to 800 ms on every
scan, for a result the user is not shown. Detached, the scan's latency is
untouched by construction, which is the issue's acceptance criterion rather
than something to measure afterwards.

What is logged is the gate for #40 and #41: how often the model and the comps
agree, and where they do not. Per lookup — status, both ranges, comp count,
match confidence, per-provider latency — plus three metrics for dashboards.

With `COMPS_ENABLED=false`, which production must keep until a provider grants
sold-data access in writing, `schedule` returns before creating anything: off
means the engine is not called at all.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from decimal import Decimal
from typing import Protocol

import metrics
from comps import normalize
from comps.engine import CompsEngine
from comps.models import CompsResult, Condition, ItemIdentity

log = logging.getLogger("snapworth.comps.shadow")


class _Identified(Protocol):
    """The slice of `valuation.Valuation` an identity is built from.

    A protocol rather than an import: `comps` stays independent of the model
    layer, and a test can pass a plain object.
    """

    category: str
    brand: str
    model: str | None
    variant: str | None
    size: str | None
    material: str | None
    condition_grade: str | None


def identity_from_valuation(val: _Identified) -> ItemIdentity:
    """The model's identification, as the engine's query.

    `year` is left unset. The model's `era` is prose ("1990s", "Y2K"), and the
    year is part of the cache key — guessing one would fragment the cache and
    veto real matches for no precision gain.
    """
    return ItemIdentity(
        category=(val.category or "other").strip().lower(),
        brand=val.brand or None,
        model=val.model,
        variant=val.variant,
        size=val.size,
        material=val.material,
        condition=_condition(val.condition_grade),
    )


def _condition(grade: str | None) -> Condition:
    """`condition_grade` is already our ladder; the text fold is a fallback.

    Enum first, because `normalize.condition` is written for marketplace prose
    and reads the camelCase "likeNew" as containing "new" — which would weight
    a like-new item's comps as brand new.
    """
    try:
        return Condition(grade)
    except ValueError:
        return normalize.condition(grade, default=Condition.GOOD) or Condition.GOOD


def _money(value: Decimal | None) -> float | None:
    return None if value is None else round(float(value), 2)


def report(result: CompsResult, *, model_low: float, model_high: float,
           model_expected: float | None) -> dict:
    """Flat, log-friendly comparison of one lookup against the model.

    The comps range is the evidence's interquartile range: the middle half of
    what matching items sold for, which is the closest thing the comps have to
    the model's low/high pair.
    """
    fields: dict = {
        "status": result.status.value,
        "model_low": model_low,
        "model_high": model_high,
        "model_expected": model_expected,
        "comp_count": len(result.comps),
        "providers_queried": list(result.providers_queried),
        "providers_failed": list(result.providers_failed),
        "provider_latency_ms": {name: round(ms, 1)
                                for name, ms in result.provider_latency_ms},
        "latency_ms": round(result.latency_ms, 1),
        "cache_hit": result.cache_hit,
    }
    if result.comps:
        fields["match_confidence"] = round(
            statistics.median(c.match_score for c in result.comps), 3)

    evidence = result.evidence
    if result.has_evidence and evidence is not None:
        comps_expected = result.prices.expected if result.prices else evidence.median
        fields.update(
            comps_low=_money(evidence.p25),
            comps_high=_money(evidence.p75),
            comps_expected=_money(comps_expected),
            dispersion=round(evidence.dispersion, 3),
            ranges_overlap=(float(evidence.p25) <= model_high
                            and model_low <= float(evidence.p75)),
        )
        if model_expected:
            fields["price_ratio"] = round(float(comps_expected) / model_expected, 3)
    return fields


class ShadowRunner:
    """Schedules shadow lookups and keeps them alive until they finish.

    The task set is not bookkeeping: the event loop holds only a weak reference
    to a task, so an un-referenced background task can be collected mid-flight.
    """

    def __init__(self, engine: CompsEngine) -> None:
        self.engine = engine
        self._tasks: set[asyncio.Task] = set()

    @property
    def pending(self) -> int:
        return len(self._tasks)

    def schedule(self, val: _Identified, *, model_low: float, model_high: float,
                 model_expected: float | None) -> asyncio.Task | None:
        """Start a shadow lookup for this scan. Never raises, never blocks."""
        if not self.engine.flags.enabled:
            return None
        try:
            identity = identity_from_valuation(val)
            task = asyncio.get_running_loop().create_task(
                self._run(identity, model_low, model_high, model_expected))
        except Exception as exc:
            log.warning("comps shadow not scheduled: %s", exc)
            return None
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run(self, identity: ItemIdentity, model_low: float,
                   model_high: float, model_expected: float | None) -> None:
        try:
            result = await self.engine.lookup(identity)
            fields = report(result, model_low=model_low, model_high=model_high,
                            model_expected=model_expected)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The engine promises not to raise; this is the belt to its braces.
            # A shadow failure must never surface anywhere a user could see it.
            log.warning("comps shadow failed: %s", exc)
            metrics.comps_shadow_lookups.inc(status="error")
            return

        metrics.comps_shadow_lookups.inc(status=fields["status"])
        metrics.comps_shadow_duration.observe(result.latency_ms / 1000)
        if "price_ratio" in fields:
            metrics.comps_shadow_price_ratio.observe(fields["price_ratio"])
        log.info("comps shadow", extra={
            "category": identity.category, "brand": identity.brand, **fields})

    async def drain(self, timeout: float = 1.0) -> None:
        """At shutdown: let running lookups finish briefly, then cancel them.

        Bounded well inside the fan-out budget plus slack. A shadow result is
        a measurement; losing one to a deploy costs nothing.
        """
        if not self._tasks:
            return
        _, pending = await asyncio.wait(set(self._tasks), timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
