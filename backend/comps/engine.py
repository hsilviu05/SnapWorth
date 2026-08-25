"""The comps engine: orchestrates the full retrieval pipeline.

    identity → cache → provider fan-out → rank → dedupe → aggregate → evidence

Everything here is about *bounded degradation*. A scan is a user standing in a
shop waiting for a number, so every failure mode in this pipeline resolves to
"return what we have" rather than "raise". The engine never raises; a total
failure is a `CompsResult` with a status explaining why.

The fan-out budget
------------------
Providers are queried in parallel with a single wall-clock deadline. Whatever
has returned when the budget expires is used, and the stragglers are cancelled.
This is `asyncio.wait(..., timeout=)` rather than `gather`, because `gather`
would make the slowest provider set the latency for everyone — which is exactly
how an optional enhancement turns into a visible regression.

Ordering of the stages is deliberate
------------------------------------
Rank *before* dedupe: dedupe needs match scores to decide which copy of a
duplicate to keep. Dedupe *before* aggregate: relists cluster at the price that
failed to sell, so aggregating first would bias the median upward.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from comps import aggregate as aggregate_module
from comps import dedupe as dedupe_module
from comps import matching
from comps.cache import CompsCache, CompsCacheLike, NullCompsCache
from comps.flags import CompsFlags
from comps.flags import flags as default_flags
from comps.models import (
    Comp,
    CompsResult,
    CompsStatus,
    ItemIdentity,
    ProviderQuery,
)
from comps.providers.base import ProviderRegistry, RegisteredProvider
from comps.providers.base import registry as default_registry

log = logging.getLogger("snapworth.comps.engine")


@dataclass
class CompsEngine:
    """Stateless orchestrator. Safe to share across requests."""

    registry: ProviderRegistry = default_registry
    # default_factory rather than `None` + __post_init__: the field is never
    # None once construction finishes, so typing it Optional only forced a
    # narrowing check at every call site for a state that cannot occur.
    cache: CompsCacheLike = field(default_factory=NullCompsCache)
    flags: CompsFlags = default_flags

    # ── Public API ───────────────────────────────────────────────────────────

    async def lookup(self, identity: ItemIdentity) -> CompsResult:
        """Find comparable sales for `identity`. Never raises."""
        started = time.monotonic()

        def finish(result: CompsResult) -> CompsResult:
            from dataclasses import replace
            return replace(result, latency_ms=(time.monotonic() - started) * 1000)

        if not self.flags.enabled:
            return finish(CompsResult(status=CompsStatus.DISABLED, identity=identity))

        if not self.flags.permits_category(identity.category):
            return finish(CompsResult(
                status=CompsStatus.DISABLED, identity=identity,
                notes=("category not enabled for comps",)))

        if not identity.is_searchable:
            # A brand alone returns thousands of unrelated comps whose median is
            # meaningless. Refusing to query is the correct answer, not a bug.
            return finish(CompsResult(
                status=CompsStatus.NOT_SEARCHABLE, identity=identity,
                notes=("identity lacks a model, variant or serial to search on",)))

        window = self.flags.window_for(identity.category)
        query = ProviderQuery(
            identity=identity,
            window_days=window,
            limit=self.flags.max_results_per_provider,
            sold_only=True,
        )

        try:
            cached = await self.cache.get(identity, window)
        except Exception as exc:
            log.warning("comps cache lookup failed: %s", exc)
            cached = None

        if cached is not None:
            return finish(self._build(identity, list(cached), window,
                                      queried=(), failed=(), cache_hit=True))

        eligible = [
            entry for entry in self.registry.eligible(query)
            if self.flags.permits_provider(entry.provider.name)
        ]
        if not eligible:
            return finish(CompsResult(
                status=CompsStatus.NO_PROVIDERS, identity=identity,
                window_days=window,
                notes=("no provider is configured for this category",)))

        raw, queried, failed = await self._fan_out(eligible, query)

        # Cache raw provider output — including the empty case, which is what
        # stops unidentifiable items re-querying every provider forever.
        try:
            await self.cache.put(identity, window, raw)
        except Exception as exc:
            log.warning("comps cache write failed: %s", exc)

        return finish(self._build(identity, raw, window,
                                  queried=queried, failed=failed, cache_hit=False))

    async def health(self):
        return await self.registry.health()

    # ── Internals ────────────────────────────────────────────────────────────

    async def _fan_out(
        self, eligible: list[RegisteredProvider], query: ProviderQuery
    ) -> tuple[list[Comp], tuple[str, ...], tuple[str, ...]]:
        """Query providers in parallel under one wall-clock budget."""
        budget = self.flags.fanout_budget_ms / 1000
        per_provider = self.flags.provider_timeout_ms / 1000

        async def call(entry: RegisteredProvider) -> tuple[str, list[Comp] | None]:
            name = entry.provider.name
            try:
                comps = await asyncio.wait_for(
                    entry.provider.search(query), timeout=per_provider)
                entry.breaker.record_success()
                return name, list(comps or [])
            except asyncio.TimeoutError:
                entry.breaker.record_failure()
                log.info("comps provider timed out", extra={"provider": name})
                return name, None
            except Exception as exc:
                entry.breaker.record_failure()
                log.warning("comps provider failed", extra={
                    "provider": name, "error": str(exc)[:200]})
                return name, None

        tasks = {asyncio.create_task(call(entry)): entry for entry in eligible}
        done, pending = await asyncio.wait(tasks, timeout=budget)

        for task in pending:
            # Over budget. Cancelling is the point: a slow provider must not set
            # the latency for the whole scan.
            task.cancel()
            entry = tasks[task]
            entry.breaker.record_failure()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        collected: list[Comp] = []
        queried: list[str] = []
        failed: list[str] = [tasks[t].provider.name for t in pending]

        for task in done:
            try:
                name, comps = task.result()
            except Exception:
                continue
            queried.append(name)
            if comps is None:
                failed.append(name)
            else:
                collected.extend(comps)

        return collected, tuple(sorted(queried)), tuple(sorted(set(failed)))

    def _build(
        self,
        identity: ItemIdentity,
        raw: list[Comp],
        window: int,
        *,
        queried: tuple[str, ...],
        failed: tuple[str, ...],
        cache_hit: bool,
    ) -> CompsResult:
        """Rank → dedupe → aggregate. Pure and deterministic given `raw`."""
        notes: list[str] = []

        if not raw:
            return CompsResult(
                status=CompsStatus.INSUFFICIENT_COMPS, identity=identity,
                providers_queried=queried, providers_failed=failed,
                cache_hit=cache_hit, window_days=window,
                notes=("no comparable sales found",))

        ranked = matching.rank(identity, raw)
        if len(ranked) < len(raw):
            notes.append(
                f"{len(raw) - len(ranked)} of {len(raw)} listings rejected as "
                f"different products")

        report = dedupe_module.deduplicate(ranked)
        if report.removed:
            notes.append(f"{report.removed} duplicate listings merged")

        surviving = list(report.kept)
        if len(surviving) < aggregate_module.MIN_COMPS:
            return CompsResult(
                status=CompsStatus.INSUFFICIENT_COMPS, identity=identity,
                comps=tuple(surviving), providers_queried=queried,
                providers_failed=failed, cache_hit=cache_hit, window_days=window,
                notes=tuple(notes + [
                    f"only {len(surviving)} matching sales found; "
                    f"{aggregate_module.MIN_COMPS} needed to price from evidence"]))

        evidence = aggregate_module.aggregate(
            surviving, target_condition=identity.condition)
        if evidence is None:
            return CompsResult(
                status=CompsStatus.INSUFFICIENT_COMPS, identity=identity,
                comps=tuple(surviving), providers_queried=queried,
                providers_failed=failed, cache_hit=cache_hit, window_days=window,
                notes=tuple(notes + ["comparable sales were not usable"]))

        if evidence.outliers_removed:
            notes.append(f"{evidence.outliers_removed} outlier sales excluded")

        prices = aggregate_module.to_prices(evidence)
        return CompsResult(
            status=CompsStatus.OK, identity=identity, comps=tuple(surviving),
            evidence=evidence, prices=prices, providers_queried=queried,
            providers_failed=failed, cache_hit=cache_hit, window_days=window,
            notes=tuple(notes))


def build_engine(cache_backend=None, *, flags: CompsFlags | None = None) -> CompsEngine:
    """Construct an engine wired to the shared cache and provider registry."""
    from comps.flags import flags as current_flags

    resolved = flags or current_flags
    cache = (
        CompsCache(
            backend=cache_backend,
            ttl_seconds=resolved.cache_ttl_seconds,
            negative_ttl_seconds=resolved.negative_cache_ttl_seconds,
        )
        if cache_backend is not None
        else NullCompsCache()
    )
    return CompsEngine(registry=default_registry, cache=cache, flags=resolved)
